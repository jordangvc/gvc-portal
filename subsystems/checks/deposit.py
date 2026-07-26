"""
Paid-by-Check — pure parsing + matching core.
=========================================================================
The two pieces that must be *correct* and are fully unit-testable without
touching Google Vision, Stripe, Monday, or Drive:

  - parse_check_ocr(text)  — turn raw OCR text into best-guess fields. Tuned
                             against a real GVC customer check (Danis Builders).
                             Every field is a guess the human confirms/edits in
                             the modal; OCR is a head start, not the truth.
  - match_invoice(check, open_rows) — pick the one open invoice a check pays,
                             grounded in the "Invoices Sent" board schema.

The live path (Vision call lives in vision.py; Stripe paid_out_of_band, Monday
Status→Paid, Drive upload) is a 3-stage gate that mutates money state. Per the original design:
the already-deposited check is caught AFTER the confirm modal, when we pull the
matched invoice's status from Stripe. See docs/portal-check-deposit-design.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

# A money token like 1,234.56 / 110,610.00 / 750.00 (optionally $-prefixed).
_MONEY_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})\b")
# Numeric dates: 6/15/2026, 06-02-26, 2026-06-15.
_NUM_DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
# Textual dates: Jun 2, 2026 / June 2 2026.
_MONTH_DATE_RE = re.compile(
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
    re.I,
)
# Check-number label (require >=4 digits so the routing fraction "73-27/421"
# can't masquerade as the check number).
_CHECK_NO_LABEL_RE = re.compile(
    r"(?:check\s*(?:no|number|#)|#|(?<!form )\bno\.)\s*[:.]?\s*(\d{4,10})", re.I
)
# MICR E-13B symbols Vision emits around the bottom number line.
_MICR_SYMBOLS = "⑆⑇⑈⑉"
# Company-ish suffixes used to find the payer (remitter) line.
_COMPANY_RE = re.compile(
    r"\b(L\.?L\.?C\.?|INC\.?|INCORPORATED|LTD\.?|CO\.?|CORP\.?|COMPANY|BUILDERS?|"
    r"CONSTRUCTION|CONTRACTORS?|HOMES?|DEVELOPMENT|GROUP|PARTNERS|ENTERPRISES?)\b",
    re.I,
)
# Lines that are never the payer (security strip / labels / bank / boilerplate).
_BOILERPLATE = (
    "WATERMARK", "HOLD AT AN ANGLE", "DO NOT CASH", "VOID IN", "PAYMENT REFERENCE",
    "CHECK NO", "CHECK NUMBER", "DATE OF CHECK", "CHECK AMOUNT", "AUTHORIZED SIGNATURE",
    "MEMO", "PAY:", "PAY ", "TO THE ORDER OF", "FORM NO",
)


def to_cents(value) -> Optional[int]:
    """Coerce a money-ish value to integer cents, or None if unparseable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100
    try:
        d = Decimal(str(value).replace(",", "").replace("$", "").strip())
        return int((d * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def find_money(text: str) -> list[Decimal]:
    """Money amounts in document order, deduped preserving order."""
    out: list[Decimal] = []
    for whole, cents in _MONEY_RE.findall(text or ""):
        try:
            amt = Decimal(f"{whole.replace(',', '')}.{cents}")
        except InvalidOperation:
            continue
        if amt not in out:
            out.append(amt)
    return out


def find_date(text: str) -> Optional[str]:
    m = _MONTH_DATE_RE.search(text or "")
    if m:
        return m.group(1)
    m = _NUM_DATE_RE.search(text or "")
    return m.group(1) if m else None


def parse_micr(lines: list[str]) -> dict:
    """
    Parse the MICR line(s) at the bottom of a check. Vision often splits the MICR
    band across two lines and emits E-13B glyphs (⑆⑈), so we gather digit groups
    from every glyph-bearing line in document order. The routing number is the
    only 9-digit group and anchors the rest: groups before it → check number,
    groups after → account. Falls back to a single-line scan if no glyphs were
    detected. Returns {} if no MICR found.
    """
    groups: list[str] = []
    for ln in lines:
        if any(sym in ln for sym in _MICR_SYMBOLS):
            groups.extend(re.findall(r"\d{3,}", ln))
    if not groups:  # no glyphs — try a single all-in-one MICR line
        for ln in lines:
            g = re.findall(r"\d{3,}", ln)
            if any(len(x) == 9 for x in g) and len(g) >= 2:
                groups = g
                break
    nine = [g for g in groups if len(g) == 9]
    if not nine:
        return {}
    routing = nine[0]
    idx = groups.index(routing)
    before, after = groups[:idx], groups[idx + 1:]
    check_no = before[-1] if before else (after[0] if after else None)
    account = after[0] if after else (before[-1] if before else None)
    return {"check_no": check_no, "routing": routing, "account": account}


def _find_amount(lines: list[str], money: list[Decimal]) -> Optional[Decimal]:
    """Prefer the value next to a 'CHECK AMOUNT' label; else the largest token."""
    for i, ln in enumerate(lines):
        if "CHECK AMOUNT" in ln.upper():
            for cand in [ln] + lines[i + 1:i + 3]:
                m = find_money(cand)
                if m:
                    return m[0]
    return max(money) if money else None


def _find_payee(lines: list[str]) -> Optional[str]:
    for i, ln in enumerate(lines):
        if "TO THE ORDER OF" in ln.upper():
            after = ln.split(":", 1)[1].strip() if ":" in ln else ""
            if after:
                return after
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return None


def _find_payer(lines: list[str], payee: Optional[str]) -> Optional[str]:
    """First company-suffix line that isn't boilerplate, the bank, or the payee."""
    payee_u = (payee or "").upper()
    for ln in lines:
        u = ln.strip()
        if not u or any(b in u.upper() for b in _BOILERPLATE):
            continue
        if "BANK" in u.upper():
            continue
        if not _COMPANY_RE.search(u):
            continue
        if "GREEN VALLEY" in u.upper():        # that's us (the payee)
            continue
        if payee_u and u.upper() in payee_u:
            continue
        return re.sub(r"\s*-\s*\d+\s*$", "", u).strip()  # drop trailing " - 34"
    return None


def _find_reference(lines: list[str]) -> Optional[str]:
    for i, ln in enumerate(lines):
        if "PAYMENT REFERENCE" in ln.upper():
            after = ln.upper().split("REFERENCE", 1)[1]
            after = ln[len(ln) - len(after):].strip(" :")
            if after:
                return after
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return None


def find_check_number(text: str) -> Optional[str]:
    m = _CHECK_NO_LABEL_RE.search(text or "")
    return m.group(1) if m else None


_PAY_TO_RE = re.compile(r"pay\s*to\s*the\s*order\s*of", re.I)


def count_checks(text: str) -> int:
    """
    How many checks are in this image. Every check has exactly one
    'PAY TO THE ORDER OF', and that survives same-bank cases where routing
    numbers repeat (two checks from one bank). Remittance stubs don't carry the
    phrase, so they don't inflate the count. Used to fail safe on multi-check
    images rather than silently mash two checks' fields together.
    """
    n = len(_PAY_TO_RE.findall(text or ""))
    if n:
        return n
    return 1 if (text or "").strip() else 0


def _find_remitter(lines: list[str]) -> Optional[str]:
    """Cashier's/official checks label the payer 'REMITTER' (often an individual,
    no company suffix), so the company-suffix payer heuristic misses them."""
    for i, ln in enumerate(lines):
        if "REMITTER" in ln.upper():
            after = re.sub(r"(?i)remitter", "", ln).strip(" :")
            if after:
                return after
            for nxt in lines[i + 1:i + 3]:
                if nxt.strip() and not any(b in nxt.upper() for b in _BOILERPLATE):
                    return nxt.strip()
    return None


@dataclass
class ParsedCheck:
    """Best-guess fields from OCR. Every field is editable in the confirm modal."""
    payer: Optional[str] = None
    payee: Optional[str] = None
    amount: Optional[Decimal] = None
    amount_candidates: list[Decimal] = field(default_factory=list)
    check_no: Optional[str] = None
    date: Optional[str] = None
    routing: Optional[str] = None
    account: Optional[str] = None
    reference: Optional[str] = None
    memo: Optional[str] = None
    raw_text: str = ""

    def as_dict(self) -> dict:
        return {
            "payer": self.payer,
            "payee": self.payee,
            "amount": str(self.amount) if self.amount is not None else None,
            "amount_candidates": [str(a) for a in self.amount_candidates],
            "check_no": self.check_no,
            "date": self.date,
            "routing": self.routing,
            "account": self.account,
            "reference": self.reference,
            "memo": self.memo,
        }


def parse_check_ocr(text: str) -> ParsedCheck:
    """Heuristic parse over Vision's full text. Conservative; confirm in the modal."""
    text = text or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    money = find_money(text)
    micr = parse_micr(lines)
    payee = _find_payee(lines)
    return ParsedCheck(
        payer=_find_payer(lines, payee) or _find_remitter(lines),
        payee=payee,
        amount=_find_amount(lines, money),
        amount_candidates=money,
        check_no=micr.get("check_no") or find_check_number(text),
        date=find_date(text),
        routing=micr.get("routing"),
        account=micr.get("account"),
        reference=_find_reference(lines),
        memo=None,                 # memo is positional; left to the human in v0
        raw_text=text,
    )


# ---------------------------------------------------------------------------
# Matching — one check pays one open invoice in full
# ---------------------------------------------------------------------------

# Closed = Paid or Void; everything else (e.g. "Invoice Sent", "Overdue", blank)
# is open for matching. Mirrors monday.CLOSED_INVOICE_STATUSES.
_CLOSED_STATUSES = {"paid", "void"}


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def _payer_matches_row(payer: str, row: dict) -> bool:
    """Loose payer ↔ customer/job overlap: any shared word of length >= 4."""
    p = set(w for w in _norm(payer).split() if len(w) >= 4)
    if not p:
        return False
    hay = set(_norm(f"{row.get('customer','')} {row.get('job','')}").split())
    return bool(p & hay)


def open_rows_only(rows: list[dict]) -> list[dict]:
    return [r for r in rows if (r.get("status") or "").strip().lower() not in _CLOSED_STATUSES]


@dataclass
class MatchResult:
    match: Optional[dict] = None
    candidates: list[dict] = field(default_factory=list)
    confidence: str = "none"          # high | medium | none
    reason: str = ""


def match_invoice(check: dict, rows: list[dict]) -> MatchResult:
    """
    Find the single open invoice a check pays. Signals, strongest first:
      1. invoice identifier appears in the memo or payment reference -> high
      2. exact amount (to the cent), 1 candidate                      -> medium
      3. exact amount, many candidates, payer narrows to 1            -> medium
      4. otherwise -> no auto-match; return candidates for manual pick.
    Never auto-matches an ambiguous check.
    """
    open_rows = open_rows_only(rows)

    # 1) memo / payment reference carries the invoice number
    ref = _norm(f"{check.get('memo','')} {check.get('reference','')}")
    if ref.strip():
        hits = [r for r in open_rows if _norm(r.get("identifier")) and _norm(r["identifier"]) in ref]
        if len(hits) == 1:
            return MatchResult(match=hits[0], confidence="high",
                               reason="Invoice number found in the check memo/reference.")
        if len(hits) > 1:
            return MatchResult(candidates=hits, confidence="none",
                               reason="Multiple invoices match the memo/reference — pick one.")

    # 2/3) exact amount
    cents = to_cents(check.get("amount"))
    if cents is None:
        return MatchResult(confidence="none", reason="No usable check amount.")
    amount_hits = [r for r in open_rows if effective_amount_cents(r) == cents]
    if len(amount_hits) == 1:
        return MatchResult(match=amount_hits[0], confidence="medium",
                           reason="Exactly one open invoice matches the amount.")
    if len(amount_hits) > 1:
        payer = check.get("payer") or ""
        narrowed = [r for r in amount_hits if _payer_matches_row(payer, r)]
        if len(narrowed) == 1:
            return MatchResult(match=narrowed[0], confidence="medium",
                               reason="Amount + payer name pinned one invoice.")
        return MatchResult(candidates=amount_hits, confidence="none",
                           reason="Several open invoices share this amount — pick one.")

    return MatchResult(confidence="none",
                       reason="No open invoice matches this amount — search manually.")


# ---------------------------------------------------------------------------
# Multi-invoice matching — one check pays SEVERAL open invoices, each IN FULL
# (Stripe's paid_out_of_band is all-or-nothing per invoice; partials are out of
# scope by design — decided 2026-07-03). Signals, strongest first:
#   1. invoice identifiers listed on the stub (memo/reference) — ALL hits match.
#   2. the single-invoice rules (exact amount, payer narrowing).
#   3. amount COMBINATION: exactly one subset of the payer's open invoices sums
#      to the check amount → suggest that set. Fail-safe: any ambiguity (two
#      possible subsets, or too many rows to search) suggests nothing.
# ---------------------------------------------------------------------------


@dataclass
class MultiMatchResult:
    matches: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    confidence: str = "none"          # high | medium | none
    reason: str = ""


def find_amount_combination(amount_cents: Optional[int], rows: list[dict], *,
                            max_rows: int = 20, max_size: int = 6) -> Optional[list[dict]]:
    """
    Find the subset of `rows` whose invoice amounts sum EXACTLY to amount_cents.
    Returns the subset only if it is UNIQUE; returns None when no subset sums,
    when more than one subset sums (ambiguous), or when the search space is too
    large (> max_rows priced rows) — never guesses. Subset size capped at
    max_size (stubs realistically list a handful of invoices).
    """
    if not amount_cents or amount_cents <= 0:
        return None
    priced = [(effective_amount_cents(r), r) for r in rows]
    priced = [(c, r) for c, r in priced if c and 0 < c <= amount_cents]
    if not priced or len(priced) > max_rows:
        return None
    found: list[list[dict]] = []

    def walk(i: int, remaining: int, picked: list[dict]) -> None:
        if len(found) > 1:            # already ambiguous — stop searching
            return
        if remaining == 0 and picked:
            found.append(list(picked))
            return
        if i >= len(priced) or remaining <= 0 or len(picked) >= max_size:
            return
        cents, row = priced[i]
        if cents <= remaining:        # branch 1: take row i
            picked.append(row)
            walk(i + 1, remaining - cents, picked)
            picked.pop()
        walk(i + 1, remaining, picked)  # branch 2: skip row i

    walk(0, amount_cents, [])
    return found[0] if len(found) == 1 else None


def match_invoices(check: dict, rows: list[dict]) -> MultiMatchResult:
    """
    Multi-invoice matcher. A stub that lists several invoice numbers matches
    them ALL (high). Otherwise fall back to the single-invoice rules, then to
    the unique amount-combination suggestion (both medium). Never auto-matches
    an ambiguous check; the human confirms/edits the selection in the modal.
    """
    open_rows = open_rows_only(rows)

    # 1) every invoice identifier found in the memo / payment reference
    ref = _norm(f"{check.get('memo','')} {check.get('reference','')}")
    if ref.strip():
        hits = [r for r in open_rows
                if _norm(r.get("identifier")).strip() and _norm(r["identifier"]) in ref]
        if len(hits) == 1:
            return MultiMatchResult(matches=hits, confidence="high",
                                    reason="Invoice number found in the check memo/reference.")
        if len(hits) > 1:
            return MultiMatchResult(matches=hits, confidence="high",
                                    reason=f"{len(hits)} invoice numbers found in the check "
                                           "memo/reference — this check covers all of them.")

    # 2) single-invoice rules (exact amount, payer narrowing)
    single = match_invoice(check, rows)
    if single.match:
        return MultiMatchResult(matches=[single.match], confidence=single.confidence,
                                reason=single.reason)

    # 3) unique amount combination (2+ invoices summing to the check amount).
    #    Prefer the payer's own open invoices; only search the whole board when
    #    the payer doesn't narrow (the max_rows cap keeps that fail-safe).
    cents = to_cents(check.get("amount"))
    payer = check.get("payer") or ""
    pool = [r for r in open_rows if _payer_matches_row(payer, r)] if payer.strip() else []
    combo = find_amount_combination(cents, pool or open_rows)
    if combo and len(combo) >= 2:
        return MultiMatchResult(matches=combo, confidence="medium",
                                reason=f"{len(combo)} open invoices sum exactly to the check "
                                       "amount — looks like one check paying all of them.")

    return MultiMatchResult(candidates=single.candidates, confidence="none",
                            reason=single.reason)


def effective_amount_cents(row: dict) -> Optional[int]:
    """
    The amount a check should be matched/settled against for this row: the
    REMAINING balance (Balance Due column, set by a prior partial payment)
    when present and positive, else the original invoice Amount. Pure.
    """
    bal = to_cents(row.get("balance_due"))
    if bal is not None and bal > 0:
        return bal
    return to_cents(row.get("amount"))


def sum_check(amount, rows: list[dict]) -> dict:
    """
    Compare the (human-confirmed) check amount against the sum of the selected
    invoices' REMAINING balances. Pure; the commit gate uses it server-side and
    the UI mirrors it. Returns {check_cents, invoices_cents, delta_cents,
    matched}. check_cents is None when the amount is unparseable (then matched
    is None too — no opinion).
    """
    check_cents = to_cents(amount)
    invoices_cents = 0
    for r in rows:
        c = effective_amount_cents(r)
        if c:
            invoices_cents += c
    if check_cents is None:
        return {"check_cents": None, "invoices_cents": invoices_cents,
                "delta_cents": None, "matched": None}
    return {"check_cents": check_cents, "invoices_cents": invoices_cents,
            "delta_cents": check_cents - invoices_cents,
            "matched": check_cents == invoices_cents}


# ---------------------------------------------------------------------------
# Commit / deposit helpers (pure — unit-tested). The live writes (Stripe pay,
# Monday Status->Paid, Drive file) live in service.py; these decide WHAT to do.
# ---------------------------------------------------------------------------

ALREADY_DEPOSITED_MESSAGE = (
    "This is a check that has already been deposited. The invoice tied to this "
    "check is marked as paid. Please confirm and return to run a new check."
)

_DRIVE_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")


def drive_folder_id(url: Optional[str]) -> Optional[str]:
    """Pull the Drive folder ID out of a .../folders/<id> URL. None if absent."""
    if not url:
        return None
    m = _DRIVE_FOLDER_RE.search(url)
    return m.group(1) if m else None


def deposit_plan(stripe_paid: bool, monday_paid: bool) -> dict:
    """
    Decide what the commit step should do, given the matched invoice's current
    paid state in Stripe and Monday. Pure + idempotent so a partial-failure
    retry runs only the steps still missing.

      - both already paid -> already_deposited (NO writes), exact UI message.
      - otherwise          -> run only the steps not yet done.
    """
    if stripe_paid and monday_paid:
        return {"already_deposited": True, "do_stripe": False, "do_monday": False,
                "message": ALREADY_DEPOSITED_MESSAGE}
    return {"already_deposited": False,
            "do_stripe": not stripe_paid,
            "do_monday": not monday_paid,
            "message": None}


ALREADY_DEPOSITED_MULTI_MESSAGE = (
    "This is a check that has already been deposited. Every invoice tied to "
    "this check is marked as paid. Please confirm and return to run a new check."
)


def multi_deposit_plan(states: list[dict]) -> dict:
    """
    Per-invoice deposit plan for a multi-invoice check. `states` is a list of
    {stripe_paid, monday_paid} (order matches the selected invoices). Pure +
    idempotent: a partial-failure retry re-plans and only the invoices/steps
    still missing run again; invoices recorded on a previous attempt come back
    already_deposited and are skipped, never re-written.

      - EVERY invoice already paid in both systems -> already_deposited overall
        (NO writes), with the exact UI message.
      - otherwise -> plans[i] says what invoice i still needs.
    """
    plans = [deposit_plan(stripe_paid=bool(s.get("stripe_paid")),
                          monday_paid=bool(s.get("monday_paid"))) for s in states]
    all_done = bool(plans) and all(p["already_deposited"] for p in plans)
    message = None
    if all_done:
        message = ALREADY_DEPOSITED_MESSAGE if len(plans) == 1 else ALREADY_DEPOSITED_MULTI_MESSAGE
    return {"already_deposited": all_done, "plans": plans, "message": message}


# ---------------------------------------------------------------------------
# Partial payments (pure — unit-tested). A check may cover LESS than the
# selected invoices' balances; the office splits the check across invoices via
# per-invoice allocations. An allocation equal to the invoice's remaining
# balance settles it in full; anything smaller is a partial payment.
# ---------------------------------------------------------------------------

def suggest_allocations(check_amount, rows: list[dict]) -> dict:
    """
    Oldest-first fill: walk `rows` in order, giving each invoice up to its
    remaining balance until the check runs out. Pure — the UI prefills from
    this and the user can edit. Returns {allocations: [{monday_item_id,
    identifier, alloc_cents, balance_cents, partial}], unallocated_cents}.
    `unallocated_cents` > 0 means the check exceeds every selected balance
    (an over-payment — the caller decides; we never auto-assign it).
    """
    left = to_cents(check_amount) or 0
    out: list[dict] = []
    for r in rows:
        bal = effective_amount_cents(r) or 0
        take = min(bal, max(left, 0))
        out.append({
            "monday_item_id": r.get("monday_item_id"),
            "identifier": r.get("identifier"),
            "alloc_cents": take,
            "balance_cents": bal,
            "partial": 0 < take < bal,
        })
        left -= take
    return {"allocations": out, "unallocated_cents": max(left, 0)}


def validate_allocations(check_amount, allocations: dict, balances: dict) -> dict:
    """
    Server-side allocation gate. `allocations` maps monday_item_id ->
    alloc_cents (user-confirmed); `balances` maps monday_item_id -> the
    AUTHORITATIVE remaining cents (Stripe amount_remaining re-fetched at
    commit — never the client's numbers). Pure.

    Rules: every allocation positive; no allocation exceeds its invoice's
    remaining balance; allocations sum EXACTLY to the check amount (with
    explicit per-invoice amounts there is nothing to "override" — the split
    itself must account for every dollar of the check).

    Returns {ok, errors, partial_ids, full_ids}.
    """
    errors: list[str] = []
    partial_ids: list[int] = []
    full_ids: list[int] = []
    check_cents = to_cents(check_amount)
    if check_cents is None or check_cents <= 0:
        return {"ok": False, "errors": ["Check amount is missing or unparseable."],
                "partial_ids": [], "full_ids": []}

    total = 0
    for item_id, alloc in allocations.items():
        bal = balances.get(item_id)
        if alloc is None or alloc <= 0:
            errors.append(f"Allocation for invoice item {item_id} must be a positive amount.")
            continue
        total += alloc
        if bal is None:
            errors.append(f"No remaining balance known for invoice item {item_id}.")
        elif alloc > bal:
            errors.append(
                f"Allocation ${alloc / 100:,.2f} for invoice item {item_id} exceeds its "
                f"remaining balance ${bal / 100:,.2f}.")
        elif alloc == bal:
            full_ids.append(item_id)
        else:
            partial_ids.append(item_id)

    if total != check_cents:
        errors.append(
            f"Allocations total ${total / 100:,.2f} but the check amount is "
            f"${check_cents / 100:,.2f} — every dollar of the check must be assigned.")

    return {"ok": not errors, "errors": errors,
            "partial_ids": partial_ids, "full_ids": full_ids}


def partial_note_line(alloc_cents: int, check_no, date_str, remaining_cents: int,
                      covers: Optional[list[str]] = None) -> str:
    """The idempotent Monday note line for one partial payment. Pure."""
    line = (f"Partial payment ${alloc_cents / 100:,.2f} by check "
            f"#{check_no or '?'} on {date_str or 'unknown date'} — "
            f"${remaining_cents / 100:,.2f} remaining")
    if covers and len(covers) > 1:
        line += f" (check covers {len(covers)} invoices: {', '.join(covers)})"
    return line
