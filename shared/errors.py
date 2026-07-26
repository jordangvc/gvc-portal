"""HTTP-facing error translation + field humanization (extracted from service.py).

Shared so both the web layer (app.service) and the invoice orchestrator
(orchestrators.invoice_flow) can produce the same {code, detail, advice}
envelope without a circular import.
"""
from __future__ import annotations

import re


_FIELD_LABELS = {
    "client.name": "Client name",
    "client.email": "Client email",
    "client.billing_address": "Billing address",
    "invoice.identifier": "Invoice number",
    "invoice.issue_date": "Issue date",
    "invoice.payment_terms": "Payment terms",
    "invoice.line_items": "Line items",
    "job.name": "Job name",
}


def humanize_validation_message(msg: str) -> str:
    """Turn an invoice.validate() ValueError into a message an office user can
    act on from the form — names the field in plain English, never mentions
    'JSON'. Falls back to the raw text for anything unrecognised."""
    # "<path> is required"
    m = re.match(r"^([\w.]+) is required$", msg)
    if m:
        label = _FIELD_LABELS.get(m.group(1), m.group(1))
        return f"{label} is required — fill it in on the form and try again."

    # "invoice.line_items[N].description is required"
    m = re.match(r"^invoice\.line_items\[(\d+)\]\.description is required$", msg)
    if m:
        return f"Line item {int(m.group(1)) + 1}: enter a description."

    # "invoice.line_items[N] needs either `amount` or both `quantity` and `unit_price`"
    m = re.match(r"^invoice\.line_items\[(\d+)\] needs", msg)
    if m:
        return (f"Line item {int(m.group(1)) + 1}: enter an amount "
                "(or a quantity and a unit price).")

    if msg == "invoice.line_items must have at least one entry":
        return "Add at least one line item before generating the invoice."

    if msg.startswith("Input must have top-level keys"):
        return ("The invoice is missing its core sections (client, job, or "
                "invoice details). Reload the form and re-enter the details.")

    if msg.startswith("invoice.retainage.scope"):
        return "Retainage scope must be either 'base' or 'all'."

    if msg.startswith("Cannot allocate retainage"):
        return ("Retainage can't be applied because the line items total zero "
                "or less. Check the line item amounts.")

    return msg


def _friendly_error(exc: Exception) -> tuple[int, str, str, str]:
    """
    Translate an exception into (status_code, code, detail, advice).

    The Claude skill consumes the {code, detail, advice} envelope to render a
    deterministic message to Andrea instead of a raw "RefreshError: ..."
    string. Match on exception type + substring of the message; fall through
    to UNEXPECTED for anything unrecognised.

    `advice` is the one line Andrea (or her Claude) should act on. `detail`
    is the raw exception message for log/debug purposes.
    """
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()

    # Gmail OAuth refresh failure (token revoked, password reset on billing@,
    # 6-month inactivity, admin revoke). Recovery: Joe re-runs `python
    # gmail.py setup` locally and rotates the secret.
    if name == "RefreshError" or "invalid_grant" in low or "token has been expired" in low:
        return (503, "GMAIL_TOKEN_EXPIRED", msg,
                "billing@ Gmail token expired or was revoked. Ask Joe to "
                "re-run `python gmail.py setup` locally and rotate the "
                "`gmail-token` secret. Stripe invoice was NOT created.")

    # Stripe API key rejected — wrong key, expired key, mode mismatch
    # (test vs live).
    if "authenticationerror" in name.lower() or "no api key provided" in low:
        return (503, "STRIPE_AUTH", msg,
                "Stripe API key rejected. Ask Joe to check the STRIPE_API_KEY "
                "secret. Stripe invoice was NOT created.")

    # Stripe idempotency collision. CAUSE: a re-run reused an idempotency key
    # (scoped to the invoice identifier) with DIFFERENT parameters — the classic
    # trigger is the office correcting a field (e.g. the client email) and
    # re-running the SAME invoice number. Stripe rejects the *first* write
    # wholesale, so NOTHING was partially created. This is safe, not a footgun:
    # the fix is a clean revision (new identifier), never a blind retry. We give
    # it its own calm, actionable message instead of the scary UNEXPECTED.
    if name == "IdempotencyError" or "idempotent requests can only be used" in low:
        return (409, "IDEMPOTENCY_CONFLICT", msg,
                "This invoice number was already used with different details, so "
                "Stripe blocked the change. Nothing was partially created — the "
                "original invoice is untouched. To correct it, use Reissue/Correct "
                "(it issues a clean revision); do not re-run the same invoice.")

    # Stripe rejected the request — bad customer, bad amount, etc. The raw
    # Stripe message is usually Andrea-readable enough to action.
    if "invalidrequesterror" in name.lower() or "stripe" in name.lower():
        return (422, "STRIPE_INVALID", msg,
                "Stripe rejected the request. Check the detail and fix the "
                "matching field on the form (e.g. a typo in the client email "
                "or a zero-amount line item).")

    # Drive permissions: the service account can't see a folder. Usually means
    # the folder is in someone's personal Drive and hasn't been shared with
    # gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com (Editor).
    if name == "HttpError" and ("404" in msg or "file not found" in low or "notfound" in low.replace(" ", "")):
        return (422, "DRIVE_NOT_FOUND", msg,
                "Drive folder or file not visible to the service account. "
                "Ask Joe to share it with "
                "gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com "
                "as Editor.")

    # Pydantic / value errors — input shape problems. Surface the actual
    # field in plain English so an office user can fix it on the form (no
    # "JSON" jargon — the portal form is the only way they touch this).
    if name == "ValueError":
        return (422, "INVALID_INPUT", msg, humanize_validation_message(msg))

    # Catch-all. CRITICAL: tell Andrea not to retry on this path — a 500 can
    # mean partial success (Stripe invoice created but Gmail draft failed, or
    # similar). Re-running on partial success is the footgun.
    return (500, "UNEXPECTED", f"{name}: {msg}",
            "Unexpected error. The invoice MAY have been partially created in "
            "Stripe. Do NOT retry. Ask Joe to check the service logs and the "
            "Stripe dashboard.")
