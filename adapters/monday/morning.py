"""
Monday reads for the Morning Brief (employee daily control center).
=========================================================================
Slice 1 (2026-08-03): READ-ONLY Operations-board fetch for the private brief.
Writes, Action Requests, routes, Drive photos, and Fireflies come later —
see docs/MORNING_BRIEF_BUILD_SPEC.md.

Reuses Job Check's board + skip-group rules so the crew sees the same active
set in both tools. Financial columns are never requested.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from adapters.monday import cache as monday_cache
from shared import boards

_VALUE_FRAGMENT = """
          id
          text
          type
          ... on MirrorValue { display_value }
          ... on BoardRelationValue { display_value }
          ... on PeopleValue {
            text
            persons_and_teams { id kind }
          }
"""

SKIP_PROJECT_STATUSES = {"project lost/canceled"}

# Status labels that mean "nothing wrong" — not attention-worthy.
_CLEAR_BLOCKED = frozenset({"", "clear", "none", "n/a", "na", "-"})
_CLEAR_OVERDUE = frozenset({"", "on time", "clear", "none", "n/a", "na", "-"})


def _item_url(item_id) -> str:
    return (f"https://greenvalleycontractors.monday.com/boards/"
            f"{boards.MORNING_BOARD_ID}/pulses/{item_id}")


def _column_text(cv: dict) -> Optional[str]:
    for key in ("display_value", "text"):
        raw = cv.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _people(cv: dict) -> list[dict]:
    """
    People column → [{id, name, email}, ...].

    Monday's PeopleEntity only exposes id+kind (not name). Names come from the
    column `text` (comma-separated). Email requires a separate users query —
    slice 1 matches on display name from portal_store + this text.
    """
    names = [n.strip() for n in (_column_text(cv) or "").split(",") if n.strip()]
    entities = cv.get("persons_and_teams") or []
    out: list[dict] = []
    for i, p in enumerate(entities):
        if not p:
            continue
        kind = (p.get("kind") or "").lower()
        if kind and kind not in ("person", "user", ""):
            continue  # skip teams/agents for Ops. Owner matching
        out.append({
            "id": p.get("id"),
            "name": names[i] if i < len(names) else (names[0] if len(names) == 1 else None),
            "email": None,
        })
    # Text-only fallback when persons_and_teams is empty but text is set.
    if not out and names:
        out = [{"id": None, "name": n, "email": None} for n in names]
    return out


def fetch_ops_items(mc) -> list[dict]:
    """
    Active Operations tasks for the Morning Brief, short-TTL cached.

    Each row:
      item_id, name, url, group_id, group_title,
      project_name, location, project_status,
      stage, stage_detail, scheduled_day, blocked, overdue, progress,
      ops_owners: [{id, name, email}, ...]
    """
    return monday_cache.get_or_set(
        "list:morning:ops_items",
        lambda: _fetch_ops_items_uncached(mc),
        ttl=monday_cache.list_ttl(),
    )


def _fetch_ops_items_uncached(mc) -> list[dict]:
    # Never request excluded money columns even if someone widens the allowlist.
    col_ids = [c for c in boards.MORNING_READ_COLUMN_IDS
               if c not in boards.MORNING_HARD_EXCLUDED_IDS]
    col_json = json.dumps(col_ids)
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
    """ % (col_json, _VALUE_FRAGMENT)
    rows: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {"boardId": [str(boards.MORNING_BOARD_ID)],
                                 "cursor": cursor})
        boards_data = data.get("boards") or []
        if not boards_data:
            break
        page = boards_data[0]["items_page"]
        for item in page.get("items") or []:
            row = _normalize(item)
            if row is not None:
                rows.append(row)
        cursor = page.get("cursor")
        if not cursor:
            break
    return rows


def _normalize(item: dict) -> Optional[dict]:
    name = (item.get("name") or "").strip()
    group = item.get("group") or {}
    if group.get("id") in boards.MORNING_SKIP_GROUP_IDS:
        return None

    texts: dict[str, Optional[str]] = {}
    owners: list[dict] = []
    for cv in item.get("column_values") or []:
        cid = cv.get("id")
        if not cid or cid in boards.MORNING_HARD_EXCLUDED_IDS:
            continue
        if cid == boards.MORNING_COL_OPS_OWNER:
            owners = _people(cv)
            # Also keep a joined text for display / name matching fallback.
            texts[cid] = ", ".join(
                p["name"] for p in owners if p.get("name")
            ) or _column_text(cv)
        else:
            texts[cid] = _column_text(cv)

    project_status = texts.get(boards.MORNING_COL_PROJECT_STATUS)
    if (project_status or "").strip().lower() in SKIP_PROJECT_STATUSES:
        return None

    return {
        "item_id": int(item["id"]),
        "name": name,
        "url": _item_url(item["id"]),
        "group_id": group.get("id"),
        "group_title": group.get("title"),
        "project_name": texts.get(boards.MORNING_COL_PROJECT_LINK),
        "location": texts.get(boards.MORNING_COL_LOCATION),
        "project_status": project_status,
        "stage": texts.get(boards.MORNING_COL_STAGE),
        "stage_detail": texts.get(boards.MORNING_COL_STAGE_DETAIL),
        "scheduled_day": texts.get(boards.MORNING_COL_SCHEDULED),
        "blocked": texts.get(boards.MORNING_COL_BLOCKED),
        "overdue": texts.get(boards.MORNING_COL_OVERDUE),
        "progress": texts.get(boards.MORNING_COL_PROGRESS),
        "ops_owners": owners,
        "ops_owner_text": texts.get(boards.MORNING_COL_OPS_OWNER),
    }


def is_attention(row: dict) -> bool:
    """PURE. Blocked or overdue in a meaningful way → Needs attention today."""
    blocked = (row.get("blocked") or "").strip().lower()
    overdue = (row.get("overdue") or "").strip().lower()
    if blocked and blocked not in _CLEAR_BLOCKED:
        return True
    if overdue and overdue not in _CLEAR_OVERDUE:
        return True
    return False


def is_personally_relevant(row: dict, *, email: str,
                           display_name: Optional[str] = None) -> bool:
    """
    PURE. Spec: Ops. Owner match. (14-day update-author rule is slice 2 —
    needs Monday updates history.)
    """
    email = (email or "").strip().lower()
    name = (display_name or "").strip().lower()
    name_parts = {p for p in name.replace(",", " ").split() if len(p) > 1}

    for p in row.get("ops_owners") or []:
        pe = (p.get("email") or "").strip().lower()
        if email and pe and pe == email:
            return True
        pn = (p.get("name") or "").strip().lower()
        if name and pn and (pn == name or _name_overlap(pn, name_parts)):
            return True

    # Fallback when Monday returns text-only people values.
    owner_text = (row.get("ops_owner_text") or "").strip().lower()
    if email and email.split("@")[0] and email.split("@")[0] in owner_text:
        # Too weak alone for short local-parts; only when display name also hits.
        pass
    if name_parts and owner_text and _name_overlap(owner_text, name_parts):
        return True
    return False


def _name_overlap(haystack: str, parts: set[str]) -> bool:
    """True when at least one distinctive name token appears in haystack."""
    hits = sum(1 for p in parts if p in haystack)
    # First name alone is enough for GVC's small roster ("Mark", "Robert").
    return hits >= 1


def assert_no_financial_keys(payload: Any) -> None:
    """PURE test helper — walk a payload and refuse known money column ids."""
    bad = boards.MORNING_HARD_EXCLUDED_IDS
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if str(k) in bad:
                    raise AssertionError(f"financial key leaked: {k}")
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
