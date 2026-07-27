"""
Monday reads + the SINGLE write path for the Job Check tool.
=========================================================================
The portal's first write surface into Monday (docs/portal-job-check-design.md,
2026-07-27). Three reads and ONE write, all against the Projects board
(1918846405):

  fetch_active_jobs()   — paged dropdown fetch, same pattern/filters as
                          adapters/monday/lien.py (every group except
                          `closed`, skip top-level "CO." rows and
                          Lost/canceled jobs).
  get_board_columns()   — column metadata (title/type/status labels+colors)
                          for the allowlisted ids, so the form can render
                          tap-to-cycle chips and the validator can check
                          labels server-side.
  get_item_values()     — one item's current values for the allowlisted
                          columns + the read-only context header fields.
  set_item_columns()    — THE write: change_multiple_column_values on ONE
                          existing item with an already-validated dict of
                          column values. Batch first; on failure retries
                          per-column so the caller gets per-column errors
                          instead of one opaque batch failure. NEVER creates
                          or deletes items.

Guardrail: this module trusts its caller (orchestrators/jobcheck_flow) to
have validated values against the shared/boards.py allowlist — but it still
refuses an empty/None item id and never carries a create/delete mutation.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from shared.boards import PROJECTS_BOARD_ID

# Read-only context columns shown at the top of the Job Check page (never
# editable there). Ids verified live via get_board_info 2026-07-27.
CONTEXT_COL_PROJECT_NUMBER = "text_mm4fvj91"   # "Project #"
CONTEXT_COL_LOCATION = "location5"             # "Job Location"
CONTEXT_COL_BUILDER = "text"                   # "Who is the Builder?"
CONTEXT_COL_SUPERVISOR = "text5"               # "Who is the Supervisor?"
CONTEXT_COL_DEAL_STAGE = "deal_stage"          # "Project Status"
CONTEXT_COL_PROJECT_TYPE = "status"            # "Project Type"

CONTEXT_COLUMN_IDS = (
    CONTEXT_COL_PROJECT_NUMBER, CONTEXT_COL_LOCATION, CONTEXT_COL_BUILDER,
    CONTEXT_COL_SUPERVISOR, CONTEXT_COL_DEAL_STAGE, CONTEXT_COL_PROJECT_TYPE,
)

# Same active-job filters as the Lien Watch fetch (adapters/monday/lien.py).
CLOSED_GROUP_ID = "closed"                     # "Completed and Paid Projects"
SKIP_DEAL_STAGES = {"project lost/canceled"}


def _item_url(item_id) -> str:
    return (f"https://greenvalleycontractors.monday.com/boards/"
            f"{PROJECTS_BOARD_ID}/pulses/{item_id}")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def fetch_active_jobs(mc) -> list[dict]:
    """
    Every active job on the Projects board, normalized for the dropdown:
      {item_id, name, url, group_id, group_title, project_number,
       location, deal_stage}
    Paged at 200. Read-only. Filters mirror lien.py: the closed group,
    top-level "CO." rows, and Lost/canceled jobs are skipped.
    """
    col_ids = json.dumps([CONTEXT_COL_PROJECT_NUMBER, CONTEXT_COL_LOCATION,
                          CONTEXT_COL_DEAL_STAGE])
    query = """
    query ($boardId: [ID!], $cursor: String) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            name
            group { id title }
            column_values(ids: %s) { id text }
          }
        }
      }
    }
    """ % col_ids
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
            row = _normalize_job(item)
            if row is not None:
                jobs.append(row)
        cursor = page.get("cursor")
        if not cursor:
            break
    return jobs


def _normalize_job(item: dict) -> Optional[dict]:
    """One raw item → dropdown row, or None when it isn't an active job."""
    name = (item.get("name") or "").strip()
    group = item.get("group") or {}
    if group.get("id") == CLOSED_GROUP_ID:
        return None
    if name.startswith("CO."):        # top-level Change Order rows, not jobs
        return None
    cols = {cv["id"]: (cv.get("text") or "").strip() or None
            for cv in item.get("column_values") or []}
    deal_stage = cols.get(CONTEXT_COL_DEAL_STAGE)
    if (deal_stage or "").strip().lower() in SKIP_DEAL_STAGES:
        return None
    return {
        "item_id": int(item["id"]),
        "name": name,
        "url": _item_url(item["id"]),
        "group_id": group.get("id"),
        "group_title": group.get("title"),
        "project_number": cols.get(CONTEXT_COL_PROJECT_NUMBER),
        "location": cols.get(CONTEXT_COL_LOCATION),
        "deal_stage": deal_stage,
    }


def get_board_columns(mc, column_ids: list[str]) -> dict[str, dict]:
    """
    Column metadata for the given Projects-board column ids:
      {col_id: {id, title, type, labels: [{label, hex}, ...]}}
    `labels` is populated for status columns only, in the board's display
    order (the form's tap-to-cycle order); deactivated labels are dropped.
    """
    query = """
    query ($boardId: [ID!], $cols: [String!]) {
      boards(ids: $boardId) {
        columns(ids: $cols) { id title type settings_str }
      }
    }
    """
    data = mc._query(query, {"boardId": [str(PROJECTS_BOARD_ID)],
                             "cols": list(column_ids)})
    out: dict[str, dict] = {}
    for board in data.get("boards") or []:
        for col in board.get("columns") or []:
            meta = {"id": col["id"], "title": col.get("title") or col["id"],
                    "type": col.get("type") or ""}
            if meta["type"] == "status":
                meta["labels"] = parse_status_labels(col.get("settings_str"))
            out[col["id"]] = meta
    return out


