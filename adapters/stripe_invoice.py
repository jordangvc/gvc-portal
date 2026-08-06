"""Stripe customer + invoice operations (extracted from invoice.py)."""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from typing import Optional

import stripe

from shared.money import to_cents

def upsert_stripe_customer(client_data: dict) -> stripe.Customer:
    """
    Lookup customer by stripe_customer_id if present, else by email.
    Create only if neither exists. Idempotent on email.

    On reuse, refresh name + address from the current JSON so the hosted
    invoice always reflects the latest client info (prevents the "old stale
    customer info on a new invoice" problem).
    """
    desired_name = client_data["name"]
    desired_address = _parse_address_block(client_data.get("billing_address", ""))

    sid = client_data.get("stripe_customer_id")
    if sid:
        return stripe.Customer.modify(sid, name=desired_name, address=desired_address)

    email = client_data["email"]
    existing = stripe.Customer.list(email=email, limit=1).data
    if existing:
        return stripe.Customer.modify(existing[0].id, name=desired_name, address=desired_address)

    return stripe.Customer.create(
        name=desired_name,
        email=email,
        address=desired_address,
        metadata={"gvc_source": "invoice_mvp"},
    )


def _parse_address_block(block: str) -> dict:
    """
    Best-effort parse of a free-text address block into Stripe Address shape.
    Stripe accepts partial; missing fields are fine.
    Expected input shape (one per line):
        Street
        City, ST ZIP
    """
    out = {"country": "US"}
    lines = [l.strip() for l in (block or "").splitlines() if l.strip()]
    if not lines:
        return out
    out["line1"] = lines[0]
    if len(lines) >= 2:
        last = lines[-1]
        # Try to split "City, ST ZIP"
        if "," in last:
            city, rest = last.rsplit(",", 1)
            out["city"] = city.strip()
            parts = rest.strip().split()
            if parts:
                out["state"] = parts[0]
            if len(parts) >= 2:
                out["postal_code"] = parts[1]
    return out


def void_stripe_invoice(stripe_invoice_id: str) -> dict:
    """
    Void a finalized Stripe invoice (used by the correction / reissue flow).

    Idempotent and tolerant of the states a correction realistically hits:
      - already "void"            -> no-op, returns status "void"
      - "draft"                   -> delete it (Stripe can't void a draft)
      - "open"                    -> void it
      - "paid" / "uncollectible"  -> raised to the caller (voiding a paid invoice
                                     is a real decision, not a quiet side effect)

    Returns {id, status, action}. Requires stripe.api_key to be set by the caller.
    """
    inv = stripe.Invoice.retrieve(stripe_invoice_id)
    status = inv.status
    if status == "void":
        return {"id": inv.id, "status": "void", "action": "noop_already_void"}
    if status == "draft":
        stripe.Invoice.delete(stripe_invoice_id)
        return {"id": stripe_invoice_id, "status": "deleted", "action": "deleted_draft"}
    if status in ("paid", "uncollectible"):
        raise ValueError(
            f"Refusing to auto-void invoice {stripe_invoice_id} in status "
            f"'{status}' — a paid/uncollectible invoice must be handled deliberately."
        )
    voided = stripe.Invoice.void_invoice(stripe_invoice_id)
    return {"id": voided.id, "status": voided.status, "action": "voided"}


