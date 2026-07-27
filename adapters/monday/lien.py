"""
Monday reads for the Lien Watch tracker — READ-ONLY in P1.
=========================================================================
Pulls every active job off the Projects board (1918846405) with just the
columns the deadline math needs. NO writebacks: the design's "notice status
as Projects-board columns" is a later phase; nothing in this module mutates
Monday.

Column facts (inspected live 2026-07-26 via get_board_info):
  date          "Start Date"    — the stocking/start date; sparsely filled,
                                  so item creation date is the documented
                                  fallback (basis "assumed").
  location5     "Job Location"  — free-text address; state parsed from it.
  status        "Project Type"  — labels incl. Residential / Commercial /
                                  Repair / Standard Project(s) — no Public.
  deal_stage    "Project Status"— used only to drop Lost/canceled jobs.
  text_mm4fvj91 "Project #"     — canonical portal key (display only here).

"Active" = every group except `closed` ("Completed and Paid Projects").
Completed-but-UNPAID groups stay in — that's exactly where lien rights
matter. CO rows (top-level "CO.…" items, the 2026-07-16 CO model) and
Lost/canceled jobs are skipped.
"""
from __future__ import annotations

from typing import Optional

from shared.boards import PROJECTS_BOARD_ID

# Projects-board column ids this tracker reads (see monday/client.py for the
# wider Projects map; COL_PROJECT_NUMBER lives there too but this module keeps
# its read list self-contained and read-only).
LIEN_COL_START_DATE = "date"            # "Start Date" — stocking/first-furnishing
LIEN_COL_LOCATION = "location5"         # "Job Location"
LIEN_COL_PROJECT_TYPE = "status"        # "Project Type" status
LIEN_COL_DEAL_STAGE = "deal_stage"      # "Project Status" status
LIEN_COL_PROJECT_NUMBER = "text_mm4fvj91"  # "Project #"

CLOSED_GROUP_ID = "closed"              # "Completed and Paid Projects"
SKIP_DEAL_STAGES = {"project lost/canceled"}
NOT_STARTED_GROUP_ID = "new_group25317__1"  # "New Projects (Not Started)"

_COLUMN_IDS = '["%s"]' % '", "'.join([
    LIEN_COL_START_DATE, LIEN_COL_LOCATION, LIEN_COL_PROJECT_TYPE,
    LIEN_COL_DEAL_STAGE, LIEN_COL_PROJECT_NUMBER,
])


def _item_url(item_id) -> str:
    return (f"https://greenvalleycontractors.monday.com/boards/"
            f"{PROJECTS_BOARD_ID}/pulses/{item_id}")


def fetch_active_projects(mc) -> list[dict]:
    """
    Every active job on the Projects board, normalized for the deadline math:
      {item_id, name, url, group_id, group_title, project_number,
       start_date, created_at, location, project_type_label, deal_stage}
    Paged at 200 (433 items on the board today). Read-only.
    """
    query = """
    query ($boardId: [ID!], $cursor: String) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            name
            created_at
            group { id title }
            column_values(ids: %s) { id text }
          }
        }
      }
    }
    """ % _COLUMN_IDS
    jobs: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {"boardId": [str(PROJECTS_BOARD_ID)],
                                 "cursor": cursor})
        boards = data.get("boards") or []
        if not boards:
            break
        page = boards[0]["items_page"]
        for item in page.get("items") or []:
            row = _normalize(item)
            if row is not None:
                jobs.append(row)
        cursor = page.get("cursor")
        if not cursor:
            break
    return jobs


def _normalize(item: dict) -> Optional[dict]:
    """One raw item → tracker row, or None when the item isn't a lien-relevant
    job (closed group, CO row, Lost/canceled)."""
    name = (item.get("name") or "").strip()
    group = item.get("group") or {}
    if group.get("id") == CLOSED_GROUP_ID:
        return None
    if name.startswith("CO."):        # top-level Change Order rows, not jobs
        return None

    cols = {cv["id"]: (cv.get("text") or "").strip() or None
            for cv in item.get("column_values") or []}
    deal_stage = cols.get(LIEN_COL_DEAL_STAGE)
    if (deal_stage or "").strip().lower() in SKIP_DEAL_STAGES:
        return None

    return {
        "item_id": int(item["id"]),
        "name": name,
        "url": _item_url(item["id"]),
        "group_id": group.get("id"),
        "group_title": group.get("title"),
        "project_number": cols.get(LIEN_COL_PROJECT_NUMBER),
        "start_date": cols.get(LIEN_COL_START_DATE),      # YYYY-MM-DD or None
        "created_at": item.get("created_at"),             # ISO timestamp
        "location": cols.get(LIEN_COL_LOCATION),
        "project_type_label": cols.get(LIEN_COL_PROJECT_TYPE),
        "deal_stage": deal_stage,
    }