def parse_status_labels(settings_str: Optional[str]) -> list[dict]:
    """
    PURE. Status-column settings JSON → ordered [{label, hex}, ...].
    Handles both shapes Monday emits:
      - classic: {"labels": {"0": "Working on it"},
                  "labels_colors": {"0": {"color": "#fdab3d"}},
                  "labels_positions_v2": {"0": 0, ...}}
      - list:    {"labels": [{"id": 0, "label": "x", "index": 1,
                              "hex": "#...", "is_deactivated": false}]}
    Order = board display order (positions/index) so the form's tap-to-cycle
    walks the same sequence the team sees on Monday. Deactivated labels drop.
    """
    if not settings_str:
        return []
    try:
        settings = json.loads(settings_str)
    except (json.JSONDecodeError, TypeError):
        return []
    labels = settings.get("labels")
    if isinstance(labels, list):
        rows = [l for l in labels
                if (l.get("label") or "").strip() and not l.get("is_deactivated")]
        rows.sort(key=lambda l: (l.get("index") is None, l.get("index", 0)))
        return [{"label": l["label"].strip(), "hex": l.get("hex") or ""}
                for l in rows]
    if isinstance(labels, dict):
        colors = settings.get("labels_colors") or {}
        positions = settings.get("labels_positions_v2") or {}
        keys = [k for k, v in labels.items() if (v or "").strip()]

        def _pos(k: str):
            p = positions.get(k)
            if isinstance(p, (int, float)):
                return (0, p)
            return (1, int(k) if str(k).lstrip("-").isdigit() else 0)

        keys.sort(key=_pos)
        return [{"label": labels[k].strip(),
                 "hex": (colors.get(k) or {}).get("color") or ""}
                for k in keys]
    return []


def get_item_values(mc, item_id: int, column_ids: list[str]) -> Optional[dict]:
    """
    ONE item's current values for `column_ids` plus the context header
    columns. Returns
      {item_id, name, url, group_id, group_title,
       values: {col_id: text-or-None}}
    or None when the item doesn't exist. Read-only.
    """
    fetch_ids = list(dict.fromkeys([*column_ids, *CONTEXT_COLUMN_IDS]))
    query = """
    query ($itemId: [ID!], $cols: [String!]) {
      items(ids: $itemId) {
        id
        name
        group { id title }
        column_values(ids: $cols) { id text }
      }
    }
    """
    data = mc._query(query, {"itemId": [str(item_id)], "cols": fetch_ids})
    items = data.get("items") or []
    if not items:
        return None
    item = items[0]
    group = item.get("group") or {}
    values = {cv["id"]: ((cv.get("text") or "").strip() or None)
              for cv in item.get("column_values") or []}
    return {
        "item_id": int(item["id"]),
        "name": (item.get("name") or "").strip(),
        "url": _item_url(item["id"]),
        "group_id": group.get("id"),
        "group_title": group.get("title"),
        "values": values,
    }


# ---------------------------------------------------------------------------
# THE write — column updates on one existing item, nothing else
# ---------------------------------------------------------------------------

_MUTATION = """
mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
  change_multiple_column_values(
    board_id: $boardId,
    item_id: $itemId,
    column_values: $values
  ) { id }
}
"""


def set_item_columns(mc, item_id: int, values: dict[str, Any]) -> dict:
    """
    Write an already-validated {col_id: api_value} dict to ONE Projects-board
    item via change_multiple_column_values (the client's existing convention —
    see MondayClient.writeback / _set_invoice_columns).

    One batch mutation first. If the batch fails, each column is retried on
    its own so the caller learns exactly which columns didn't land instead of
    getting one opaque error. Returns
      {written: [col_id, ...], failed: {col_id: error-message}}
    Never creates or deletes items; never touches any other item.
    """
    if not item_id:
        raise ValueError("set_item_columns: item_id is required")
    if not values:
        return {"written": [], "failed": {}}

    variables = {"boardId": str(PROJECTS_BOARD_ID), "itemId": str(int(item_id))}
    try:
        mc._query(_MUTATION, {**variables, "values": json.dumps(values)})
        return {"written": sorted(values), "failed": {}}
    except Exception as batch_err:  # noqa: BLE001 — fall through to per-column
        batch_msg = f"{type(batch_err).__name__}: {batch_err}"

    written: list[str] = []
    failed: dict[str, str] = {}
    for col_id, value in values.items():
        try:
            mc._query(_MUTATION,
                      {**variables, "values": json.dumps({col_id: value})})
            written.append(col_id)
        except Exception as e:  # noqa: BLE001 — report per column
            failed[col_id] = f"{type(e).__name__}: {e}"
    if not written and not failed:
        failed["_batch"] = batch_msg
    return {"written": sorted(written), "failed": failed}
