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

from adapters.monday import cache as monday_cache
from shared.boards import JOBCHECK_BOARD_ID, JOBCHECK_SKIP_GROUP_IDS

# Read-only context columns shown at the top of the Job Check page (never
# editable there). OPERATIONS-board ids, verified live via get_board_info
# 2026-07-28. Several are mirrors/relations of the Projects board — fine to
# READ for context, impossible to write (see shared/boards.py).
CONTEXT_COL_PROJECT_LINK = "link_to_projects"   # → the Projects item
CONTEXT_COL_LOCATION = "lookup_mknf1rdw"        # "Job Location" (mirror)
CONTEXT_COL_PROJECT_STATUS = "mirror3"          # "Project Status" (mirror)
CONTEXT_COL_OPS_OWNER = "multiple_person_mm1ht2vj"  # "Ops. Owner"
CONTEXT_COL_OVERDUE = "color_mm1x2172"          # "Overdue" (automation-owned)

CONTEXT_COLUMN_IDS = (
    CONTEXT_COL_PROJECT_LINK, CONTEXT_COL_LOCATION, CONTEXT_COL_PROJECT_STATUS,
    CONTEXT_COL_OPS_OWNER, CONTEXT_COL_OVERDUE,
)

# A job whose parent project was lost/cancelled shouldn't be in the crew's
# picker. Read off the Project Status mirror (Operations has no deal_stage).
SKIP_PROJECT_STATUSES = {"project lost/canceled"}

# ⚠ Mirror and board-relation columns return text = NULL — their readable value
# lives in `display_value` (verified live 2026-07-28: link_to_projects/mirror3/
# lookup_mknf1rdw all had text=None while display_value carried the project
# name, status and address). Every Operations context column is one of those
# types, so both reads request this fragment and prefer display_value.
_VALUE_FRAGMENT = """
          id
          text
          ... on MirrorValue { display_value }
          ... on BoardRelationValue { display_value }
"""


def _column_text(cv: dict) -> Optional[str]:
    """Readable value of one column_value: display_value (mirrors/relations)
    falling back to text (everything else). None when empty."""
    for key in ("display_value", "text"):
        raw = cv.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _item_url(item_id) -> str:
    return (f"https://greenvalleycontractors.monday.com/boards/"
            f"{JOBCHECK_BOARD_ID}/pulses/{item_id}")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def fetch_active_jobs(mc) -> list[dict]:
    """
    Every active task on the Operations board, normalized for the picker:
      {item_id, name, url, group_id, group_title, project_number,
       location, deal_stage}
    Paged at 200. Read-only. Completed Tasks / Ready to Invoice groups and
    lost-or-cancelled projects are skipped (see JOBCHECK_SKIP_GROUP_IDS).

    Short-TTL cached (see adapters/monday/cache.py) — Job Check + Field Guide
    both hit this on every page open, and the UI searches client-side.
    """
    return monday_cache.get_or_set_swr(
        "list:jobcheck:active_jobs",
        lambda: _fetch_active_jobs_uncached(mc),
        ttl=monday_cache.list_ttl(),
        stale_ttl=monday_cache.stale_ttl(),
    )


def _fetch_active_jobs_uncached(mc) -> list[dict]:
    col_ids = json.dumps([CONTEXT_COL_PROJECT_LINK, CONTEXT_COL_LOCATION,
                          CONTEXT_COL_PROJECT_STATUS])
    query = """
    query ($boardId: [ID!], $cursor: String) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            name
            group { id title }
            column_values(ids: %s) { %s }
          }
        }
      }
    }
    """ % (col_ids, _VALUE_FRAGMENT)
    jobs: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {"boardId": [str(JOBCHECK_BOARD_ID)],
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
    """One raw item → picker row, or None when it isn't an active task."""
    name = (item.get("name") or "").strip()
    group = item.get("group") or {}
    if group.get("id") in JOBCHECK_SKIP_GROUP_IDS:
        return None
    cols = {cv["id"]: _column_text(cv) for cv in item.get("column_values") or []}
    project_status = cols.get(CONTEXT_COL_PROJECT_STATUS)
    if (project_status or "").strip().lower() in SKIP_PROJECT_STATUSES:
        return None
    return {
        "item_id": int(item["id"]),
        "name": name,
        "url": _item_url(item["id"]),
        "group_id": group.get("id"),
        "group_title": group.get("title"),
        # Kept under the same keys the flow/UI already read: the linked
        # Projects item stands in for the old "Project #".
        "project_number": cols.get(CONTEXT_COL_PROJECT_LINK),
        "location": cols.get(CONTEXT_COL_LOCATION),
        "deal_stage": project_status,
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
    data = mc._query(query, {"boardId": [str(JOBCHECK_BOARD_ID)],
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
        column_values(ids: $cols) { %s }
      }
    }
    """ % _VALUE_FRAGMENT
    data = mc._query(query, {"itemId": [str(item_id)], "cols": fetch_ids})
    items = data.get("items") or []
    if not items:
        return None
    item = items[0]
    group = item.get("group") or {}
    values = {cv["id"]: _column_text(cv) for cv in item.get("column_values") or []}
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

    variables = {"boardId": str(JOBCHECK_BOARD_ID), "itemId": str(int(item_id))}
    try:
        mc._query(_MUTATION, {**variables, "values": json.dumps(values)})
        monday_cache.invalidate("list:jobcheck:active_jobs")
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
    if written:
        monday_cache.invalidate("list:jobcheck:active_jobs")
    return {"written": sorted(written), "failed": failed}
