# Stripe `invoice.paid` → Monday Paid (v1 slice)

## Problem
Check path already marks the Invoices Sent board Paid
(`MondayClient.set_invoice_paid`). Online Stripe pay — the customer clicking
the hosted-invoice "Pay Now" link — has no webhook wired up, so that row
never updates on its own. Andrea has to notice in the Stripe dashboard and
flip the row by hand.

## Fix (this slice, deliberately narrow)
1. `adapters/monday/client.py`: `set_invoice_paid` takes an optional
   `note_line=` override so a non-check payment path can write its own note
   ("Paid via Stripe online on ...") instead of the "Paid by check #..." text.
2. `orchestrators/stripe_paid_flow.py`: given a verified Stripe event dict,
   only acts on `invoice.paid`. Matches the Monday row by
   `metadata.gvc_invoice_id` (Document #), falls back to a `stripe_invoice_id`
   scan for older rows. Already-Paid is a no-op (idempotent — safe for Stripe
   retries). No match found is a business no-op, not an error.
3. `app/service.py`: `POST /v1/webhooks/stripe` — verifies
   `Stripe-Signature` with `STRIPE_WEBHOOK_SECRET` (`stripe.Webhook.construct_event`).
   Bad/missing signature → 400 (Stripe retries). Everything else — wrong
   event type, no matching row, already Paid, even a downstream Monday write
   failure — is a 200 so Stripe doesn't retry forever on something that will
   never resolve.
4. `tests/test_stripe_paid_webhook.py` — offline, no network. Orchestrator
   tested against a fake Monday client; the route tested with a *real*
   `Stripe-Signature` header computed locally with `hmac`/`sha256` (Stripe's
   signature check is pure local HMAC verification, so this exercises the
   real auth path).

## Explicitly out of scope for v1
- PandaDoc — not used or proposed anywhere in this change.
- Projects board invoice status — never touched; this only writes the
  Invoices Sent board (same board `set_invoice_paid` already wrote to).
- Refunds, partial payments, `payment_intent.*` events — still
  `check_flow` / `subsystems.checks.deposit`'s job. The webhook route only
  acts on `invoice.paid`; every other event type is accepted and ignored
  (200) so the Stripe endpoint's event selection can be widened later
  without a code change here.

## What Jordan has to do once (see `docs/DEPLOY-IN-BROWSER.md`)
1. Create a Stripe webhook endpoint pointed at
   `https://<cloud-run-url>/v1/webhooks/stripe` for event `invoice.paid`.
2. Copy that endpoint's signing secret into the Cloud Run service as
   `STRIPE_WEBHOOK_SECRET`.

## Smoke test after deploy
Stripe dashboard → Webhooks → the new endpoint → **Send test webhook** →
`invoice.paid`. If the test invoice's Document # matches a Monday row, the
Invoices Sent row flips to Paid with a "Paid via Stripe online on ..." note;
if not, Stripe still shows a `200` (that's the intended no-op).
