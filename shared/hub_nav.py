"""
Hub navigation config — tool groups + role → home tool.
=========================================================================
Used by the personal hub shell (web/hub.html) and orchestrators/hub_flow.py.
Grants **dim** unreachable tools; they are never removed from the rail so
people learn the hub has them and can ask for access.
"""
from __future__ import annotations

from typing import Any, Optional

# (group_label, [(display_name, feature_key, href, external?)])
# Order = rail order. Feature keys match shared.access.FEATURES.
TOOL_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str, bool], ...]], ...] = (
    ("Today", (
        ("Your Morning Brief", "morning", "/ui/morning", False),
        ("GM Morning Huddle", "morning_gm", "/ui/morning-gm", False),
        ("Owner Pulse", "morning_owner", "/ui/morning-owner", False),
        ("Activity", "activity", "/ui/activity", False),
    )),
    ("Estimating & bids", (
        ("Takeoff", "takeoff", "https://gvctakeoff.netlify.app/v2.html", True),
        ("Estimate Generator", "estimate", "/ui/estimate", False),
        ("Change Order", "change_order", "/ui/change-order", False),
        ("Job Start", "jobstart", "/ui/jobstart", False),
    )),
    ("Money", (
        ("Billing Hub", "invoice", "/ui/billing", False),
        ("Invoice Generator", "invoice", "/ui/invoice", False),
        ("Paid by Check", "check", "/ui/check", False),
        ("Lien Watch", "lien", "/ui/lien", False),
    )),
    ("Field", (
        ("Job Check", "jobcheck", "/ui/jobcheck", False),
        ("Field Manual", "fieldguide", "/ui/fieldguide", False),
    )),
    ("Paperwork", (
        ("COI Generator", "coi", "/ui/coi", False),
        ("Time Off", "timeoff", "/ui/timeoff", False),
    )),
    ("Company", (
        ("Admin", "admin", "/ui/admin", False),
    )),
)

# Role id → default home tool feature (cold-link preference; Admin overrides later).
ROLE_HOME_TOOL: dict[str, str] = {
    "owner": "morning_owner",
    "gm": "morning_gm",
    "office": "invoice",  # Billing Hub
    "field": "morning",
}

ROLE_HOME_HREF: dict[str, str] = {
    "owner": "/ui/morning-owner",
    "gm": "/ui/morning-gm",
    "office": "/ui/billing",
    "field": "/ui/morning",
}

ROLE_QUEUE_TITLE: dict[str, str] = {
    "owner": "Exceptions",
    "gm": "Huddle queue",
    "office": "Billing queue",
    "field": "Your route today",
}

ROLE_TITLE: dict[str, str] = {
    "owner": "Owner",
    "gm": "General Manager",
    "office": "Office",
    "field": "Field",
}


def resolve_role(features: set[str]) -> str:
    """owner > gm > office > field. One role drives payload shape (handoff §5)."""
    feats = features or set()
    if "morning_owner" in feats or "admin" in feats:
        return "owner"
    if "morning_gm" in feats:
        return "gm"
    if feats & {"invoice", "estimate", "coi", "check"}:
        return "office"
    return "field"


def tools_for_client(features: set[str]) -> list[dict[str, Any]]:
    """Flat tool list with granted flag for the rail JSON."""
    feats = features or set()
    out: list[dict[str, Any]] = []
    for group, items in TOOL_GROUPS:
        for name, feature, href, external in items:
            out.append({
                "group": group,
                "name": name,
                "feature": feature,
                "href": href,
                "external": external,
                "granted": feature in feats,
            })
    return out


def groups_for_client(features: set[str]) -> list[dict[str, Any]]:
    """Grouped tools for rail rendering."""
    feats = features or set()
    groups = []
    for group, items in TOOL_GROUPS:
        rows = []
        for name, feature, href, external in items:
            rows.append({
                "name": name,
                "feature": feature,
                "href": href,
                "external": external,
                "granted": feature in feats,
            })
        groups.append({"name": group, "tools": rows})
    return groups


def initials_for(name: str, email: str) -> str:
    n = (name or "").strip()
    if n:
        parts = [p for p in n.replace(",", " ").split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        if parts:
            return parts[0][:2].upper()
    local = (email or "").split("@")[0]
    return (local[:2] or "?").upper()


def display_name(email: str, person: Optional[dict] = None) -> str:
    person = person or {}
    name = (person.get("name") or "").strip()
    if name:
        return name
    local = (email or "").split("@")[0].replace(".", " ").replace("_", " ")
    return local.title() if local else "there"


def greeting_for(name: str, *, hour: Optional[int] = None) -> str:
    """America/New_York hour when hour is None — caller should pass ET hour."""
    h = 9 if hour is None else int(hour)
    if h < 12:
        part = "Good morning"
    elif h < 17:
        part = "Good afternoon"
    else:
        part = "Good evening"
    first = (name or "there").split()[0]
    return f"{part}, {first}."
