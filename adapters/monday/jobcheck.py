"""
Monday reads + write paths for the Job Check tool.
=========================================================================
The portal's first write surface into Monday (docs/portal-job-check-design.md,
2026-07-27). Reads + two explicit writes, all against the Operations board
(1920364853) unless noted:

  fetch_active_jobs()   — paged dropdown fetch (skips Completed / Ready to
                          Invoice groups).
  get_board_columns()   — column metadata (title/type/status labels+colors)
                          for the allowlisted ids, so the form can render
                          tap-to-cycle chips and the validator can check
                          labels server-side.
  get_item_values()     — one item's current values for the allowlisted
                          columns + the read-only context header fields.
  set_item_columns()    — allowlisted column write: change_multiple_column_values
                          on ONE existing item. Batch first; on failure retries
                          per-column. NEVER creates or deletes items.
  move_ops_item_to_ready_to_invoice()
                        — explicit "Mark Ready to Invoice" move into
                          group_mm3zq4q2 (+ optional Ready for Invoice Date).
                          Dedicated path — never auto-fired from Save.

Guardrail: this module trusts its caller (orchestrators/jobcheck_flow) to
have validated values against the shared/boards.py allowlist — but it still
refuses an empty/None item id and never carries a create/delete mutation.
"""
from __future__ import annotations

import json
from datetime import date as _date
from typing import Any, Optional

from adapters.drive import folder_id_from_url
from adapters.monday import cache as monday_cache
from shared.boards import (
    JOBCHECK_BOARD_ID,
    JOBCHECK_SKIP_GROUP_IDS,
    JOBSTART_BID_PROJECT_LINK_COL,
    MORNING_BOARD_ID,
    OPERATIONS_BOARD_ID,
    PROJECTS_BOARD_ID,
    PROJECTS_GFOLDER_COL,
)

# Same ids as adapters/monday/billing — keep Billing Hub's Ready queue in sync.
READY_TO_INVOICE_GROUP_ID = "group_mm3zq4q2"
OPS_COL_READY_DATE = "date_mm3zry96"  # hard-excluded from form saves; stamped here only

# Read-only context columns shown at the top of the Job Check page (never
# editable there). OPERATIONS-board ids, verified live via get_board_info
# 2026-07-28. Several are mirrors/relations of the Projects board — fine to
# READ for context, impossible to write (see shared/boards.py).
CONTEXT_COL_PROJECT_LINK = "link_to_projects"   # → the Projects item
CONTEXT_COL_LOCATION = "lookup_mknf1rdw"        # "Job Location" (mirror)
CONTEXT_COL_PROJECT_STATUS = "mirror3"          # "Project Status" (mirror)
CONTEXT_COL_OPS_OWNER = "multiple_person_mm1ht2vj"  # "Ops. Owner"
CONTEXT_COL_OVERDUE = "color_mm1x2172"          # "Overdue" (automation-owned)
CONTEXT_COL_PROGRESS = "lookup_mkpeqd8w"        # mirrored Progress (read-only)

