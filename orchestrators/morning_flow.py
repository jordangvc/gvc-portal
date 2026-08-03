"""
Morning Brief flow — private employee daily control center.
=========================================================================
Slice 1 (2026-08-03): build the authenticated employee brief from live
Operations-board data. Preparation scoring, routes, Action Requests, GM
huddle, Owner Pulse, and Fireflies are later slices — see
docs/MORNING_BRIEF_BUILD_SPEC.md.

Governing rule: never put routine work on Jordan. This flow personalizes for
the signed-in employee and strips financial fields at the adapter boundary.
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from adapters.monday import morning as mm
from adapters.monday.client import MondayClient

_ET = ZoneInfo("America/New_York")


def _person_name_for(email: str) -> Optional[str]:
    """Best-effort display name from the portal store (may be missing)."""
    try:
        from shared import portal_store
        from shared import access
        if access.backend() != "gcs":
            return None
        doc = portal_store.load()
        user = (doc.get("users") or {}).get((email or "").strip().lower()) or {}
        person = user.get("person") or {}
        name = (person.get("name") or "").strip()
        return name or None
    except Exception:  # noqa: BLE001 — store optional for env-backend users
        return None


def _card(row: dict, *, reason: str) -> dict:
    """UI card — collapsed-default fields only; no financial keys."""
    blocked = (row.get("blocked") or "").strip()
    overdue = (row.get("overdue") or "").strip()
    clear = not mm.is_attention(row)
    return {
        "item_id": row["item_id"],
        "name": row["name"],
        "url": row["url"],
        "project_name": row.get("project_name"),
        "location": row.get("location"),
        "stage": row.get("stage"),
        "stage_detail": row.get("stage_detail"),
        "scheduled_day": row.get("scheduled_day"),
        "blocked": blocked or None,
        "overdue": overdue or None,
        "progress": row.get("progress"),
        "ops_owner_text": row.get("ops_owner_text"),
        "clear": clear,
        "relevance_reason": reason,
        "group_title": row.get("group_title"),
    }


def build_employee_brief(email: str) -> dict[str, Any]:
    """
    Personalized Morning Brief for `email`.

    Returns a JSON-ready dict the UI renders. Preparation is a STUB in slice 1
    (opened_at only) — full 6-criterion readiness lands with the prep store.
    """
    email = (email or "").strip().lower()
    display_name = _person_name_for(email)
    now = datetime.now(_ET)

    mc = MondayClient()
    try:
        items = mm.fetch_ops_items(mc)
    except Exception as e:  # noqa: BLE001
        print(f"[morning] ops fetch failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        raise

    mine: list[dict] = []
    attention: list[dict] = []
    holds: list[dict] = []  # slice 1: empty — needs 7-day quiet detection

    for row in items:
        relevant = mm.is_personally_relevant(
            row, email=email, display_name=display_name)
        if relevant:
            mine.append(_card(row, reason="Ops. Owner"))
        if mm.is_attention(row):
            attention.append(_card(
                row,
                reason="Needs attention" if not relevant else "Ops. Owner · attention",
            ))

    # Stable-ish order: scheduled / stage text, then name.
    def _sort_key(c: dict):
        return (
            (c.get("scheduled_day") or "zzz").lower(),
            (c.get("name") or "").lower(),
        )

    mine.sort(key=_sort_key)
    attention.sort(key=_sort_key)

    # Hub tile summary — prep stub until preparation events exist.
    prep = {
        "ready": 0,
        "total": 6,
        "label": "Prep tracking comes next",
        "opened": True,
        "criteria": [
            {"id": "opened", "label": "Opened the brief", "done": True},
            {"id": "first_stop", "label": "First stop confirmed", "done": False},
            {"id": "work", "label": "Today's work confirmed", "done": False},
            {"id": "materials", "label": "Materials / info reviewed", "done": False},
            {"id": "blockers", "label": "Blockers answered", "done": False},
            {"id": "requests", "label": "Requests submitted", "done": False},
        ],
    }

    refreshed = now.strftime("%I:%M %p").lstrip("0")

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "workdate": now.date().isoformat(),
        "timezone": "America/New_York",
        "employee": {
            "email": email,
            "name": display_name,
        },
        "weather": None,          # Calendar/weather widgets — later
        "leave": None,
        "preparation": prep,
        "origin": None,           # Private start location — later
        "stops": [],              # Route service — later
        "action_requests": {
            "incoming": [],
            "outgoing": [],
            "note": "Action Requests board ships in a later slice.",
        },
        "my_projects": mine,
        "needs_attention": attention,
        "long_term_holds": holds,
        "hub": {
            "ready": prep["ready"],
            "total": prep["total"],
            "stops": len(mine),
            "attention": len(attention),
            "refreshed_at": refreshed,
            "label": f"{len(mine)} of yours · {len(attention)} need attention",
        },
    }


def hub_summary(email: str) -> dict[str, Any]:
    """Lightweight payload for the hub tile (or reuse build_employee_brief.hub)."""
    brief = build_employee_brief(email)
    return {"ok": True, "hub": brief.get("hub") or {},
            "generated_at": brief.get("generated_at")}
