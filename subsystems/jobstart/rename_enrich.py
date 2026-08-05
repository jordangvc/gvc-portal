"""
Enrich + plan one rename row — recorded Monday facts, then geocode.
=========================================================================
Shared by scripts/backfill_job_rename_*.py so incomplete rows are looked up
instead of skipped.
"""
from __future__ import annotations

from typing import Callable, Optional

from subsystems.jobstart import location_lookup, naming, rename_plan


def plan_enriched_row(
    *,
    name: str,
    builder: Optional[str] = None,
    customer: Optional[str] = None,
    location_text: Optional[str] = None,
    location_value_json: Optional[str] = None,
    location_column: Optional[dict] = None,
    extra_hints: Optional[list[Optional[str]]] = None,
    linked_project_name: Optional[str] = None,
    parent_name: Optional[str] = None,
    item_id: Optional[int] = None,
    board: Optional[str] = None,
    gfolder_url: Optional[str] = None,
    geocode: bool = True,
    geocode_street_fn: Optional[Callable[[str], Optional[dict]]] = None,
    reverse_geocode_fn: Optional[Callable] = None,
) -> dict:
    """
    Look up location, then run rename_plan.plan_row.

    When still incomplete and `geocode` is True, tries:
      1. reverse geocode from Monday lat/lng
      2. forward geocode street in OH/IN/KY (unique hit only)
    """
    enriched = location_lookup.enrich_location(
        name=name,
        location_text=location_text,
        location_value_json=location_value_json,
        location_column=location_column,
        extra_hints=extra_hints,
    )
    lookup_bits = list(enriched.get("sources") or [])
    hint = enriched.get("hint")

    plan = rename_plan.plan_row(
        name=name,
        location=hint,
        builder=builder,
        customer=customer,
        item_id=item_id,
        board=board,
        gfolder_url=gfolder_url,
        linked_project_name=linked_project_name,
        parent_name=parent_name,
        lookup_note="; ".join(lookup_bits) if lookup_bits else None,
    )

    if plan["action"] != "skip_incomplete" or not geocode:
        if lookup_bits and plan.get("note") and plan["action"] == "rename":
            plan["note"] = (
                f"{plan['note']} (lookup: {', '.join(lookup_bits)})"
                if plan["note"] else f"lookup: {', '.join(lookup_bits)}"
            )
        plan["lookup_sources"] = lookup_bits
        return plan

    # --- network lookup paths (injected for tests) ---
    if reverse_geocode_fn is None or geocode_street_fn is None:
        try:
            from adapters import geocode as _geo
            reverse_geocode_fn = reverse_geocode_fn or _geo.reverse_lat_lng
            geocode_street_fn = geocode_street_fn or _geo.lookup_tri_state_street
        except Exception:  # noqa: BLE001
            plan["lookup_sources"] = lookup_bits
            return plan

    geo_hit = None
    if enriched.get("lat") not in (None, "") and enriched.get("lng") not in (None, ""):
        try:
            geo_hit = reverse_geocode_fn(enriched["lat"], enriched["lng"])
            if geo_hit:
                lookup_bits.append("reverse_geocode")
        except Exception:  # noqa: BLE001 — keep going to forward geocode
            geo_hit = None

    if not geo_hit:
        street = enriched.get("street") or location_lookup.street_from_job_name(name)
        if street:
            try:
                geo_hit = geocode_street_fn(street)
                if geo_hit:
                    lookup_bits.append("nominatim_tri_state")
            except Exception as exc:  # noqa: BLE001
                plan["note"] = (
                    f"{plan.get('note') or ''} Geocode error: {exc}"
                ).strip()
                plan["lookup_sources"] = lookup_bits
                return plan

    if not geo_hit or not geo_hit.get("hint"):
        plan["lookup_sources"] = lookup_bits
        if not plan.get("note"):
            plan["note"] = (
                "Still missing city/state/ZIP after Monday lookup + geocode."
            )
        return plan

    plan2 = rename_plan.plan_row(
        name=name,
        location=geo_hit["hint"],
        builder=builder,
        customer=customer,
        item_id=item_id,
        board=board,
        gfolder_url=gfolder_url,
        linked_project_name=linked_project_name,
        parent_name=parent_name,
        lookup_note="; ".join(lookup_bits),
    )
    plan2["lookup_sources"] = lookup_bits
    if plan2["action"] == "rename":
        plan2["note"] = (
            f"Looked up via {', '.join(lookup_bits)}"
            + (f" → {geo_hit.get('display_name')}" if geo_hit.get("display_name") else "")
        )
    return plan2


def index_parent_titles(plans: list[dict]) -> dict[str, str]:
    """
    PURE. Map any old parent title (and new title) → best standard parent title.

    Used for CO cascade after the non-CO pass.
    """
    index: dict[str, str] = {}
    for plan in plans:
        if rename_plan.is_co_item_name(plan.get("old_name") or ""):
            continue
        old = (plan.get("old_name") or "").strip()
        new = (plan.get("new_name") or "").strip()
        if naming.is_standard(new):
            if old:
                index[old] = new
            index[new] = new
        elif naming.is_standard(old):
            index[old] = old
    return index


def resolve_parent_title(
    co_name: str,
    parent_index: dict[str, str],
    *,
    match_fn=None,
) -> Optional[str]:
    """PURE. Find the standard parent title for a CO row."""
    parent = location_lookup.co_parent_from_name(co_name) or ""
    if not parent:
        return None
    if parent in parent_index:
        return parent_index[parent]
    # Fuzzy: parent_index values as candidates
    candidates = [
        {"id": i, "name": name}
        for i, name in enumerate(sorted(set(parent_index.values())))
    ]
    if not candidates:
        return None
    match_fn = match_fn or naming.best_match
    hit = match_fn(parent, candidates, threshold=0.5)
    if hit and naming.is_standard(hit.get("name") or ""):
        return hit["name"]
    return None
