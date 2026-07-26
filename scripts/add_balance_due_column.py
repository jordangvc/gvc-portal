"""
One-time: create the "Balance Due" numeric column on the Invoices Sent board
(1931784889) for partial check payments, and print the column id.

The check flow syncs this column from Stripe's amount_remaining at every check
commit; the picker/matcher read it as each row's remaining balance. Until the
column exists AND its id is set via env, balance tracking is silently skipped
(full-pay behavior unchanged).

Usage (from the repo root, with MONDAY_API_TOKEN set):

    python scripts/add_balance_due_column.py

Then set the printed id on the service:

    gcloud run services update gvc-invoice --region us-central1 \
      --project gvc-invoice-system --account=hello@greenvalleycontractors.com \
      --update-env-vars GVC_MONDAY_BALANCE_DUE_COL=<column_id>

Idempotent-ish: if a "Balance Due" column already exists on the board, this
script prints its id and creates nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday.client import INVOICES_SENT_BOARD_ID, MondayClient  # noqa: E402

TITLE = "Balance Due"


def main() -> int:
    mc = MondayClient()  # raises MondayNotConfigured if MONDAY_API_TOKEN unset

    existing = mc._query(
        """query ($boardId: [ID!]) {
             boards(ids: $boardId) { columns { id title type } }
           }""",
        {"boardId": [str(INVOICES_SENT_BOARD_ID)]},
    )
    for col in (existing.get("boards") or [{}])[0].get("columns") or []:
        if (col.get("title") or "").strip().lower() == TITLE.lower():
            print(f"Column already exists: id={col['id']} (type={col.get('type')})")
            print(f"Set: GVC_MONDAY_BALANCE_DUE_COL={col['id']}")
            return 0

    data = mc._query(
        """mutation ($boardId: ID!, $title: String!) {
             create_column(board_id: $boardId, title: $title,
                           column_type: numbers,
                           description: "Remaining balance after partial check payments — synced from Stripe by the portal at each check commit.") {
               id
             }
           }""",
        {"boardId": str(INVOICES_SENT_BOARD_ID), "title": TITLE},
    )
    cid = data["create_column"]["id"]
    print(f"Created column '{TITLE}' on board {INVOICES_SENT_BOARD_ID}: id={cid}")
    print(f"Set: GVC_MONDAY_BALANCE_DUE_COL={cid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
