"""
Audit the Invoices Sent board (1931784889) for STALE STRIPE POINTERS — rows
whose Stripe Invoice ID references a voided (or missing) Stripe invoice.

Why: rows written before the server-side ledger shipped (~2026-06-25), or rows
whose invoice was voided in the Stripe dashboard and re-billed, can still point
at the dead invoice. The check-recording flow then fails with "Voided invoices
cannot be paid" (the Greg Gavin CU-0166 incident, 2026-07-06). The portal now
self-heals these at check time, but this sweep finds and fixes them up front.

For every OPEN row (status not Paid/Void) with a Stripe Invoice ID:
  - retrieve the invoice from Stripe;
  - if it is VOID (or gone), resolve the CURRENT invoice for the same
    Document # via find_current_invoice_for_identifier (customer list +
    metadata search);
  - report; with --fix, repoint the row (Stripe id + pay link + note).

Usage (from the repo root, with env MONDAY_API_TOKEN + STRIPE_API_KEY set):

    python scripts/audit_invoice_ledger.py           # report only
    python scripts/audit_invoice_ledger.py --fix     # repoint fixable rows

Read-only unless --fix. Rows without a Stripe id are listed as NO_ID (usually
skill-era rows — fix by hand if the invoice exists in Stripe).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stripe  # noqa: E402

from adapters.monday.client import MondayClient  # noqa: E402
from adapters.stripe_invoice import find_current_invoice_for_identifier  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Find (and optionally fix) Invoices-board rows pointing at voided Stripe invoices.")
    ap.add_argument("--fix", action="store_true",
                    help="Repoint fixable rows to the current Stripe invoice (default: report only).")
    args = ap.parse_args()

    api_key = os.environ.get("STRIPE_API_KEY")
    if not api_key:
        print("STRIPE_API_KEY not set.", file=sys.stderr)
        return 2
    stripe.api_key = api_key

    mc = MondayClient()  # raises MondayNotConfigured if MONDAY_API_TOKEN unset
    rows = mc.fetch_invoice_rows(open_only=True)
    print(f"Auditing {len(rows)} open rows on the Invoices board...\n")

    stale = fixed = unfixable = no_id = errors = 0
    for row in rows:
        ident = row.get("identifier") or row.get("name") or "?"
        sid = row.get("stripe_invoice_id")
        if not sid:
            no_id += 1
            print(f"NO_ID      {ident:<28} row {row.get('monday_item_id')} — no Stripe invoice id on the row")
            continue
        try:
            inv = stripe.Invoice.retrieve(sid)
            status = getattr(inv, "status", None)
            customer = getattr(inv, "customer", None)
        except Exception as e:  # noqa: BLE001 — keep sweeping
            errors += 1
            print(f"ERROR      {ident:<28} {sid} — retrieve failed: {e}")
            continue

        if status != "void":
            continue  # healthy pointer

        stale += 1
        current = find_current_invoice_for_identifier(ident, customer_id=customer)
        if not current:
            unfixable += 1
            print(f"STALE      {ident:<28} {sid} is VOID — no live replacement found "
                  f"(reissued under a new number?) — fix row {row.get('monday_item_id')} by hand")
            continue

        if args.fix:
            mc.repoint_invoice_stripe(
                row["monday_item_id"], current["id"],
                hosted_url=current.get("hosted_invoice_url"),
                note=(f"Stripe invoice repointed {sid} → {current['id']} "
                      f"(original voided; fixed by ledger audit)"))
            fixed += 1
            print(f"FIXED      {ident:<28} {sid} (void) → {current['id']} ({current.get('status')})")
        else:
            print(f"STALE      {ident:<28} {sid} is VOID → would repoint to "
                  f"{current['id']} ({current.get('status')}) — rerun with --fix")

    print(f"\nDone. stale={stale} fixed={fixed} unfixable={unfixable} "
          f"no_stripe_id={no_id} retrieve_errors={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