CONTEXT_COLUMN_IDS = (
    CONTEXT_COL_PROJECT_LINK, CONTEXT_COL_LOCATION, CONTEXT_COL_PROJECT_STATUS,
    CONTEXT_COL_OPS_OWNER, CONTEXT_COL_OVERDUE, CONTEXT_COL_PROGRESS,
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

    When Morning and Job Check share the same board (default), the uncached
    path reshapes Morning's Ops walk — one GraphQL pagination serves both
    tools (same pattern as billing accepted_bids ← jobstart bids).
    """
    return monday_cache.get_or_set_swr(
        "list:jobcheck:active_jobs",
        lambda: _fetch_active_jobs_uncached(mc),
        ttl=monday_cache.list_ttl(),
        stale_ttl=monday_cache.stale_ttl(),
    )


def shape_active_job_from_morning_row(row: dict) -> Optional[dict]:
    """
    Morning Ops row → Job Check picker keys. Membership already filtered by
    Morning; this only renames fields the Job Check UI expects.
    """
    if not row or not row.get("item_id"):
        return None
    return {
        "item_id": int(row["item_id"]),
        "name": row.get("name") or "",
        "url": row.get("url") or _item_url(row["item_id"]),
        "group_id": row.get("group_id"),
        "group_title": row.get("group_title"),
        # Morning stores the linked Projects display under project_name.
        "project_number": row.get("project_name"),
        "location": row.get("location"),
        "deal_stage": row.get("project_status"),
    }


def _ops_list_cache_keys() -> tuple[str, ...]:
    """Caches that share the active Ops membership set."""
    return ("list:jobcheck:active_jobs", "list:morning:ops_items")


def _fetch_active_jobs_uncached(mc) -> list[dict]:
    # Same board as Morning (default) → reshape Morning's fat walk. Divergent
    # env overrides keep the lean Job Check pagination so neither tool sees
    # the wrong board.
    if int(JOBCHECK_BOARD_ID) == int(MORNING_BOARD_ID):
        from adapters.monday import morning as monday_morning
        rows = monday_morning.fetch_ops_items(mc)
        out: list[dict] = []
        for row in rows or []:
            shaped = shape_active_job_from_morning_row(row)
            if shaped is not None:
                out.append(shaped)
        return out

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


def get_board_columns(mc, column_ids: list[str],
                      board_id: Optional[int] = None) -> dict[str, dict]:
    """
    Column metadata for the given column ids on `board_id` (defaults to the
    Job Check / Operations board):
      {col_id: {id, title, type, labels: [{label, hex}, ...]}}
    `labels` is populated for status columns only, in the board's display
    order (the form's tap-to-cycle order); deactivated labels are dropped.
    Pass PROJECTS_BOARD_ID when reading Projects trade-status columns —
    status_19 (and friends) mean different things per board.
    """
    query = """
    query ($boardId: [ID!], $cols: [String!]) {
      boards(ids: $boardId) {
        columns(ids: $cols) { id title type settings_str }
      }
    }
    """
    bid = int(board_id) if board_id is not None else JOBCHECK_BOARD_ID
    data = mc._query(query, {"boardId": [str(bid)],
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

_MOVE_GROUP = """
mutation ($itemId: ID!, $groupId: String!) {
  move_item_to_group(item_id: $itemId, group_id: $groupId) { id }
}
"""


def move_ops_item_to_ready_to_invoice(
    mc, item_id: int, *,
    ready_date: Optional[str] = None,
    current_group_id: Optional[str] = None,
) -> dict:
    """
    Move one Operations item into the Ready to Invoice group and stamp
    Ready for Invoice Date (date_mm3zry96).

    Dedicated path for the explicit "Mark Ready to Invoice" tap — does NOT
    go through the Job Check allowlist (that date column stays hard-excluded
    from normal form saves). Never creates/deletes items. Never auto-moves.

    Returns {
      ok, group_moved, date_written, already_ready,
      group_id, ready_date, error?, date_error?
    }
    """
    out: dict = {
        "ok": False,
        "group_moved": False,
        "date_written": False,
        "already_ready": False,
        "group_id": READY_TO_INVOICE_GROUP_ID,
        "ready_date": None,
        "error": None,
        "date_error": None,
    }
    if not item_id:
        out["error"] = "item_id is required"
        return out
    item_id = int(item_id)
    today = (ready_date or _date.today().isoformat()).strip()[:10]
    out["ready_date"] = today

    if (current_group_id or "") == READY_TO_INVOICE_GROUP_ID:
        out["ok"] = True
        out["already_ready"] = True
        return out

    try:
        mc._query(_MOVE_GROUP, {
            "itemId": str(item_id),
            "groupId": READY_TO_INVOICE_GROUP_ID,
        })
        out["group_moved"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"move failed: {type(e).__name__}: {e}"
        return out

    # Stamp ready date via this dedicated mutation — not set_item_columns /
    # not the form allowlist. Best-effort: the group move is load-bearing.
    board_id = JOBCHECK_BOARD_ID or OPERATIONS_BOARD_ID
    try:
        mc._query(_MUTATION, {
            "boardId": str(board_id),
            "itemId": str(item_id),
            "values": json.dumps({OPS_COL_READY_DATE: {"date": today}}),
        })
        out["date_written"] = True
    except Exception as e:  # noqa: BLE001
        out["date_error"] = f"{type(e).__name__}: {e}"

    monday_cache.invalidate(
        "list:jobcheck:active_jobs",
        "list:morning:ops_items",
        "list:billing:ready_to_invoice",
    )
    out["ok"] = True
    return out


def set_item_columns(mc, item_id: int, values: dict[str, Any],
                     board_id: Optional[int] = None) -> dict:
    """
    Write an already-validated {col_id: api_value} dict to ONE item on
    `board_id` (defaults to the Job Check / Operations board) via
    change_multiple_column_values (the client's existing convention —
    see MondayClient.writeback / _set_invoice_columns).

    One batch mutation first. If the batch fails, each column is retried on
    its own so the caller learns exactly which columns didn't land instead of
    getting one opaque error. Returns
      {written: [col_id, ...], failed: {col_id: error-message}}
    Never creates or deletes items; never touches any other item.
    Pass PROJECTS_BOARD_ID when writing Projects trade-status columns.
    """
    if not item_id:
        raise ValueError("set_item_columns: item_id is required")
    if not values:
        return {"written": [], "failed": {}}

    bid = int(board_id) if board_id is not None else JOBCHECK_BOARD_ID
    variables = {"boardId": str(bid), "itemId": str(int(item_id))}
    try:
        mc._query(_MUTATION, {**variables, "values": json.dumps(values)})
        if bid == JOBCHECK_BOARD_ID:
            monday_cache.invalidate("list:jobcheck:active_jobs", "list:morning:ops_items")
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
    if written and bid == JOBCHECK_BOARD_ID:
        monday_cache.invalidate("list:jobcheck:active_jobs", "list:morning:ops_items")
    return {"written": sorted(written), "failed": failed}


# ---------------------------------------------------------------------------
# Monday updates (view + post) — Operations item only; never auto-changes Stage
# ---------------------------------------------------------------------------

def list_item_updates(mc, item_id: int, *, limit: int = 25) -> list[dict]:
    """
    Recent updates on ONE Ops item, newest first:
      [{id, body, created_at, creator_name, creator_email}, ...]
    Adapted from adapters/monday/jobstart.fetch_item_updates (structured for UI).
    Read-only.
    """
    if not item_id:
        return []
    query = """
    query ($itemIds: [ID!], $limit: Int!) {
      items(ids: $itemIds) {
        id
        updates(limit: $limit) {
          id
          body
          created_at
          creator { id name email }
        }
      }
    }
    """
    data = mc._query(query, {"itemIds": [str(int(item_id))], "limit": int(limit)})
    rows: list[dict] = []
    for item in data.get("items") or []:
        for upd in item.get("updates") or []:
            body = (upd.get("body") or "").strip()
            if not body:
                continue
            creator = upd.get("creator") or {}
            rows.append({
                "id": str(upd.get("id") or ""),
                "body": body,
                "created_at": upd.get("created_at") or "",
                "creator_name": (creator.get("name") or "").strip() or None,
                "creator_email": (creator.get("email") or "").strip() or None,
            })
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows


def create_item_update(mc, item_id: int, body: str) -> dict:
    """
    Post a Monday update on an Operations item. Does NOT touch Stage or any
    column — updates only. Raises ValueError on empty body.
    """
    body = (body or "").strip()
    if not body:
        raise ValueError("update body required")
    query = """
    mutation ($itemId: ID!, $body: String!) {
      create_update (item_id: $itemId, body: $body) { id }
    }
    """
    data = mc._query(query, {"itemId": str(int(item_id)), "body": body})
    return data.get("create_update") or {}


def _link_column_url(cv: dict) -> Optional[str]:
    """
    Monday Link columns store the real URL in LinkValue.url / value JSON.
    Column `text` is often just the label ("GFolder") — not usable for Drive.
    """
    url = (cv.get("url") or "").strip()
    if url.startswith("http") or "/folders/" in url:
        return url
    raw = cv.get("value")
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                u = (parsed.get("url") or "").strip()
                if u:
                    return u
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    text = (cv.get("text") or "").strip()
    if text and ("/folders/" in text or text.startswith("http")
                 or (len(text) >= 10 and " " not in text and "/" not in text
                     and text.lower() != "gfolder")):
        return text
    return None


def photo_ready_status(ginfo: dict) -> dict:
    """
    Map get_linked_project_gfolder() (or get_project_gfolder) output into a
    photo-upload readiness verdict. Pure — no I/O. `photo_ready` is True only
    when a concrete Drive folder_id is present.
    """
    ginfo = ginfo or {}
    folder_id = ginfo.get("folder_id")
    return {
        "has_project_link": bool(ginfo.get("project_item_id")),
        "has_gfolder": bool(ginfo.get("gfolder_url")),
        "gfolder_url": ginfo.get("gfolder_url"),
        "project_item_id": ginfo.get("project_item_id"),
        "folder_id": folder_id,
        "photo_ready": bool(folder_id),
        "photo_block_reason": (
            None if folder_id
            else (ginfo.get("error") or "Drive folder not linked.")
        ),
        # Alias kept for callers that still read `.reason`.
        "reason": (
            None if folder_id
            else (ginfo.get("error") or "Drive folder not linked.")
        ),
    }


def set_projects_gfolder_if_empty(
    mc, project_item_id: int, folder_url: str, *, text: str = "GFolder",
) -> dict:
    """
    Fill-if-empty write to Projects GFolder Link (link_mkwr6ef9 /
    PROJECTS_GFOLDER_COL). Never overwrites a non-empty GFolder Link.

    Returns {ok, written: bool, skipped: bool, reason?, gfolder_url, error?}.
    Bare Drive folder IDs are wrapped into a folders/ URL.
    """
    url = (folder_url or "").strip()
    out: dict = {
        "ok": False,
        "written": False,
        "skipped": False,
        "gfolder_url": None,
    }
    if not project_item_id:
        out["reason"] = "No Projects item id."
        out["error"] = out["reason"]
        return out
    if not url:
        out["reason"] = "No Drive folder URL."
        out["error"] = out["reason"]
        return out
    if "/folders/" not in url and not url.startswith("http"):
        if " " not in url and "/" not in url and len(url) >= 10:
            url = f"https://drive.google.com/drive/folders/{url}"
        else:
            out["reason"] = "folder_url required"
            out["error"] = out["reason"]
            return out
    out["gfolder_url"] = url

    gcol = PROJECTS_GFOLDER_COL
    read_q = """
    query ($ids: [ID!], $cols: [String!]) {
      items(ids: $ids) {
        id
        column_values(ids: $cols) {
          id
          text
          value
          ... on LinkValue { url text }
        }
      }
    }
    """
    try:
        data = mc._query(read_q, {
            "ids": [str(int(project_item_id))],
            "cols": [gcol],
        })
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"read failed: {type(e).__name__}: {e}"
        out["error"] = out["reason"]
        return out
    items = data.get("items") or []
    if not items:
        out["reason"] = f"Projects item {project_item_id} not found."
        out["error"] = out["reason"]
        return out
    existing = None
    for cv in items[0].get("column_values") or []:
        if cv.get("id") == gcol:
            existing = _link_column_url(cv)
            break
    if existing:
        out["ok"] = True
        out["skipped"] = True
        out["reason"] = "GFolder Link already set."
        out["gfolder_url"] = existing
        return out

    values = {gcol: {"url": url, "text": text or "GFolder"}}
    write_q = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(
        board_id: $boardId,
        item_id: $itemId,
        column_values: $values
      ) { id }
    }
    """
    try:
        mc._query(write_q, {
            "boardId": str(PROJECTS_BOARD_ID),
            "itemId": str(int(project_item_id)),
            "values": json.dumps(values),
        })
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"write failed: {type(e).__name__}: {e}"
        out["error"] = out["reason"]
        return out
    out["ok"] = True
    out["written"] = True
    out["gfolder_url"] = url
    return out


def set_ops_project_link_if_empty(mc, ops_item_id: int,
                                  project_item_id: int) -> dict:
    """
    Fill-if-empty write of Operations `link_to_projects` → one Projects item.

    NEVER overwrites an existing relation. Returns
      {ok, written, skipped, project_item_id, reason?, error?}
    """
    if not ops_item_id or not project_item_id:
        return {"ok": False, "written": False, "skipped": False,
                "project_item_id": None, "error": "ops + project ids required"}
    col = CONTEXT_COL_PROJECT_LINK
    q = """
    query ($ids: [ID!], $cols: [String!]) {
      items(ids: $ids) {
        id
        column_values(ids: $cols) {
          id
          text
          ... on BoardRelationValue {
            linked_item_ids
            linked_items { id }
          }
        }
      }
    }
    """
    try:
        data = mc._query(q, {"ids": [str(int(ops_item_id))], "cols": [col]})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "written": False, "skipped": False,
                "project_item_id": None,
                "error": f"read failed: {type(e).__name__}: {e}"}
    items = data.get("items") or []
    if not items:
        return {"ok": False, "written": False, "skipped": False,
                "project_item_id": None,
                "error": f"Operations item {ops_item_id} not found"}
    linked: list[int] = []
    for cv in items[0].get("column_values") or []:
        if cv.get("id") != col:
            continue
        linked = [int(x) for x in (cv.get("linked_item_ids") or []) if x]
        if not linked:
            linked = [int(x["id"]) for x in (cv.get("linked_items") or [])
                      if x and x.get("id")]
        break
    if linked:
        return {"ok": True, "written": False, "skipped": True,
                "project_item_id": linked[0], "reason": "already_set"}

    values = {col: {"item_ids": [int(project_item_id)]}}
    mutation = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(
        board_id: $boardId, item_id: $itemId, column_values: $values
      ) { id }
    }
    """
    board_id = JOBCHECK_BOARD_ID or OPERATIONS_BOARD_ID
    try:
        mc._query(mutation, {
            "boardId": str(board_id),
            "itemId": str(int(ops_item_id)),
            "values": json.dumps(values),
        })
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "written": False, "skipped": False,
                "project_item_id": int(project_item_id),
                "error": f"write failed: {type(e).__name__}: {e}"}
    monday_cache.invalidate("list:jobcheck:active_jobs", "list:morning:ops_items")
    return {"ok": True, "written": True, "skipped": False,
            "project_item_id": int(project_item_id)}


def get_linked_project_id(mc, ops_item_id: int) -> dict:
    """
    Resolve Ops item → linked Projects item via `link_to_projects`.

    Returns {project_item_id, error?}. `error` is a clear human message when
    the relation is empty or unreadable. Never raises for missing data —
    photo upload and trade-status writes both need this link.
    """
    out: dict = {"project_item_id": None, "error": None}
    col = CONTEXT_COL_PROJECT_LINK
    q1 = """
    query ($ids: [ID!], $cols: [String!]) {
      items(ids: $ids) {
        id
        column_values(ids: $cols) {
          id
          text
          ... on BoardRelationValue {
            linked_item_ids
            linked_items { id }
          }
        }
      }
    }
    """
    try:
        data = mc._query(q1, {"ids": [str(int(ops_item_id))], "cols": [col]})
    except Exception as e:  # noqa: BLE001
        out["error"] = f"Couldn't read the linked project ({type(e).__name__})."
        return out
    items = data.get("items") or []
    if not items:
        out["error"] = f"Operations item {ops_item_id} not found."
        return out
    linked_ids: list[str] = []
    for cv in items[0].get("column_values") or []:
        if cv.get("id") != col:
            continue
        linked_ids = [str(x) for x in (cv.get("linked_item_ids") or []) if x]
        if not linked_ids:
            linked_ids = [
                str(x.get("id")) for x in (cv.get("linked_items") or [])
                if x and x.get("id")
            ]
        break
    if not linked_ids:
        out["error"] = ("No linked Projects item on this Operations task "
                        "(link_to_projects is empty). Use “Link a Projects "
                        "item” on Job Check before editing trade status or "
                        "uploading photos.")
        return out
    out["project_item_id"] = int(linked_ids[0])
    return out


def get_linked_project_gfolder(mc, ops_item_id: int) -> dict:
    """
    Resolve Ops item → linked Projects item → GFolder Link (link_mkwr6ef9).

    Returns {
      gfolder_url, folder_id, project_item_id, error?
    }
    `error` is a clear human message when the chain is incomplete (no linked
    project, missing GFolder, or unparseable URL). Never raises for missing
    data — callers decide whether to fail hard (photo upload) or warn.
    """
    link = get_linked_project_id(mc, ops_item_id)
    out: dict = {
        "gfolder_url": None,
        "folder_id": None,
        "project_item_id": link.get("project_item_id"),
        "error": link.get("error"),
    }
    if out["error"] or not out["project_item_id"]:
        # Keep photo-upload wording that also mentions GFolder when the link
        # itself is missing (callers surface `error` as-is).
        if out["error"] and "link_to_projects is empty" in out["error"]:
            out["error"] = ("No linked Projects item on this Operations task "
                            "(link_to_projects is empty). Link the Projects item "
                            "in Monday, and make sure that Projects row has a "
                            "GFolder Link.")
        return out

    gcol = PROJECTS_GFOLDER_COL
    linked_ids = [str(out["project_item_id"])]
    q2 = """
    query ($ids: [ID!], $cols: [String!]) {
      items(ids: $ids) {
        id
        column_values(ids: $cols) {
          id
          text
          value
          ... on LinkValue { url text }
        }
      }
    }
    """
    try:
        data2 = mc._query(q2, {"ids": linked_ids[:3], "cols": [gcol]})
    except Exception as e:  # noqa: BLE001
        out["error"] = f"Couldn't read GFolder Link ({type(e).__name__})."
        return out
    gurl = None
    for it in data2.get("items") or []:
        for cv in it.get("column_values") or []:
            if cv.get("id") != gcol:
                continue
            gurl = _link_column_url(cv)
            if gurl:
                out["project_item_id"] = int(it["id"])
                break
        if gurl:
            break
    if not gurl:
        out["error"] = ("No GFolder Link on the linked Projects item. "
                        "Ask office to paste the project Drive folder URL "
                        "into Monday's GFolder Link column.")
        return out
    out["gfolder_url"] = gurl
    fid = folder_id_from_url(gurl)
    if not fid:
        out["error"] = (f"GFolder Link isn't a recognizable Drive folder URL: "
                        f"{gurl!r}")
        return out
    out["folder_id"] = fid
    return out


def get_project_gfolder(mc, project_item_id: int) -> dict:
    """
    Read GFolder Link on one Projects item. Same return shape as
    get_linked_project_gfolder (minus the Ops→Projects hop).
    """
    out: dict = {
        "gfolder_url": None,
        "folder_id": None,
        "project_item_id": int(project_item_id),
        "error": None,
    }
    gcol = PROJECTS_GFOLDER_COL
    q = """
    query ($ids: [ID!], $cols: [String!]) {
      items(ids: $ids) {
        id
        column_values(ids: $cols) {
          id
          text
          value
          ... on LinkValue { url text }
        }
      }
    }
    """
    try:
        data = mc._query(q, {"ids": [str(int(project_item_id))], "cols": [gcol]})
    except Exception as e:  # noqa: BLE001
        out["error"] = f"Couldn't read GFolder Link ({type(e).__name__})."
        return out
    items = data.get("items") or []
    if not items:
        out["error"] = f"Projects item {project_item_id} not found."
        return out
    gurl = None
    for cv in items[0].get("column_values") or []:
        if cv.get("id") == gcol:
            gurl = _link_column_url(cv)
            break
    if not gurl:
        out["error"] = ("No GFolder Link on this Projects item. "
                        "Ask office to paste the project Drive folder URL "
                        "into Monday's GFolder Link column.")
        return out
    out["gfolder_url"] = gurl
    fid = folder_id_from_url(gurl)
    if not fid:
        out["error"] = (f"GFolder Link isn't a recognizable Drive folder URL: "
                        f"{gurl!r}")
        return out
    out["folder_id"] = fid
    return out


def linked_project_ids_from_bid(mc, bid_item_id: int) -> list[int]:
    """
    Read Bid Board connect_boards4 ("Projects") → linked Projects item ids.
    Empty list when the relation is blank or the item is missing. Never raises
    for empty data.
    """
    col = JOBSTART_BID_PROJECT_LINK_COL
    query = """
    query ($ids: [ID!], $cols: [String!]) {
      items(ids: $ids) {
        id
        column_values(ids: $cols) {
          id
          ... on BoardRelationValue {
            linked_item_ids
            linked_items { id }
          }
        }
      }
    }
    """
    data = mc._query(query, {"ids": [str(int(bid_item_id))], "cols": [col]})
    items = data.get("items") or []
    if not items:
        return []
    out: list[int] = []
    for cv in items[0].get("column_values") or []:
        if cv.get("id") != col:
            continue
        for x in (cv.get("linked_item_ids") or []):
            if x:
                out.append(int(x))
        if not out:
            for li in (cv.get("linked_items") or []):
                if li and li.get("id"):
                    out.append(int(li["id"]))
        break
    return out
