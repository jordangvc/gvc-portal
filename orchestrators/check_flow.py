"""Paid-by-check orchestration (extracted from service.py route handlers).

The web layer (app.service) now only reads the upload + resolves the signed-in
user, then delegates here. This module owns the cross-system work: Vision OCR →
parse → match (extract), and the re-fetch → guard → Stripe → Monday → Drive
record pipeline (commit). HTTP-specific failures raise HTTPException so the thin
route can return them unchanged.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from fastapi import HTTPException
import stripe

from shared import activity
from shared.errors import _friendly_error
from adapters import vision
from adapters.drive import DriveUploader
from adapters.monday.client import MondayClient, MondayNotConfigured
from adapters.stripe_invoice import (
    find_current_invoice_for_identifier,
    record_partial_out_of_band,
)
from subsystems.checks import deposit as check_deposit


def _slim_row(r: dict) -> dict:
    return {"monday_item_id": r.get("monday_item_id"), "identifier": r.get("identifier"),
            "amount": r.get("amount"), "customer": r.get("customer"), "status": r.get("status"),
            # Remaining balance (Balance Due column after a prior partial; else
            # the full amount) — the UI matches and prefills allocations from this.
            "balance_due": r.get("balance_due"),
            "effective_cents": check_deposit.effective_amount_cents(r)}


def extract_check(image_bytes: bytes, *, email: str) -> dict:
    """READ ONLY. OCR the check, parse fields, match against open invoices. No writes."""
    try:
        text = vision.ocr_text(image_bytes)
    except vision.VisionNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "VISION_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — Vision API / credentials aren't set up on the service."},
        )

    parsed = check_deposit.parse_check_ocr(text)
    n_checks = check_deposit.count_checks(text)
    try:
        rows = MondayClient().fetch_invoice_rows(open_only=True)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    res = check_deposit.match_invoices(parsed.as_dict(), rows)
    activity.log_event("check.extract", actor=email,
                       target=parsed.check_no or "unknown",
                       amount=str(parsed.amount), confidence=res.confidence,
                       n_checks=n_checks,
                       matched=",".join((m.get("identifier") or "?") for m in res.matches) or None)
    # Oldest-first split suggestion for the matched invoices — the UI prefills
    # per-invoice amounts from this when the check doesn't cover the balances.
    suggested = (check_deposit.suggest_allocations(parsed.amount, res.matches)
                 if res.matches else None)

    return {
        "ok": True,
        "parsed": parsed.as_dict(),
        # A check may pay SEVERAL invoices (stub lists them / amounts sum). The
        # UI pre-selects all of `matches`; `match` kept for single-match compat.
        "suggested_allocations": suggested,
        "matches": [_slim_row(r) for r in res.matches],
        "match": _slim_row(res.matches[0]) if len(res.matches) == 1 else None,
        "candidates": [_slim_row(r) for r in res.candidates],
        "confidence": res.confidence,
        "reason": res.reason,
        "open_rows": [_slim_row(r) for r in rows],
        # Fail safe on multi-check images: the parsed fields below describe only
        # ONE check, so the UI warns and asks for one check per photo.
        "multi_check": n_checks,
    }


def _stripe_state(
    stripe_invoice_id: Optional[str],
) -> tuple[bool, Optional[str], Optional[str], Optional[int]]:
    """(stripe_paid, stripe_status, customer_id, amount_remaining_cents) for one
    invoice; raises the friendly envelope."""
    if not stripe_invoice_id:
        return False, None, None, None
    try:
        inv = stripe.Invoice.retrieve(stripe_invoice_id)
        status = getattr(inv, "status", None)
        return ((status == "paid") or bool(getattr(inv, "paid", False)), status,
                getattr(inv, "customer", None),
                getattr(inv, "amount_remaining", None))
    except Exception as e:  # noqa: BLE001
        status_code, code, detail, advice = _friendly_error(e)
        raise HTTPException(status_code=status_code, detail={"ok": False, "code": code,
            "detail": detail, "advice": advice})


def commit_check(*, monday_item_ids: list[int], image_bytes: bytes, content_type: Optional[str],
                 check_no: Optional[str], amount: Optional[str], date_str: Optional[str],
                 email: str, allow_mismatch: bool = False,
                 allocations: Optional[dict[int, int]] = None) -> dict:
    """
    Record a check against ONE OR MORE open invoices. Two modes:

    FULL (no `allocations`, legacy): each selected invoice is paid IN FULL via
    Stripe pay(paid_out_of_band); sum gate warns on mismatch, override via
    allow_mismatch.

    ALLOCATED (`allocations` = {monday_item_id: cents}): the office splits the
    check across invoices. An allocation equal to the invoice's remaining
    balance settles it in full (same paid_out_of_band path); a smaller one is
    recorded as a PARTIAL payment — a Stripe PaymentRecord attached to the
    invoice (native partial out-of-band; the invoice stays open with an
    accurate amount_remaining) + Monday Status="Partially Paid" + Balance Due
    sync. Allocations must sum EXACTLY to the check amount and may not exceed
    any invoice's remaining balance (validated against Stripe, never the
    client's numbers).

    Order: re-fetch every invoice (never trust the client for stripe id /
    folder / status) → already-deposited guard → sum/allocation gate → per
    invoice: Stripe → Monday → file the image in the invoice's Drive folder.
    Every step idempotent (partials dedupe by note line + Stripe idempotency
    key), and per-invoice failures don't stop the remaining invoices — a retry
    re-plans and runs only what's missing.
    """
    allocations = {int(k): int(v) for k, v in (allocations or {}).items()} or None
    ids = list(dict.fromkeys(monday_item_ids))  # dedupe, preserve order
    if allocations and set(allocations) != set(ids):
        raise HTTPException(status_code=400, detail={"ok": False, "code": "BAD_ALLOCATIONS",
            "detail": "Allocations must cover exactly the selected invoices.",
            "advice": "Re-open the check and confirm again — the split didn't match the selection."})
    if not ids:
        raise HTTPException(status_code=400, detail={"ok": False, "code": "NO_INVOICES",
            "detail": "No invoices selected.",
            "advice": "Pick the invoice(s) this check pays before recording."})

    # 1) Re-fetch every chosen invoice authoritatively.
    mc = MondayClient()
    rows: list[dict] = []
    try:
        for item_id in ids:
            row = mc.get_invoice_row(item_id)
            if not row:
                raise HTTPException(status_code=404, detail={"ok": False, "code": "INVOICE_NOT_FOUND",
                    "detail": f"No Invoices-Sent item with id {item_id}.",
                    "advice": "Re-open the check, pick the invoice(s) from the list, and confirm again."})
            rows.append(row)
    except MondayNotConfigured as e:
        raise HTTPException(status_code=503, detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED",
            "detail": str(e), "advice": "Ask an admin to set MONDAY_API_TOKEN."})

    identifiers = [r.get("identifier") or str(i) for r, i in zip(rows, ids)]
    covers = identifiers if len(identifiers) > 1 else None

    # 2) Determine each invoice's Stripe paid-state (authoritative), then plan.
    api_key = os.environ.get("STRIPE_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail={"ok": False, "code": "STRIPE_NOT_CONFIGURED",
            "detail": "STRIPE_API_KEY is not set.", "advice": "Ask an admin to set the STRIPE_API_KEY secret."})
    stripe.api_key = api_key

    states: list[dict] = []
    stripe_statuses: list[Optional[str]] = []
    repoints: list[Optional[dict]] = []
    remainings: list[Optional[int]] = []
    for row in rows:
        paid, status, cust, remaining = _stripe_state(row.get("stripe_invoice_id"))
        repoint: Optional[dict] = None
        if status == "void":
            # STALE LEDGER ROW: the row's Stripe invoice was voided (corrected/
            # reissued, or voided in the dashboard) and the row never repointed.
            # Resolve the CURRENT invoice for the same Document # and self-heal
            # instead of failing with Stripe's raw "Voided invoices cannot be
            # paid" (the CU-0166 incident, 2026-07-06).
            current = find_current_invoice_for_identifier(
                row.get("identifier") or "", customer_id=cust)
            if current:
                repoint = {"from": row.get("stripe_invoice_id"), "to": current["id"],
                           "hosted_url": current.get("hosted_invoice_url")}
                row["stripe_invoice_id"] = current["id"]
                # Re-read the replacement's authoritative state (incl. remaining).
                paid, status, cust, remaining = _stripe_state(current["id"])
            else:
                repoint = {"unresolved": True, "from": row.get("stripe_invoice_id")}
        repoints.append(repoint)
        remainings.append(remaining if remaining is not None
                          else check_deposit.effective_amount_cents(row))
        states.append({"stripe_paid": paid,
                       "monday_paid": (row.get("status") or "").strip().lower() == "paid"})
        stripe_statuses.append(status)

    plan = check_deposit.multi_deposit_plan(states)
    if plan["already_deposited"]:
        activity.log_event("check.already_deposited", actor=email, target=";".join(identifiers),
                           result="ok", check_no=check_no)
        return {"ok": True, "already_deposited": True, "invoice": ", ".join(identifiers),
                "invoices": identifiers, "message": plan["message"]}

    # 3) Sum / allocation gate.
    if allocations:
        # ALLOCATED mode: per-invoice amounts must be positive, must not exceed
        # each invoice's REMAINING balance (Stripe-authoritative), and must sum
        # exactly to the check. Retry-safety: an invoice fully recorded on a
        # previous attempt has plan.already_deposited — drop it from validation
        # (its allocation is already banked) and skip it in the loop below.
        balances = {i: r for i, r in zip(ids, remainings)}
        pending_allocs = {i: a for i, a in allocations.items()
                          if not plan["plans"][ids.index(i)]["already_deposited"]}
        banked = sum(a for i, a in allocations.items() if i not in pending_allocs)
        pending_amount = f"{((check_deposit.to_cents(amount) or 0) - banked) / 100:.2f}"
        gate = check_deposit.validate_allocations(pending_amount, pending_allocs, balances)
        if not gate["ok"]:
            raise HTTPException(status_code=409, detail={"ok": False, "code": "ALLOCATION_INVALID",
                "detail": " ".join(gate["errors"]),
                "advice": "Adjust the per-invoice amounts so they cover the whole check "
                          "without exceeding any invoice's remaining balance, then confirm again."})
        partial_ids = set(gate["partial_ids"])
    else:
        # FULL mode (legacy) — sum gate over remaining balances. Warn-and-
        # override (decided 2026-07-03): a mismatch blocks unless allow_mismatch.
        sums = check_deposit.sum_check(amount, rows)
        if sums["matched"] is False and not allow_mismatch:
            raise HTTPException(status_code=409, detail={"ok": False, "code": "SUM_MISMATCH",
                "detail": (f"The selected invoice(s) total ${sums['invoices_cents'] / 100:,.2f} "
                           f"but the check amount is ${sums['check_cents'] / 100:,.2f} "
                           f"(difference ${abs(sums['delta_cents']) / 100:,.2f})."),
                "sum": sums,
                "advice": "Re-check the selection (each invoice is recorded as paid IN FULL). "
                          "For a short-paid check, enter per-invoice amounts instead — partial "
                          "payments are supported. If the difference is intentional, tick "
                          "'Record anyway' and confirm again."})
        partial_ids = set()

    results: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    ext = ".jpg"
    ctype = (content_type or "").lower()
    if "png" in ctype:
        ext = ".png"
    elif "pdf" in ctype:
        ext = ".pdf"

    for row, item_id, identifier, inv_plan, stripe_status, repoint, remaining in zip(
            rows, ids, identifiers, plan["plans"], stripe_statuses, repoints, remainings):
        res: dict[str, str] = {}
        inv_errors: list[str] = []
        stripe_invoice_id = row.get("stripe_invoice_id")
        alloc = allocations.get(item_id) if allocations else None
        is_partial = alloc is not None and item_id in partial_ids

        # 3-pre) Row points at a voided Stripe invoice and no live replacement
        #        was found — a clean, actionable error beats Stripe's raw
        #        "Voided invoices cannot be paid". Skip this invoice's writes;
        #        the others still run.
        if repoint and repoint.get("unresolved"):
            inv_errors.append(
                f"[{identifier}] The Monday row points at Stripe invoice "
                f"{repoint['from']}, which is VOID, and no live invoice with the "
                f"same Document # was found — it was likely reissued under a new "
                f"number. Fix the row's Stripe Invoice ID (or pick the reissued "
                f"invoice's row) and confirm again.")
            results[identifier] = {"stripe": "skipped — row points at a voided invoice"}
            errors.extend(inv_errors)
            continue

        # 3a-partial) PARTIAL allocation: record the check portion as a Stripe
        #     PaymentRecord attached to the invoice (native partial out-of-band;
        #     invoice stays open, amount_remaining accurate), then Monday
        #     Status="Partially Paid" + Balance Due sync. Idempotent: the
        #     deterministic note line dedupes a clean retry, and the Stripe
        #     idempotency key prevents double-crediting within its window.
        if is_partial and inv_plan["do_stripe"]:
            remaining_before = remaining if remaining is not None else 0
            note_line = check_deposit.partial_note_line(
                alloc, check_no, date_str, max(remaining_before - alloc, 0), covers=covers)
            if note_line in (row.get("note") or ""):
                res["stripe"] = "partial already recorded (note present)"
                res["monday"] = "already Partially Paid"
            elif not stripe_invoice_id:
                # No Stripe pointer — record the partial on the board only
                # (mirrors the full path's behavior for id-less rows).
                try:
                    mres = mc.set_invoice_partially_paid(
                        item_id, note_line=note_line,
                        remaining_cents=max(remaining_before - alloc, 0))
                    res["stripe"] = "skipped — no Stripe invoice id on the row"
                    res["monday"] = "Partially Paid" + (" (+note)" if mres.get("note_appended") else "")
                except Exception as e:  # noqa: BLE001
                    inv_errors.append(f"Monday [{identifier}]: {e}")
            else:
                after = max(remaining_before - alloc, 0)
                try:
                    pres = record_partial_out_of_band(
                        stripe_invoice_id, alloc, identifier=identifier,
                        check_no=check_no, date_str=date_str)
                    if pres.get("amount_remaining") is not None:
                        after = pres["amount_remaining"]
                    res["stripe"] = (f"partial ${alloc / 100:,.2f} recorded (out of band) — "
                                     f"${after / 100:,.2f} remaining")
                except Exception as e:  # noqa: BLE001
                    inv_errors.append(f"Stripe [{identifier}]: {e}")
                if not inv_errors:
                    try:
                        if after == 0:
                            # Payment records covered the whole balance after all.
                            mres = mc.set_invoice_paid(item_id, check_no=check_no,
                                                       date_str=date_str, covers=covers)
                            res["monday"] = "Paid" + (" (+note)" if mres.get("note_appended") else "")
                        else:
                            mres = mc.set_invoice_partially_paid(
                                item_id, note_line=note_line, remaining_cents=after)
                            res["monday"] = ("Partially Paid"
                                             + (" (+note)" if mres.get("note_appended") else ""))
                    except Exception as e:  # noqa: BLE001
                        inv_errors.append(f"Monday [{identifier}]: {e}")

        # 3a) FULL settle — mark paid out of band (we received a physical
        #     check). Covers the legacy no-allocation mode AND allocations that
        #     equal the invoice's remaining balance. Safety net: a draft
        #     invoice can't be paid, so finalize it first (paid_out_of_band
        #     requires an open invoice). Normal GVC invoices are already
        #     finalized.
        elif inv_plan["do_stripe"] and stripe_invoice_id:
            try:
                finalized_note = ""
                if stripe_status == "draft":
                    stripe.Invoice.finalize_invoice(stripe_invoice_id)
                    finalized_note = "finalized draft + "
                stripe.Invoice.pay(stripe_invoice_id, paid_out_of_band=True)
                res["stripe"] = f"{finalized_note}marked paid (out of band)"
            except Exception as e:  # noqa: BLE001
                inv_errors.append(f"Stripe [{identifier}]: {e}")
        elif not stripe_invoice_id:
            res["stripe"] = "skipped — no Stripe invoice id on the row"
        else:
            res["stripe"] = "already paid"

        # 3a2) Self-heal the stale row: persist the resolved Stripe invoice id
        #      + pay link back to Monday so the next flow reads the right
        #      invoice. Only after this invoice's Stripe step succeeded;
        #      best-effort (the payment itself already landed correctly).
        if repoint and repoint.get("to") and not inv_errors:
            try:
                mc.repoint_invoice_stripe(
                    item_id, repoint["to"], hosted_url=repoint.get("hosted_url"),
                    note=(f"Stripe invoice repointed {repoint['from']} → "
                          f"{repoint['to']} (original voided; auto-fixed by "
                          f"check recording)"))
                res["repointed"] = f"row Stripe id fixed: {repoint['from']} → {repoint['to']}"
            except Exception as e:  # noqa: BLE001 — heal is best-effort
                res["repointed"] = f"row fix skipped: {e}"

        # 3b) Monday — Status=Paid + note (FULL path only; the partial branch
        #     wrote its own Monday state above). Runs only if THIS invoice's
        #     Stripe step didn't just fail; other invoices' failures don't
        #     block it.
        if is_partial:
            pass  # Monday handled in 3a-partial
        elif inv_plan["do_monday"] and not inv_errors:
            try:
                mres = mc.set_invoice_paid(item_id, check_no=check_no, date_str=date_str,
                                           covers=covers)
                res["monday"] = "Paid" + (" (+note)" if mres.get("note_appended") else "")
            except Exception as e:  # noqa: BLE001
                inv_errors.append(f"Monday [{identifier}]: {e}")
        elif not inv_plan["do_monday"]:
            res["monday"] = "already Paid"

        # 3c) Drive — file the check/stub image in EACH invoice's folder (decided
        #     2026-07-03: every project keeps its own payment record). Best-effort.
        folder_id = check_deposit.drive_folder_id(row.get("drive_folder_url"))
        if folder_id and not inv_errors:
            try:
                safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", identifier)
                fname = f"check_{check_no or 'unknown'}__{safe_id}{ext}"
                up = DriveUploader().upload_or_replace_file(
                    folder_id=folder_id, filename=fname, data=image_bytes,
                    mimetype=content_type or "application/octet-stream")
                res["drive"] = up.get("web_view_link") or "filed"
            except Exception as e:  # noqa: BLE001 — Stripe+Monday are the truth; Drive is convenience
                res["drive"] = f"skipped — upload failed: {e}"
        elif not folder_id:
            res["drive"] = "skipped — no Drive folder on the invoice row"

        results[identifier] = res
        errors.extend(inv_errors)

    ok = not errors
    # Customer + job come off the Monday rows so the activity log answers
    # "whose check was this?" without opening the board (dedup preserves order).
    _customers = list(dict.fromkeys(r.get("customer") for r in rows if r.get("customer")))
    _jobs = list(dict.fromkeys(r.get("job") for r in rows if r.get("job")))
    activity.log_event("check.commit", actor=email, target=";".join(identifiers),
                       result="ok" if ok else "error", check_no=check_no,
                       customer=", ".join(_customers) or None,
                       job=", ".join(_jobs) or None,
                       amount=amount, n_invoices=len(ids),
                       steps=";".join(f"{i}:{k}={v}" for i, r in results.items() for k, v in r.items()),
                       errors=";".join(errors) or None)

    if not ok:
        # COMMIT_PARTIAL (502) propagates to the global exception handler, which
        # fires the ops failure alert — no separate alert needed here.
        raise HTTPException(status_code=502, detail={"ok": False, "code": "COMMIT_PARTIAL",
            "detail": "; ".join(errors), "invoice": ", ".join(identifiers), "results": results,
            "advice": "Some steps failed. It's safe to confirm again — completed steps and "
                      "already-recorded invoices are skipped on retry."})

    # Best-effort billing-channel notice that money came in. Never breaks the
    # commit (the payment is already recorded in Stripe + Monday by this point).
    try:
        from adapters.slack_notify import notify_payment_recorded
        notify_payment_recorded({"identifier": ", ".join(identifiers), "amount": amount,
                                 "check_no": check_no})
    except Exception:  # noqa: BLE001 — alerting must never break the flow
        pass

    return {"ok": True, "invoice": ", ".join(identifiers), "invoices": identifiers,
            "results": results}
