"""Location tree — PURE operations over the locations doc.

Custodians ARE locations (DECISIONS.md D4): trucks, job sites, employee
custody, repair, disposal are all rows with a `kind`, so every movement is
location→location and "an asset is in exactly one place" holds by
construction.

Doc shape:
{
  "schema": 1,
  "locations": {loc_id: {id, name, kind, parent, address, notes,
                active bool, scan_token, employee_email (kind=employee),
                monday_item_id (kind=job_site, optional), created_at}},
  "tokens": {scan_token: loc_id},
  "next_seq": int,
}
"""
from __future__ import annotations

import copy

from subsystems.inventory.model import (
    LEAF_ONLY_KINDS, LOCATION_KINDS, new_token, now_iso, require, slugify,
)


def ensure_shape(doc: dict) -> dict:
    doc.setdefault("schema", 1)
    doc.setdefault("locations", {})
    doc.setdefault("tokens", {})
    doc.setdefault("next_seq", 1)
    return doc


def get_location(doc: dict, loc_id: str, *,
                 allow_inactive: bool = False) -> dict:
    loc = (doc.get("locations") or {}).get(loc_id)
    require(loc is not None, "UNKNOWN_LOCATION",
            f"Location '{loc_id}' does not exist.", field="location_id")
    require(allow_inactive or loc.get("active", True), "LOCATION_INACTIVE",
            f"'{loc.get('name')}' is deactivated.", field="location_id",
            advice="A manager can reactivate it or move stock elsewhere.")
    return loc


def upsert_location(doc: dict, payload: dict, *, actor: str,
                    loc_id: str = "") -> tuple[dict, dict]:
    new = copy.deepcopy(ensure_shape(doc))
    name = str(payload.get("name") or "").strip()
    require(bool(name), "INVALID_INPUT", "Location name is required.",
            field="name")
    kind = str(payload.get("kind") or "storage")
    require(kind in LOCATION_KINDS, "INVALID_INPUT",
            f"kind must be one of {LOCATION_KINDS}.", field="kind")

    parent = str(payload.get("parent") or "")
    if parent:
        p = new["locations"].get(parent)
        require(p is not None, "UNKNOWN_LOCATION",
                f"Parent '{parent}' does not exist.", field="parent")
        require(p["kind"] not in LEAF_ONLY_KINDS, "INVALID_INPUT",
                f"A {p['kind']} location can't contain sub-locations.",
                field="parent")

    if loc_id:
        loc = new["locations"].get(loc_id)
        require(loc is not None, "UNKNOWN_LOCATION",
                f"Location '{loc_id}' does not exist.", field="location_id")
        # Cycle guard: walking up from the new parent must not reach self.
        seen, cur = set(), parent
        while cur:
            require(cur != loc_id, "INVALID_INPUT",
                    "A location can't be its own ancestor.", field="parent")
            require(cur not in seen, "INVALID_INPUT",
                    "Location tree cycle detected.", field="parent")
            seen.add(cur)
            cur = (new["locations"].get(cur) or {}).get("parent") or ""
    else:
        seq = int(new.get("next_seq") or 1)
        new["next_seq"] = seq + 1
        loc_id = f"loc-{seq:04d}-{slugify(name)[:24]}"
        token = new_token("L")
        loc = {"id": loc_id, "created_at": now_iso(), "active": True,
               "scan_token": token}
        new["tokens"][token] = loc_id

    loc.update({
        "name": name, "kind": kind, "parent": parent,
        "address": str(payload.get("address") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
        "employee_email": str(payload.get("employee_email") or "").strip()
                          .lower(),
        "monday_item_id": payload.get("monday_item_id") or None,
        "updated_at": now_iso(), "updated_by": actor,
    })
    if kind == "employee":
        require(bool(loc["employee_email"]), "INVALID_INPUT",
                "Employee-custody locations need employee_email.",
                field="employee_email")
    new["locations"][loc_id] = loc
    return new, loc


def ensure_employee_location(doc: dict, email: str,
                             display_name: str = "") -> tuple[dict, dict]:
    """Idempotent: the 'Me' destination. Returns existing doc object
    untouched (identity) when the location already exists."""
    email = (email or "").strip().lower()
    require(bool(email), "INVALID_INPUT", "employee email required.",
            field="employee_email")
    for loc in (doc.get("locations") or {}).values():
        if loc.get("kind") == "employee" \
                and loc.get("employee_email") == email \
                and loc.get("active", True):
            return doc, loc
    name = display_name or email.split("@")[0].replace(".", " ").title()
    return upsert_location(doc, {"name": f"{name} (custody)",
                                 "kind": "employee",
                                 "employee_email": email}, actor=email)


def set_active(doc: dict, loc_id: str, active: bool, *, actor: str,
               holdings_empty: bool) -> tuple[dict, dict]:
    """Deactivation requires the caller to have proven the location holds
    nothing (ledger check happens in the orchestrator — this module can't
    see balances)."""
    new = copy.deepcopy(ensure_shape(doc))
    loc = new["locations"].get(loc_id)
    require(loc is not None, "UNKNOWN_LOCATION",
            f"Location '{loc_id}' does not exist.", field="location_id")
    if not active:
        require(holdings_empty, "LOCATION_NOT_EMPTY",
                f"'{loc['name']}' still holds inventory.",
                field="location_id",
                advice="Transfer its contents first (Inventory admin has "
                       "a move-all preview), then deactivate.")
        for child in new["locations"].values():
            require(not (child.get("parent") == loc_id
                         and child.get("active", True)),
                    "LOCATION_NOT_EMPTY",
                    f"'{loc['name']}' still has active sub-locations.",
                    field="location_id")
    loc["active"] = bool(active)
    loc["updated_at"] = now_iso()
    loc["updated_by"] = actor
    return new, loc


def rotate_token(doc: dict, loc_id: str, *, actor: str) -> tuple[dict, dict]:
    """Revoke + replace a compromised label token."""
    new = copy.deepcopy(ensure_shape(doc))
    loc = new["locations"].get(loc_id)
    require(loc is not None, "UNKNOWN_LOCATION",
            f"Location '{loc_id}' does not exist.", field="location_id")
    old = loc.get("scan_token")
    if old and old in new["tokens"]:
        del new["tokens"][old]
    token = new_token("L")
    loc["scan_token"] = token
    loc["updated_at"] = now_iso()
    loc["updated_by"] = actor
    new["tokens"][token] = loc_id
    return new, loc


def resolve_token(doc: dict, token: str) -> dict | None:
    loc_id = (doc.get("tokens") or {}).get((token or "").strip())
    if not loc_id:
        return None
    return (doc.get("locations") or {}).get(loc_id)


def path_name(doc: dict, loc_id: str) -> str:
    """'Main Storage › Scaffold Zone' for display."""
    parts, cur, hops = [], loc_id, 0
    locs = doc.get("locations") or {}
    while cur and hops < 10:
        loc = locs.get(cur)
        if not loc:
            break
        parts.append(loc.get("name") or cur)
        cur = loc.get("parent") or ""
        hops += 1
    return " › ".join(reversed(parts))
