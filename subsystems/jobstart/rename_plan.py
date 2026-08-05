"""
Bulk job-rename planner — pure, no Monday / Drive I/O.
=========================================================================
Jordan 2026-08-05: city/state/ZIP required in titles. This module decides
what each existing item SHOULD be named under the new standard, without
writing anything.

    plan_row({name, location?, builder?, customer?, ...}) → decision dict

Actions:
  skip_standard   — already `Street, City, ST ZIP | Builder`
  skip_co         — top-level `CO.{n} - …` change-order row (cascade later)
  skip_incomplete — to_standard ok=False (missing geo or builder) — ASK
  rename          — old ≠ new and ok=True

Callers (scripts/backfill_job_rename_*.py) own paging, dry-run, and apply.
"""
from __future__ import annotations

from typing import Optional

from subsystems.jobstart import naming


CO_ITEM_PREFIX = "CO."


def is_co_item_name(name: str) -> bool:
    """True for top-level Change Order Projects/Ops rows (`CO.1 - …`)."""
    s = (name or "").strip()
    return s.upper().startswith(CO_ITEM_PREFIX)


def plan_row(
    *,
    name: str,
    location: Optional[str] = None,
    builder: Optional[str] = None,
    customer: Optional[str] = None,
    item_id: Optional[int] = None,
    board: Optional[str] = None,
    gfolder_url: Optional[str] = None,
    linked_project_name: Optional[str] = None,
) -> dict:
    """
    PURE. One Monday/Drive row → rename decision.

    Prefer `linked_project_name` when Ops should mirror a Projects title that
    was already planned/renamed — avoids re-parsing a short Ops title.
    """
    name = (name or "").strip()
    base = {
        "item_id": item_id,
        "board": board,
        "old_name": name,
        "new_name": name,
        "gfolder_url": (gfolder_url or "").strip() or None,
        "ok": False,
        "note": "",
        "street": None,
        "builder": None,
        "city": None,
        "state": None,
        "zip": None,
    }

    if not name:
        return {**base, "action": "skip_incomplete",
                "note": "Empty item name."}

    if is_co_item_name(name):
        return {**base, "action": "skip_co",
                "note": "CO item — rename parent first, cascade later."}

    # Ops (and mirrors): if a linked Projects name is already standard, use it.
    link = (linked_project_name or "").strip()
    if link and naming.is_standard(link):
        if name == link:
            return {**base, "action": "skip_standard", "ok": True,
                    "new_name": link, "note": "Already matches linked Project."}
        return {**base, "action": "rename", "ok": True, "new_name": link,
                "note": "Mirror linked Projects title."}

    if naming.is_standard(name):
        loc = naming.parse_location(name.split("|", 1)[0])
        return {
            **base,
            "action": "skip_standard",
            "ok": True,
            "new_name": name,
            "street": loc.get("street"),
            "city": loc.get("city"),
            "state": loc.get("state"),
            "zip": loc.get("zip"),
            "note": "",
        }

    std = naming.to_standard(
        name,
        builder_hint=(builder or "").strip() or None,
        customer_hint=(customer or "").strip() or None,
        location_hint=(location or "").strip() or None,
    )
    new_name = (std.get("name") or "").strip()
    out = {
        **base,
        "ok": bool(std.get("ok")),
        "new_name": new_name or name,
        "note": std.get("note") or "",
        "street": std.get("street"),
        "builder": std.get("builder"),
        "city": std.get("city"),
        "state": std.get("state"),
        "zip": std.get("zip"),
    }
    if not std.get("ok") or not new_name:
        return {**out, "action": "skip_incomplete"}
    if new_name == name:
        return {**out, "action": "skip_standard", "ok": True, "note": ""}
    return {**out, "action": "rename"}


def summarize(plans: list[dict]) -> dict:
    """PURE. Counts by action for a dry-run footer."""
    counts: dict[str, int] = {}
    for p in plans:
        a = p.get("action") or "unknown"
        counts[a] = counts.get(a, 0) + 1
    return {
        "total": len(plans),
        "rename": counts.get("rename", 0),
        "skip_standard": counts.get("skip_standard", 0),
        "skip_incomplete": counts.get("skip_incomplete", 0),
        "skip_co": counts.get("skip_co", 0),
        "by_action": counts,
    }


def rename_candidates(plans: list[dict]) -> list[dict]:
    """PURE. Only rows the apply path should touch."""
    return [p for p in plans if p.get("action") == "rename" and p.get("ok")]
