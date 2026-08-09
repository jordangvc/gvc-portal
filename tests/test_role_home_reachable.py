"""Every role's home screen must be a page that role can actually open.

A home href pointing at a page the user holds no grant for doesn't degrade —
it 303s to sign-in, so the person lands nowhere. This caught the `ops` preset
(morning_ops + jobcheck + jobstart — Mark / Robert, the field PMs) resolving to
the sales role and being sent to /ui/estimate, which ops cannot open.

Runs under pytest OR directly: ``python tests/test_role_home_reachable.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import access, hub_nav  # noqa: E402

# Page path -> feature required by app/service.py. Kept beside the route table;
# if a page changes its guard, update here too.
PAGE_FEATURE: dict[str, str | None] = {
    "/": None,
    "/ui/morning": "morning",
    "/ui/morning-gm": "morning_gm",
    "/ui/morning-owner": "morning_owner",
    "/ui/billing": "invoice",
    "/ui/invoice": "invoice",
    "/ui/estimate": "estimate",
    "/ui/change-order": "change_order",
    "/ui/check": "check",
    "/ui/coi": "coi",
    "/ui/jobstart": "jobstart",
    "/ui/jobcheck": "jobcheck",
    "/ui/takeoff": "takeoff",
    "/ui/lien": "lien",
    "/ui/fieldguide": "fieldguide",
    "/ui/training": "training",
    "/ui/timeoff": "timeoff",
    "/ui/activity": "activity",
    "/ui/admin": "admin",
}


def _effective(features: set[str]) -> set[str]:
    if access.WILDCARD in features:
        return set(access.FEATURES)
    return set(access._expand(features))


def test_every_preset_home_is_reachable() -> None:
    for preset in access.ROLE_PRESETS:
        feats = _effective(set(preset["features"]))
        role = hub_nav.resolve_role(feats)
        home = hub_nav.ROLE_HOME_HREF.get(role)
        assert home, f"role {role} has no home href"
        needed = PAGE_FEATURE.get(home, "__unknown__")
        assert needed != "__unknown__", f"{home} missing from PAGE_FEATURE"
        assert needed is None or needed in feats, (
            f"preset {preset['id']!r} resolves to role {role!r} and is sent to "
            f"{home}, which requires {needed!r} — a grant this preset lacks. "
            "That home screen redirects to sign-in."
        )


def test_ops_preset_is_field_not_sales() -> None:
    ops = next(p for p in access.ROLE_PRESETS if p["id"] == "ops")
    feats = _effective(set(ops["features"]))
    assert hub_nav.resolve_role(feats) == "field", (
        "ops (field PMs) must not resolve to sales — they hold no estimate grant"
    )


def test_sales_preset_still_resolves_to_sales() -> None:
    sales = next(p for p in access.ROLE_PRESETS if p["id"] == "sales")
    feats = _effective(set(sales["features"]))
    assert hub_nav.resolve_role(feats) == "sales"


if __name__ == "__main__":
    test_every_preset_home_is_reachable()
    test_ops_preset_is_field_not_sales()
    test_sales_preset_still_resolves_to_sales()
    print("ALL PASSED")
