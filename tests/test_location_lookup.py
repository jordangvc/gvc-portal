"""Pure location lookup helpers for rename enrichment."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.jobstart import location_lookup as ll  # noqa: E402
from subsystems.jobstart import rename_enrich as re  # noqa: E402
from subsystems.jobstart import rename_plan as rp  # noqa: E402


SAMPLE_VALUE = json.dumps({
    "lat": "39.246",
    "lng": "-84.312",
    "address": "9195 Silva Drive, Cincinnati, OH 45241",
})

STANDARD = (
    "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence"
)
JOB_TITLE = "Smith residence"


def test_parse_monday_location_json():
    out = ll.parse_monday_location_json(SAMPLE_VALUE)
    assert out["city"] == "Cincinnati"
    assert out["state"] == "OH"
    assert out["zip"] == "45241"
    assert "Silva" in (out["street"] or "")


def test_enrich_from_value_json_not_just_text():
    enriched = ll.enrich_location(
        name="9195 Silva | Willow Creek",
        location_text="",  # empty text — JSON still wins
        location_value_json=SAMPLE_VALUE,
    )
    assert enriched["complete"] is True
    assert "45241" in (enriched["hint"] or "")


def test_plan_enriched_uses_json_without_geocode():
    plan = re.plan_enriched_row(
        name="9195 Silva | Willow Creek",
        location_value_json=SAMPLE_VALUE,
        builder="Willow Creek",
        job_title=JOB_TITLE,
        geocode=False,
        item_id=1,
        board="projects",
    )
    assert plan["action"] == "rename"
    assert plan["new_name"] == STANDARD


def test_plan_enriched_geocode_fallback():
    def fake_geo(street: str):
        assert "Silva" in street
        return {
            "street": "9195 Silva Drive",
            "city": "Cincinnati",
            "state": "OH",
            "zip": "45241",
            "hint": "9195 Silva Drive, Cincinnati, OH 45241",
            "display_name": "9195 Silva Drive, Cincinnati, OH",
        }

    plan = re.plan_enriched_row(
        name="9195 Silva | Willow Creek",
        job_title=JOB_TITLE,
        geocode=True,
        geocode_street_fn=fake_geo,
        reverse_geocode_fn=lambda *a, **k: None,
        item_id=2,
    )
    assert plan["action"] == "rename"
    assert "nominatim_tri_state" in (plan.get("lookup_sources") or [])
    assert plan["new_name"].endswith(f"| Willow Creek | {JOB_TITLE}")
    assert "45241" in plan["new_name"]


def test_co_cascade_with_parent():
    parent = STANDARD
    plan = rp.plan_row(
        name="CO.1 - 9195 Silva | Willow Creek",
        parent_name=parent,
    )
    assert plan["action"] == "rename"
    assert plan["new_name"] == f"CO.1 - {parent}"


def test_co_incomplete_without_parent():
    plan = rp.plan_row(name="CO.1 - 9195 Silva | Willow Creek")
    assert plan["action"] == "skip_incomplete"


def test_parent_index_and_resolve():
    plans = [
        rp.plan_row(
            name="9195 Silva | Willow Creek",
            location="9195 Silva Drive, Cincinnati, OH 45241",
            job_title=JOB_TITLE,
        ),
    ]
    assert plans[0]["action"] == "rename"
    index = re.index_parent_titles(plans)
    assert index["9195 Silva | Willow Creek"] == STANDARD
    resolved = re.resolve_parent_title(
        "CO.2 - 9195 Silva | Willow Creek", index)
    assert resolved == plans[0]["new_name"]


def test_geocode_unique_state_only():
    from adapters import geocode as geo

    calls = []

    def search(params):
        calls.append(params)
        state = params.get("state")
        if state == "Ohio":
            return [{
                "display_name": "9195 Silva Drive, Cincinnati, OH",
                "address": {
                    "house_number": "9195",
                    "road": "Silva Drive",
                    "city": "Cincinnati",
                    "state": "Ohio",
                    "postcode": "45241",
                },
            }]
        return []

    hit = geo.lookup_tri_state_street("9195 Silva", search_fn=search)
    assert hit is not None
    assert hit["state"] == "OH"
    assert hit["zip"] == "45241"
    # Ambiguous if IN also hits:
    def search_both(params):
        return [{
            "display_name": "x",
            "address": {
                "house_number": "9195", "road": "Silva Drive",
                "city": "Town", "state": params["state"], "postcode": "47001",
            },
        }]
    assert geo.lookup_tri_state_street("9195 Silva", search_fn=search_both) is None


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK  {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    raise SystemExit(failed)
