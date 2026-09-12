"""
One-time repair for the five Invoices-board rows that were created name-only
between 2026-08-27 and 2026-09-10 (see CLAUDE.md, "bare ledger rows" incident).

Every fact below was taken from the portal's own run logs for each invoice
(Stripe id, Gmail draft id, Drive file id, amount, dates) and from the live
boards (row item ids, Ops → Projects links). The Stripe HOSTED URL is NOT
hand-copied — it is fetched from Stripe by invoice id at run time, which also
re-verifies that the invoice exists and is open.

DEFAULT IS DRY-RUN: prints the exact column values per row and writes nothing.
    python scripts/repair_bare_ledger_rows.py
Apply (Monday board write — needs Jordan's go):
    python scripts/repair_bare_ledger_rows.py --apply

Env: MONDAY_API_TOKEN, STRIPE_API_KEY (inject from Secret Manager; never on disk).
Writes go by ITEM ID (not Document #) because these rows have no Document # yet —
an upsert-by-identifier would create duplicates.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.monday import client as mc_mod  # noqa: E402

ROWS = [
    # identifier, ledger item id, Projects item (via Ops link_to_projects), customer name,
    # job ref, amount, issue, due, stripe invoice id, gmail compose id, drive file id
    ("2026-0827-02", 2847087296, 2788955802, "CMsquared, LLC",
     "312 Walnut St, Cincinnati OH 45202", 27189.00, "2026-08-27", "2026-09-26",
     "in_1U97CwApcln2OQNScRr8uIXx", "r782480679510984038", "1vNWs91XzpWE29e-GgWklN_dwtZys_065"),
    ("2026-0901-01", 2850541634, 2843017205, "Brad Hahn",
     "1908 S P County Rd 850 E, Greensburg IN 47240", 2200.00, "2026-09-01", "2026-10-01",
     "in_1UAstrApcln2OQNSL5SDvstK", "r-6550841431625878395", "1nKP8HKHoxpTBZU1v7Aoz7zRdXXOi20Dy"),
    ("2026-0903-01", 2852067296, 2821304758, "JDC Construction",
     "18863 Rileys Ridge, Greendale IN 47025", 26300.00, "2026-09-03", "2026-10-03",
     "in_1UBeLNApcln2OQNSqrnip9TQ", "r5209762991972380672", "1pYtdLjmXK880yw25zwxjJ1l7Cd1e-mZB"),
    ("2026-0903-02", 2852070499, 2686535838, "Wieland Builders, LLC",
     "2960 Old Line Lane, Fairfield OH 45011", 3900.00, "2026-09-03", "2026-10-03",
     "in_1UBfRDApcln2OQNS864KmJYg", "r4547863932783475886", "1wg-rN4ezqL_hDxP5j-ejTVmn5LJW-TK6"),
    ("2026-0910-01", 2856623424, 2725762273, "Willow Creek Builders - Invoicing",
     "7791 Waynetowne Blvd, Huber Heights OH 45424", 19200.00, "2026-09-10", "2026-10-10",
     "in_1UE7TiApcln2OQNS4qN5wq24", "r5187540482806895700", "1hYqNot56GvJ7833lYdkpQQ10u1mAdBng"),
]


def build_values(mc, stripe, row) -> tuple[dict, list[str]]:
    (ident, item_id, project_id, customer, job_ref, amount, issue, due,
     stripe_id, gmail_id, drive_id) = row
    notes: list[str] = []
    inv = stripe.Invoice.retrieve(stripe_id)
    if inv.status not in ("open", "paid"):
        notes.append(f"⚠ Stripe invoice {stripe_id} status={inv.status}")
    values = {
        mc_mod.INV_COL_DOCUMENT: ident,
        mc_mod.INV_COL_JOB: job_ref,
        mc_mod.INV_COL_AMOUNT: str(round(float(amount), 2)),
        mc_mod.INV_COL_ISSUE_DATE: {"date": issue},
        mc_mod.INV_COL_DUE_DATE: {"date": due},
        mc_mod.INV_COL_STRIPE_INVOICE: stripe_id,
        mc_mod.INV_COL_STRIPE_URL: {"url": inv.hosted_invoice_url, "text": "Pay invoice"},
        mc_mod.INV_COL_GMAIL: {"url": f"https://mail.google.com/mail/u/0/#drafts?compose={gmail_id}",
                               "text": "Open Gmail draft"},
        mc_mod.INV_COL_DRIVE_FOLDER: {"url": f"https://drive.google.com/file/d/{drive_id}/view",
                                      "text": "Invoice PDF"},
        mc_mod.INV_COL_LINKED_PROJECT: {"item_ids": [int(project_id)]},
        # the original create would have set this; the watcher flips it to
        # "Invoice Sent" once it finds the email in hello@ Sent
        mc_mod.INV_COL_STATUS: {"label": mc_mod.LEDGER_CREATE_STATUS_LABEL},
    }
    cust_id = mc.resolve_customer_item_id(customer)
    if cust_id:
        values[mc_mod.INV_COL_CUSTOMER] = {"item_ids": [int(cust_id)]}
    else:
        notes.append(f'Customer link: could not resolve "{customer}" — link manually.')
    if notes:
        values[mc_mod.INV_COL_NOTE] = {"text": "\n".join(notes)}
    return values, notes


def main() -> int:
    apply = "--apply" in sys.argv
    import stripe
    stripe.api_key = os.environ["STRIPE_API_KEY"]
    mc = mc_mod.MondayClient()
    print("MODE:", "APPLY — writing to Monday" if apply else "DRY RUN — no writes")
    for row in ROWS:
        ident, item_id = row[0], row[1]
        values, notes = build_values(mc, stripe, row)
        print(f"\n== {ident}  item {item_id}")
        print(json.dumps(values, indent=2, ensure_ascii=False))
        if notes:
            print("notes:", notes)
        if apply:
            dropped = mc._set_invoice_columns(item_id, values,
                                              droppable=mc_mod.LEDGER_DROPPABLE_COLUMNS)
            print("WRITTEN", "— dropped:" if dropped else "— clean", dropped or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
