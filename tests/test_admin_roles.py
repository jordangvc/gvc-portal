"""Role presets + feature groups used by the faster /ui/admin UI."""
from __future__ import annotations

from shared import access


def test_role_presets_known_features_only():
    ids = set()
    for role in access.ROLE_PRESETS:
        assert role["id"]
        assert role["label"]
        assert role["id"] not in ids
        ids.add(role["id"])
        for feat in role["features"]:
            if feat == access.WILDCARD:
                continue
            assert feat in access.ALL_FEATURES, f"{role['id']} has unknown {feat}"
            assert feat not in access.BASELINE, (
                f"{role['id']} repeats baseline {feat} — store grants only"
            )


def test_feature_groups_cover_catalog():
    grouped = set()
    for _label, feats in access.FEATURE_GROUPS:
        for f in feats:
            assert f in access.ALL_FEATURES
            grouped.add(f)
    # Groups are a display aid — every FEATURE should appear somewhere so the
    # admin page doesn't dump half the catalog into an "Other" bucket by accident.
    missing = access.ALL_FEATURES - grouped
    assert not missing, f"FEATURES missing from FEATURE_GROUPS: {sorted(missing)}"


def test_sales_and_ops_match_grants_plan_intent():
    by_id = {r["id"]: set(r["features"]) for r in access.ROLE_PRESETS}
    assert by_id["sales"] == {"estimate", "takeoff", "jobstart"}
    assert by_id["ops"] == {"morning_ops", "jobcheck", "jobstart"}
    assert by_id["crew"] == {"morning_ops", "jobcheck"}
    assert by_id["full"] == {access.WILDCARD}


if __name__ == "__main__":
    test_role_presets_known_features_only()
    test_feature_groups_cover_catalog()
    test_sales_and_ops_match_grants_plan_intent()
    print("ok")