def create_stripe_invoice(
    customer: stripe.Customer,
    enriched: dict,
    *,
    finalize: bool = True,
    from_invoice_id: Optional[str] = None,
) -> stripe.Invoice:
    """
    Build a Stripe Invoice with one InvoiceItem per line item (plus discount).
    Idempotent on invoice.identifier so reruns don't double-bill.

    Flow:
      1) Create draft invoice (empty).
      2) Create each InvoiceItem with `invoice=invoice.id` so it attaches
         explicitly to THIS invoice. This is critical when the same customer
         has multiple invoices in a batch — the older pending-items pattern
         would let them compete for the same orphaned items.
      3) Finalize to get hosted_invoice_url.

    REVISION MODE (`from_invoice_id` set): create the invoice as a Stripe
    *revision* of an existing finalized invoice (`from_invoice`). Stripe links
    the two and AUTO-VOIDS the original when the revision is finalized — the
    native way to correct a finalized (immutable) invoice. NOTE: from_invoice
    COPIES the original's line items into the draft, so we clear those first and
    then attach the corrected lines below — otherwise the revision would double
    every line. The new identifier (e.g. "… Rev 1") gives a fresh idempotency
    root, so no key collision with the original.

    NOTE: We do NOT call stripe.Invoice.send_invoice(). Per the architecture,
    Andrea reviews the GVC-branded PDF and triggers send via Gmail. Stripe
    only provides the hosted payment URL.
    """
    inv = enriched["invoice"]
    identifier = inv["identifier"]

    days_until_due = max(
        (datetime.strptime(inv["due_date"], "%Y-%m-%d").date()
         - datetime.strptime(inv["issue_date"], "%Y-%m-%d").date()).days,
        0,
    )

    # v3 prefix: v2 invoices were voided after the Upon Receipt / due-today issue
    # discovered 2026-05-18. Bumping prefix gives fresh idempotency caches.
    idempotency_root = f"gvc_inv_v3_{identifier}"

    # Build the human-readable Stripe description.
    # Convention: "{job} — Progress Bill #N" / "{job} — Final Bill" / "{job} — {identifier}".
    # Append " plusTM" if any line item is flagged as T&M / change-order work.
    invoice_type = inv.get("invoice_type", "standard")
    pay_app_number = inv.get("pay_app_number")
    if invoice_type == "progress" and pay_app_number is not None:
        bill_label = f"Progress Bill #{pay_app_number}"
    elif invoice_type == "final":
        bill_label = "Final Bill"
    else:
        bill_label = identifier
    has_tm = any(
        (li.get("kind") or "").lower() in ("tm", "co", "change_order", "change-order")
        for li in inv["line_items"]
    )
    tm_suffix = " plusTM" if has_tm else ""
    stripe_description = f"{enriched['job']['name']} — {bill_label}{tm_suffix}"

    # 1) Draft invoice
    create_kwargs: dict = dict(
        customer=customer.id,
        collection_method="send_invoice",
        days_until_due=days_until_due,
        description=stripe_description,
        metadata={
            "gvc_invoice_id": identifier,
            "gvc_job_name": enriched["job"]["name"],
            "gvc_source": "invoice_mvp",
            "gvc_bill_label": bill_label,
            "gvc_has_tm": "true" if has_tm else "false",
            **({"gvc_revision_of": from_invoice_id} if from_invoice_id else {}),
        },
        pending_invoice_items_behavior="exclude",
        idempotency_key=f"{idempotency_root}_create",
    )
    if from_invoice_id:
        # Native revision: Stripe links it to the original and auto-voids the
        # original on finalize.
        create_kwargs["from_invoice"] = {"action": "revision", "invoice": from_invoice_id}
    invoice = stripe.Invoice.create(**create_kwargs)

    if from_invoice_id:
        # from_invoice COPIES the original's line items into this draft. Remove
        # them so only our corrected lines (attached below) remain — otherwise
        # we'd double-bill. If the clear fails we raise BEFORE finalizing, so the
        # worst case is a harmless un-finalized draft, never a doubled charge.
        copied = stripe.Invoice.list_lines(invoice.id, limit=100).data
        line_ids = [{"id": ln.id} for ln in copied]
        if line_ids:
            stripe.Invoice.remove_lines(
                invoice.id, lines=line_ids,
                idempotency_key=f"{idempotency_root}_revclear",
            )

    # 2) Attach line items explicitly to this invoice.
    #
    # Retainage handling: NEVER pushed to Stripe as its own negative line item.
    # Instead, each line item's amount is reduced by its share of retainage
    # before being sent, so Stripe sees only the net due. Allocation depends
    # on retainage.scope:
    #   - "base" (default): the first non-CO line eats the full retainage.
    #     CO / T&M lines bill at full value.
    #   - "all": retainage is allocated proportionally across ALL lines
    #     (base + CO). Each line's share = total_retainage * (line_amount /
    #     sum_of_all_line_amounts). The last line picks up rounding
    #     remainder so cents tie out exactly.
    retainage_block = inv.get("retainage")
    retainage_for_stripe = abs(float(retainage_block["amount"])) if retainage_block else 0.0
    retainage_scope = (retainage_block.get("scope") if retainage_block else "base") or "base"
    line_count = len(inv["line_items"])
    retainage_per_line_cents: list[int] = [0] * line_count

    if retainage_for_stripe and line_count:
        total_retainage_cents = to_cents(retainage_for_stripe)
        if retainage_scope == "base":
            for i, li in enumerate(inv["line_items"]):
                if not li.get("_is_co"):
                    retainage_per_line_cents[i] = total_retainage_cents
                    break
        else:  # "all" — proportional allocation
            line_amounts_cents = [to_cents(li["amount"]) for li in inv["line_items"]]
            sum_amounts_cents = sum(line_amounts_cents)
            if sum_amounts_cents <= 0:
                raise ValueError("Cannot allocate retainage: line items sum to <= 0")
            allocated_so_far = 0
            for i, amt_c in enumerate(line_amounts_cents):
                if i == line_count - 1:
                    # Last line absorbs the remainder so totals tie out.
                    share_c = total_retainage_cents - allocated_so_far
                else:
                    share_c = round(total_retainage_cents * amt_c / sum_amounts_cents)
                    allocated_so_far += share_c
                retainage_per_line_cents[i] = share_c

    for i, li in enumerate(inv["line_items"]):
        gross_cents = to_cents(li["amount"])
        retain_cents = retainage_per_line_cents[i]
        net_cents = gross_cents - retain_cents
        stripe_description = li["description"]
        if retain_cents > 0:
            stripe_description = f"{stripe_description} (net of retainage)"
        stripe.InvoiceItem.create(
            customer=customer.id,
            invoice=invoice.id,
            amount=net_cents,
            currency="usd",
            description=stripe_description,
            metadata={
                "gvc_invoice_id": identifier,
                "gvc_line_index": str(i),
                "gvc_line_kind": (li.get("kind") or "work"),
            },
            idempotency_key=f"{idempotency_root}_li_{i}",
        )

    if inv.get("discount"):
        stripe.InvoiceItem.create(
            customer=customer.id,
            invoice=invoice.id,
            amount=to_cents(inv["discount"]["amount"]),  # negative
            currency="usd",
            description=inv["discount"]["description"],
            metadata={"gvc_invoice_id": identifier, "gvc_kind": "discount"},
            idempotency_key=f"{idempotency_root}_discount",
        )

    # 3) Finalize so we get a hosted_invoice_url
    if finalize:
        invoice = stripe.Invoice.finalize_invoice(invoice.id)

    return invoice


