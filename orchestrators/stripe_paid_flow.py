"""Stripe `invoice.paid` webhook -> Monday Paid (v1 slice).

Online Stripe pay (the customer clicking the hosted-invoice "Pay Now" link)
has no webhook wired up yet, so the Invoices Sent board never learns the
invoice was paid — Andrea has to notice in the Stripe dashboard and flip the
row by hand. This orchestrator is the missing link: given a VERIFIED Stripe
event (the caller — app.service — already checked the signature), mark the
matching Monday row Paid the same way the paid-by-check flow does
(MondayClient.set_invoice_paid), just with a Stripe-flavored note line.

v1 scope, deliberately narrow (see docs/plans/2026-08-04-stripe-paid-monday.md):
  - Only `invoice.paid` is actioned. Every other event type is ignored.
  - No refunds, no partial payments, no `payment_intent.*` handling — those
    stay check_flow/subsystems.checks.deposit's job for now.
  - Never touches the Projects board invoice status (locked scope).

Pure-ish: takes a plain dict (the Stripe event, already `.to_dict()`'d by the
caller) and returns a plain result dict. The only I/O is MondayClient (+ a
best-effort activity log / Slack notice) — no Stripe SDK calls here, so this
module is cheap to unit test with a fake Monday client.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

from adapters.monday.client import MondayClient, MondayNotConfigured
from shared import activity

PAID_EVENT_TYPE = "invoice.paid"


def _invoice_object(event: dict) -> Optional[dict]:
    return ((event.get("data") or {}).get("object")) or None


def _pretty_amount(amount_cents: Optional[int]) -> Optional[str]:
    if amount_cents is None:
        return None
    try:
        return f"${amount_cents / 100:,.2f}"
    except (TypeError, ValueError):
        return None


def find_invoice_row_by_document(
    mc: MondayClient, *, identifier: Optional[str], stripe_invoice_id: Optional[str],
) -> Optional[dict]:
    """
    Find the Monday 'Invoices Sent' row for this Stripe invoice.

    Prefers an exact (case-insensitive) match on the Document # column —
    that's `metadata.gvc_invoice_id`, the unique dedupe key per
    adapters/monday/client.py. Falls back to a scan for a row already
    pointing at this Stripe invoice id, which covers older rows whose
    Document # doesn't line up 1:1 with the metadata for some reason.

    Scans ALL rows (`open_only=False`), not just open ones, so an
    already-Paid row is still found — that's what makes the caller's
    already-paid check idempotent instead of a false "no matching row".
    """
    rows = mc.fetch_invoice_rows(open_only=False)
    if identifier:
        needle = identifier.strip().lower()
        for row in rows:
            if (row.get("identifier") or "").strip().lower() == needle:
                return row
    if stripe_invoice_id:
        for row in rows:
            if (row.get("stripe_invoice_id") or "").strip() == stripe_invoice_id.strip():
                return row
    return None


def handle_stripe_event(event: dict) -> dict:
    """
    Handle one verified Stripe event dict (signature already checked by the
    caller — app.service). Only `invoice.paid` is actioned; every other type
    is ignored so the webhook endpoint can be pointed at a broader Stripe
    event selection later without any code change here.

    Never raises for a "nothing to do" business case (wrong event type,
    missing metadata, no matching Monday row, Monday not configured) — those
    come back as `{"ok": True, "handled": False, "reason": ...}` so the
    caller can always 200 the webhook and Stripe doesn't retry forever over
    something that will never resolve. A real Monday WRITE failure after a
    match was found comes back `ok: False` so it's visible in logs/activity,
    but still never raises.
    """
    event_type = event.get("type")
    if event_type != PAID_EVENT_TYPE:
        return {"ok": True, "handled": False,
                "reason": f"ignored event type {event_type!r}"}

    invoice = _invoice_object(event)
    if not invoice:
        return {"ok": True, "handled": False,
                "reason": "invoice.paid event had no data.object"}

    stripe_invoice_id = invoice.get("id")
    metadata = invoice.get("metadata") or {}
    identifier = (metadata.get("gvc_invoice_id") or "").strip() or None

    if not identifier and not stripe_invoice_id:
        return {"ok": True, "handled": False,
                "reason": "invoice had no gvc_invoice_id metadata and no id"}

    try:
        mc = MondayClient()
    except MondayNotConfigured as e:
        return {"ok": True, "handled": False, "reason": f"Monday not configured: {e}"}

    try:
        row = find_invoice_row_by_document(
            mc, identifier=identifier, stripe_invoice_id=stripe_invoice_id)
    except Exception as e:  # noqa: BLE001 — a Monday read hiccup is never fatal here
        print(f"[stripe-paid] Monday lookup failed for "
              f"{identifier or stripe_invoice_id}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return {"ok": True, "handled": False, "reason": f"Monday lookup failed: {e}"}

    if row is None:
        # No matching row (e.g. the invoice predates the portal, or Document #
        # was never set) — a business no-op, not an error. Always 200 this so
        # Stripe doesn't retry a case that will never resolve on retry.
        return {"ok": True, "handled": False, "stripe_invoice_id": stripe_invoice_id,
                "identifier": identifier, "reason": "no matching Invoices Sent row found"}

    item_id = row["monday_item_id"]
    row_identifier = row.get("identifier") or identifier or stripe_invoice_id

    if (row.get("status") or "").strip().lower() == "paid":
        return {"ok": True, "handled": True, "already_paid": True,
                "item_id": item_id, "identifier": row_identifier}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note_line = f"Paid via Stripe online on {today}"
    try:
        mres = mc.set_invoice_paid(item_id, note_line=note_line)
    except Exception as e:  # noqa: BLE001 — surface, but never raise out of a webhook
        print(f"[stripe-paid] Monday write failed for item {item_id}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return {"ok": False, "handled": False, "item_id": item_id,
                "identifier": row_identifier, "error": f"{type(e).__name__}: {e}"}

    activity.log_event(
        "stripe.invoice_paid", actor="stripe:webhook", target=row_identifier,
        result="ok", stripe_invoice_id=stripe_invoice_id, item_id=item_id,
        amount_paid_cents=invoice.get("amount_paid"),
    )

    # Best-effort billing-channel notice, same posture as the paid-by-check
    # flow (orchestrators/check_flow.py) — never breaks the webhook response.
    try:
        from adapters.slack_notify import notify_payment_recorded
        notify_payment_recorded({
            "identifier": row_identifier,
            "amount": _pretty_amount(invoice.get("amount_paid")),
        })
    except Exception:  # noqa: BLE001 — alerting must never break the flow
        pass

    return {"ok": True, "handled": True, "already_paid": False, "item_id": item_id,
            "identifier": row_identifier, "stripe_invoice_id": stripe_invoice_id,
            "note_appended": mres.get("note_appended")}
