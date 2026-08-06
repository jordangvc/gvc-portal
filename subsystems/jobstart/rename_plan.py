"""
Bulk job-rename planner — pure, no Monday / Drive I/O.
=========================================================================
Jordan 2026-08-05: city/state/ZIP required in titles.
Jordan 2026-08-06: Job Title is the required third pipe segment.

This module decides what each existing item SHOULD be named under the
3-part standard, without writing anything:

    [Street], [City], [ST] [ZIP] | [Builder] | [Job Title]

    plan_row({name, location?, builder?, job_title?, ...}) → decision dict

Actions:
  skip_standard   — already 3-part with complete geo + builder + job title
  skip_incomplete — still missing geo/builder/job title AFTER lookup
  rename          — old ≠ new and ok=True (includes CO cascade when parent known)

Callers (scripts/backfill_job_rename_*.py) own paging, lookup, dry-run, and apply.
CO rows: pass `parent_name` (looked-up / already-renamed parent title) so they
rename to `CO.n - {parent}` instead of being skipped.
"""
from __future__ import annotations

from typing import Optional

from subsystems.jobstart import location_lookup, naming


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
    job_title: Optional[str] = None,
    project_type: Optional[str] = None,
    homeowner_last: Optional[str] = None,
    business_name: Optional[str] = None,
    item_id: Optional[int] = None,
    board: Optional[str] = None,
    gfolder_url: Optional[str] = None,
    linked_project_name: Optional[str] = None,
    parent_name: Optional[str] = None,
    lookup_note: Optional[str] = None,
) -> dict:
    """
    PURE. One Monday/Drive row → rename decision.

    Prefer `linked_project_name` when Ops should mirror a Projects title that
    was already planned/renamed — avoids re-parsing a short Ops title.

    For `CO.n - …` rows, pass `parent_name` (standard parent title). The CO
    title becomes `CO.n - {parent_name}`.

    Optional `job_title` / `project_type` / `homeowner_last` / `business_name`
    feed `naming.to_standard` so a 2-part geo+builder row can become 3-part.
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
        "job_title": None,
        "city": None,
        "state": None,
        "zip": None,
        "lookup_note": (lookup_note or "").strip() or None,
    }

    if not name:
        return {**base, "action": "skip_incomplete",
                "note": "Empty item name."}

    if is_co_item_name(name):
        parent = (parent_name or linked_project_name or "").strip()
        if not parent:
            # Try to recover parent text from the CO title itself, then the
            # caller should have enriched/renamed that parent already.
            parent = location_lookup.co_parent_from_name(name) or ""
        if parent and naming.is_standard(parent):
            new_name = location_lookup.build_co_title(parent, name)
            if new_name == name:
                return {**base, "action": "skip_standard", "ok": True,
                        "new_name": new_name,
                        "note": "CO title already matches standard parent."}
            return {**base, "action": "rename", "ok": True, "new_name": new_name,
                    "note": (lookup_note or "Cascade CO title from parent.")}
        return {**base, "action": "skip_incomplete",
                "note": (lookup_note
                         or "CO parent not yet standard — rename parent first, "
                            "then re-run.")}

    # Ops (and mirrors): if a linked Projects name is already standard, use it.
    link = (linked_project_name or "").strip()
    if link and naming.is_standard(link):
        if name == link:
            return {**base, "action": "skip_standard", "ok": True,
                    "new_name": link, "note": "Already matches linked Project."}
        return {**base, "action": "rename", "ok": True, "new_name": link,
                "note": "Mirror linked Projects title."}

    if naming.is_standard(name):
        parts = [p.strip() for p in name.split("|")]
        loc = naming.parse_location(parts[0])
        return {
            **base,
            "action": "skip_standard",
            "ok": True,
            "new_name": name,
            "street": loc.get("street"),
            "builder": parts[1] if len(parts) > 1 else None,
            "job_title": parts[2] if len(parts) > 2 else None,
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
        job_title_hint=(job_title or "").strip() or None,
        project_type=(project_type or "").strip() or None,
        homeowner_last=(homeowner_last or "").strip() or None,
        business_name=(business_name or "").strip() or None,
    )
    new_name = (std.get("name") or "").strip()
    out = {
        **base,
        "ok": bool(std.get("ok")),
        "new_name": new_name or name,
        "note": std.get("note") or "",
        "street": std.get("street"),
        "builder": std.get("builder"),
        "job_title": std.get("job_title"),
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