# Statuses that mean "do not create another Stripe invoice for this
# identifier". `draft` is included so a create that failed mid-finalize
# (orphan draft) is finalized-and-reused instead of duplicated — see
# CLAUDE.md "INVOICE FINALIZE FINDING (2026-06-18)".
REUSABLE_INVOICE_STATUSES = frozenset({"open", "paid", "draft"})


def is_reusable_stripe_status(status: Optional[str]) -> bool:
    """True when live create should short-circuit to the existing invoice."""
    return (status or "").strip().lower() in REUSABLE_INVOICE_STATUSES


def finalize_draft_invoice(invoice_id: str) -> dict:
    """
    Finalize an orphan Stripe draft so it gets a hosted_invoice_url.

    Returns the same dict shape preflight reports for an existing invoice.
    Raises Stripe errors to the caller (live path maps them via _friendly_error).
    """
    if not invoice_id:
        raise ValueError("finalize_draft_invoice requires invoice_id")
    inv = stripe.Invoice.finalize_invoice(invoice_id)
    return {
        "id": inv.id,
        "status": inv.status,
        "hosted_invoice_url": inv.hosted_invoice_url,
        "amount_due": inv.amount_due,
        "customer": getattr(inv, "customer", None),
    }


def preflight_stripe(enriched: dict) -> dict:
    """
    Read-only Stripe check. Reports what would happen on a live run without
    creating customers, invoice items, or invoices. Returns a dict report.
    """
    client_data = enriched["client"]
    identifier = enriched["invoice"]["identifier"]

    report: dict = {
        "identifier": identifier,
        "customer": {"action": None, "id": None, "email": client_data["email"]},
        "existing_invoice_with_identifier": None,
    }

    sid = client_data.get("stripe_customer_id")
    if sid:
        cust = stripe.Customer.retrieve(sid)
        report["customer"]["action"] = "reuse_by_id"
        report["customer"]["id"] = cust.id
    else:
        existing = stripe.Customer.list(email=client_data["email"], limit=1).data
        if existing:
            report["customer"]["action"] = "reuse_by_email"
            report["customer"]["id"] = existing[0].id
        else:
            report["customer"]["action"] = "would_create"

    if report["customer"]["id"]:
        invs = stripe.Invoice.list(customer=report["customer"]["id"], limit=100).data
        for inv in invs:
            md = inv.metadata
            inv_gvc_id = getattr(md, "gvc_invoice_id", None) if md else None
            inv_gvc_status = getattr(md, "gvc_status", None) if md else None
            if inv_gvc_id != identifier:
                continue
            if inv_gvc_status == "zombie_replaced":
                # Ignore the empty-invoice zombies left by the pre-v2 batch run.
                continue
            report["existing_invoice_with_identifier"] = {
                "id": inv.id,
                "status": inv.status,
                "hosted_invoice_url": inv.hosted_invoice_url,
                "amount_due": inv.amount_due,
                "customer": getattr(inv, "customer", None),
            }
            break

    # Email-proof fallback. The customer-scoped scan above misses the original
    # invoice whenever the office EDITED the client email before re-running:
    # the new email resolves to a different (or no) customer, so the original
    # invoice — filed under the OLD customer — looks invisible, the guard falls
    # through to "create", and Stripe rejects the reused idempotency key with an
    # IdempotencyError. (That is exactly the storm Andrea hit.) Look the invoice
    # up by its gvc_invoice_id metadata, independent of customer, so an email
    # edit can never make a re-run masquerade as a brand-new invoice.
    if report["existing_invoice_with_identifier"] is None:
        found = _find_invoice_by_identifier_metadata(identifier)
        if found:
            report["existing_invoice_with_identifier"] = found
            cust_id = report["customer"]["id"]
            report["existing_invoice_email_mismatch"] = (
                not cust_id or found.get("customer") != cust_id
            )

    return report


