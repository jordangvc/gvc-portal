"""
One-time: create the "Action Requests" Monday board (docs/MORNING_BRIEF_BUILD_SPEC.md
"Action Requests") and print the board + column ids for env configuration.

The Action Requests SoT today is GCS (subsystems/morning/action_requests.py,
portal/morning/action-requests.json) — this board is an OPTIONAL Monday mirror
for anyone who wants to see requests alongside other boards; nothing in the
portal reads/writes it yet (shared.boards.ACTION_REQUESTS_BOARD_ID defaults to
0 = "not configured", which the portal treats as GCS-only). Wiring the portal
to actually sync to this board is a later slice.

Columns created (one of each of the five column *types* the spec's build
task called for): a Status column for the request's lifecycle, a People
column for who the request is directed at, a Text column for the requester,
a Date column for the due date/time, and a Long Text column for the
plain-language need.

Usage (from the repo root, with MONDAY_API_TOKEN set):

    python scripts/create_action_requests_board.py

Then set the printed ids on the service (all optional — only needed once the
portal is wired to sync to Monday):

    gcloud run services update gvc-invoice --region us-central1 \
      --project gvc-invoice-system --account=hello@greenvalleycontractors.com \
      --update-env-vars GVC_MONDAY_ACTION_REQUESTS_BOARD_ID=<board_id>,\
GVC_MONDAY_AR_STATUS_COL=<id>,GVC_MONDAY_AR_NEEDED_FROM_COL=<id>,\
GVC_MONDAY_AR_REQUESTER_COL=<id>,GVC_MONDAY_AR_DUE_COL=<id>,GVC_MONDAY_AR_NEED_COL=<id>

Idempotent: if GVC_MONDAY_ACTION_REQUESTS_BOARD_ID is already set in the
environment, this script does nothing but print that id (per the build spec:
"Idempotent if GVC_MONDAY_ACTION_REQUESTS_BOARD_ID already set — just print").
It does NOT re-check Monday for a board named "Action Requests" — the env var
is treated as the single source of truth for "already created".

GraphQL caveats (see also docs/MORNING_BRIEF_BUILD_SPEC.md):
  - `create_board` requires `board_kind` (public/private/share); this script
    uses "public" (matches the rest of GVC's boards — no board on the
    account is currently private).
  - `workspace_id` is best-effort: this script tries the CRM workspace
    (1102536, per CLAUDE.md — the workspace Bid Board/Projects/Customers live
    in) first, and if Monday rejects it (e.g. the token's user lacks access,
    or the workspace id is wrong for this account) retries with no
    workspace_id (lands in "My workspace" instead). Either way the board is
    created; only its workspace placement is uncertain up front.
  - Status column labels are seeded via `defaults` (a JSON string on
    create_column) — Monday's schema for this is loosely documented and has
    shifted across API versions; if label seeding fails, the column is still
    created with default "Label 1..N" placeholders you'd rename by hand.
  - Monday's `create_column` does not accept a description on every column
    type in every API version — kept minimal (title + column_type only) to
    dodge that.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday.client import MondayClient  # noqa: E402

BOARD_TITLE = "Action Requests"
CRM_WORKSPACE_ID = "1102536"

ENV_BOARD_ID = "GVC_MONDAY_ACTION_REQUESTS_BOARD_ID"

# (title, column_type, env_var, defaults-json-or-None)
COLUMNS = [
    (
        "Status",
        "status",
        "GVC_MONDAY_AR_STATUS_COL",
        json.dumps({"labels": {"0": "Open", "1": "Acknowledged",
                                "2": "Completed", "3": "Needs Triage"}}),
    ),
    ("Needed From", "people", "GVC_MONDAY_AR_NEEDED_FROM_COL", None),
    ("Requester", "text", "GVC_MONDAY_AR_REQUESTER_COL", None),
    ("Due Date", "date", "GVC_MONDAY_AR_DUE_COL", None),
    ("Need", "long_text", "GVC_MONDAY_AR_NEED_COL", None),
]


def _create_board(mc: MondayClient) -> str:
    mutation = """
    mutation ($name: String!, $kind: BoardKind!, $workspaceId: ID) {
      create_board (board_name: $name, board_kind: $kind, workspace_id: $workspaceId) {
        id
      }
    }
    """
    try:
        data = mc._query(mutation, {
            "name": BOARD_TITLE, "kind": "public", "workspaceId": CRM_WORKSPACE_ID,
        })
        return data["create_board"]["id"]
    except Exception as e:  # noqa: BLE001 — workspace_id may not be usable
        print(f"[create_action_requests_board] workspace-scoped create failed "
              f"({e}); retrying without workspace_id (lands in My workspace).",
              file=sys.stderr)
        data = mc._query(mutation, {
            "name": BOARD_TITLE, "kind": "public", "workspaceId": None,
        })
        return data["create_board"]["id"]


def _create_column(mc: MondayClient, board_id: str, title: str,
                    column_type: str, defaults: str | None) -> str:
    mutation = """
    mutation ($boardId: ID!, $title: String!, $type: ColumnType!, $defaults: JSON) {
      create_column (board_id: $boardId, title: $title, column_type: $type,
                     defaults: $defaults) {
        id
      }
    }
    """
    try:
        data = mc._query(mutation, {
            "boardId": str(board_id), "title": title, "type": column_type,
            "defaults": defaults,
        })
        return data["create_column"]["id"]
    except Exception as e:  # noqa: BLE001 — defaults-JSON schema can vary
        if defaults is None:
            raise
        print(f"[create_action_requests_board] '{title}' with seeded labels "
              f"failed ({e}); retrying without defaults.", file=sys.stderr)
        data = mc._query(mutation, {
            "boardId": str(board_id), "title": title, "type": column_type,
            "defaults": None,
        })
        return data["create_column"]["id"]


def main() -> int:
    existing = (os.environ.get(ENV_BOARD_ID) or "").strip()
    if existing:
        print(f"Action Requests board already configured: {ENV_BOARD_ID}={existing}")
        print("Nothing to do (idempotent - env var already set).")
        return 0

    mc = MondayClient()  # raises MondayNotConfigured if MONDAY_API_TOKEN unset

    board_id = _create_board(mc)
    print(f"Created board '{BOARD_TITLE}': id={board_id}")

    col_ids: dict[str, str] = {}
    for title, column_type, env_var, defaults in COLUMNS:
        cid = _create_column(mc, board_id, title, column_type, defaults)
        col_ids[env_var] = cid
        print(f"  Column '{title}' ({column_type}): id={cid}")

    print()
    print("Set (all on the same --update-env-vars, comma-joined, no spaces):")
    print(f"  {ENV_BOARD_ID}={board_id}")
    for env_var, cid in col_ids.items():
        print(f"  {env_var}={cid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
