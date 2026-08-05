"""Pure bulk-rename planner — no Monday / Drive / network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.jobstart import rename_plan as rp  # noqa: E402


def test_skip_already_standard():
    p = rp.plan_row(
        name="9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert p["action"] == "skip_standard"
    assert p["ok"] is True


def test_rename_from_short_with_location_hint():
    p = rp.plan_row(
        name="9195 Silva | Willow Creek",
        location="9195 Silva Drive, Cincinnati, OH 45241",
        item_id=1, board="projects")
    assert p["action"] == "rename"
    assert p["ok"] is True
    assert p["new_name"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert p["old_name"] == "9195 Silva | Willow Creek"


def test_skip_incomplete_without_geo():
    p = rp.plan_row(name="9195 Silva | Willow Creek")
    assert p["action"] == "skip_incomplete"
    assert p["ok"] is False
    assert "city/state/ZIP" in p["note"]


def test_skip_co_item():
    p = rp.plan_row(name="CO.1 - 9195 Silva | Willow Creek")
    assert p["action"] == "skip_co"


def test_ops_mirrors_linked_standard_project():
    linked = "9761 Gertrude Lane, Cincinnati, OH 45231 | Jent Construction"
    p = rp.plan_row(
        name="9761 Gertrude | Jent Construction",
        linked_project_name=linked,
        board="operations")
    assert p["action"] == "rename"
    assert p["new_name"] == linked


def test_ops_already_matches_linked():
    linked = "9761 Gertrude Lane, Cincinnati, OH 45231 | Jent Construction"
    p = rp.plan_row(name=linked, linked_project_name=linked)
    assert p["action"] == "skip_standard"


def test_summarize_and_candidates():
    plans = [
        rp.plan_row(name="9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek"),
        rp.plan_row(name="9195 Silva | Willow Creek",
                    location="9195 Silva Drive, Cincinnati, OH 45241"),
        rp.plan_row(name="CO.2 - whatever"),
        rp.plan_row(name="100 Main | Acme"),
    ]
    s = rp.summarize(plans)
    assert s["total"] == 4
    assert s["rename"] == 1
    assert s["skip_co"] == 1
    assert s["skip_incomplete"] == 1
    assert s["skip_standard"] == 1
    cands = rp.rename_candidates(plans)
    assert len(cands) == 1
    assert cands[0]["new_name"].endswith("| Willow Creek")


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