def _find_invoice_by_identifier_metadata(identifier: str) -> Optional[dict]:
    """
    Find an invoice by its `gvc_invoice_id` metadata, independent of which
    customer it's filed under. Uses the Stripe Search API. Returns the first
    non-zombie match as the same dict shape preflight reports, or None.

    GRACEFUL: any failure (Search API not enabled, transient error, no match)
    returns None so callers degrade to the customer-scoped result rather than
    raising — a read-only convenience, never a hard dependency.
    """
    if not identifier:
        return None
    # Escape single quotes for the search query string per Stripe's syntax.
    safe = identifier.replace("'", r"\'")
    try:
        res = stripe.Invoice.search(
            query=f"metadata['gvc_invoice_id']:'{safe}'", limit=20
        )
    except Exception as e:  # noqa: BLE001 — search is best-effort
        print(f"[preflight] metadata search skipped: {type(e).__name__}: {e}",
              file=sys.stderr)
        return None
    for inv in getattr(res, "data", []) or []:
        md = inv.metadata
        if md and getattr(md, "gvc_status", None) == "zombie_replaced":
            continue
        return {
            "id": inv.id,
            "status": inv.status,
            "hosted_invoice_url": inv.hosted_invoice_url,
            "amount_due": inv.amount_due,
            "customer": getattr(inv, "customer", None),
        }
    return None


# Ranking for pick_current_invoice: the invoice a payment should land on.
# open first (payable now), then draft (finalizable), then paid (already
# settled — caller decides), then uncollectible. void/zombie never qualify.
_CURRENT_STATUS_RANK = {"open": 0, "draft": 1, "paid": 2, "uncollectible": 3}


def pick_current_invoice(candidates: list[dict]) -> Optional[dict]:
    """
    PURE. From a list of {id, status, created, ...} candidates sharing one
    gvc_invoice_id, pick the CURRENT invoice: drop void/unknown statuses,
    prefer open > draft > paid > uncollectible, newest `created` on a tie.
    Returns the winning dict or None.
    """
    live = [c for c in candidates or [] if (c.get("status") or "") in _CURRENT_STATUS_RANK]
    if not live:
        return None
    live.sort(key=lambda c: (_CURRENT_STATUS_RANK[c["status"]], -(c.get("created") or 0)))
    return live[0]


def _invoice_candidate(inv) -> dict:
    return {
        "id": inv.id,
        "status": inv.status,
        "hosted_invoice_url": getattr(inv, "hosted_invoice_url", None),
        "amount_due": getattr(inv, "amount_due", None),
        "customer": getattr(inv, "customer", None),
        "created": getattr(inv, "created", 0),
    }


def find_current_invoice_for_identifier(
    identifier: str, *, customer_id: Optional[str] = None
) -> Optional[dict]:
    """
    Resolve the CURRENT (non-void) Stripe invoice carrying gvc_invoice_id ==
    `identifier`. Used to self-heal a stale Monday ledger row that still points
    at a voided/reissued invoice (the Greg Gavin CU-0166 failure, 2026-07-06).

    Two sources, merged: the customer-scoped Invoice.list (STRONGLY consistent
    — pass the voided invoice's own customer id) plus the metadata Search API
    (eventually consistent, but customer-independent so it survives email/
    customer edits). Both best-effort; ranking via pick_current_invoice.
    Returns the preflight-shaped dict (+created) or None.
    """
    if not identifier:
        return None
    cands: dict[str, dict] = {}

    if customer_id:
        try:
            for inv in stripe.Invoice.list(customer=customer_id, limit=100).data:
                md = inv.metadata
                if not md or getattr(md, "gvc_invoice_id", None) != identifier:
                    continue
                if getattr(md, "gvc_status", None) == "zombie_replaced":
                    continue
                cands[inv.id] = _invoice_candidate(inv)
        except Exception as e:  # noqa: BLE001 — best-effort source
            print(f"[current-invoice] customer list skipped: {type(e).__name__}: {e}",
                  file=sys.stderr)

    try:
        safe = identifier.replace("'", r"\'")
        res = stripe.Invoice.search(
            query=f"metadata['gvc_invoice_id']:'{safe}'", limit=20
        )
        for inv in getattr(res, "data", []) or []:
            md = inv.metadata
            if md and getattr(md, "gvc_status", None) == "zombie_replaced":
                continue
            cands.setdefault(inv.id, _invoice_candidate(inv))
    except Exception as e:  # noqa: BLE001 — best-effort source
        print(f"[current-invoice] metadata search skipped: {type(e).__name__}: {e}",
              file=sys.stderr)

    return pick_current_invoice(list(cands.values()))


def record_partial_out_of_band(
    invoice_id: str,
    amount_cents: int,
    *,
    identifier: str,
    check_no: Optional[str] = None,
    date_str: Optional[str] = None,
) -> dict:
    """
    Record a PARTIAL out-of-band (paper check) payment against an open Stripe
    invoice: report a PaymentRecord for the check portion, then attach it to
    the invoice. Stripe updates amount_paid/amount_remaining and flips the
    invoice to `paid` automatically only when payments cover the full amount
    — the native partial-payments flow (API 2025-03-31.basil+; SDK verified).

    Idempotency: report_payment carries an idempotency key scoped to
    (check_no, identifier, amount) so a clean retry of the same recording
    returns the SAME PaymentRecord instead of double-crediting (24h window).
    Callers should ALSO pre-check amount_remaining before calling.

    Returns {payment_record_id, amount_paid, amount_remaining, status} — the
    post-attach invoice state.
    """
    now = int(time.time())
    display = f"Paper check #{check_no}" if check_no else "Paper check"
    idem = "gvc_check_{}_{}_{}".format(
        re.sub(r"[^A-Za-z0-9]+", "", str(check_no or "unknown")),
        re.sub(r"[^A-Za-z0-9]+", "", identifier or "inv"),
        amount_cents,
    )
    pr = stripe.PaymentRecord.report_payment(
        amount_requested={"currency": "usd", "value": int(amount_cents)},
        initiated_at=now,
        outcome="guaranteed",
        guaranteed={"guaranteed_at": now},
        payment_method_details={"type": "custom",
                                "custom": {"display_name": display, "type": "check"}},
        processor_details={"type": "custom",
                           "custom": {"payment_reference": str(check_no or identifier)}},
        description=f"Partial check payment for {identifier}"
                    + (f" on {date_str}" if date_str else ""),
        metadata={"gvc_invoice_id": identifier,
                  "gvc_check_no": str(check_no or ""),
                  "gvc_check_date": str(date_str or "")},
        idempotency_key=idem,
    )
    inv = stripe.Invoice.attach_payment(invoice_id, payment_record=pr.id)
    return {
        "payment_record_id": pr.id,
        "amount_paid": getattr(inv, "amount_paid", None),
        "amount_remaining": getattr(inv, "amount_remaining", None),
        "status": getattr(inv, "status", None),
    }
