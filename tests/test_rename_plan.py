"""Pure bulk-rename planner — no Monday / Drive / network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.jobstart import rename_plan as rp  # noqa: E402


STANDARD = (
    "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence")


def test_skip_already_standard():
    p = rp.plan_row(name=STANDARD)
    assert p["action"] == "skip_standard"
    assert p["ok"] is True
    assert p["job_title"] == "Smith residence"
    assert p["builder"] == "Willow Creek"


def test_two_part_with_geo_is_incomplete_without_job_title():
    p = rp.plan_row(
        name="9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert p["action"] == "skip_incomplete"
    assert p["ok"] is False
    assert "job title" in p["note"].lower()


def test_rename_from_short_with_location_and_job_title():
    p = rp.plan_row(
        name="9195 Silva | Willow Creek",
        location="9195 Silva Drive, Cincinnati, OH 45241",
        job_title="Smith residence",
        item_id=1, board="projects")
    assert p["action"] == "rename"
    assert p["ok"] is True
    assert p["new_name"] == STANDARD
    assert p["job_title"] == "Smith residence"
    assert p["old_name"] == "9195 Silva | Willow Creek"


def test_rename_supplies_job_title_via_homeowner():
    p = rp.plan_row(
        name="9195 Silva | Willow Creek",
        location="9195 Silva Drive, Cincinnati, OH 45241",
        project_type="Residential",
        homeowner_last="Smith")
    assert p["action"] == "rename"
    assert p["ok"] is True
    assert p["new_name"] == STANDARD


def test_skip_incomplete_without_geo():
    p = rp.plan_row(name="9195 Silva | Willow Creek",
                    job_title="Smith residence")
    assert p["action"] == "skip_incomplete"
    assert p["ok"] is False
    assert "city/state/ZIP" in p["note"]


def test_co_cascades_when_parent_standard():
    p = rp.plan_row(
        name="CO.1 - 9195 Silva | Willow Creek",
        parent_name=STANDARD,
    )
    assert p["action"] == "rename"
    assert p["new_name"] == f"CO.1 - {STANDARD}"


def test_co_incomplete_without_standard_parent():
    # 2-part geo+builder parent is NOT standard under the 3-part rule
    p = rp.plan_row(
        name="CO.1 - 9195 Silva | Willow Creek",
        parent_name="9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert p["action"] == "skip_incomplete"


def test_ops_mirrors_linked_standard_project():
    linked = ("9761 Gertrude Lane, Cincinnati, OH 45231 | Jent Construction | "
              "Bryant residence")
    p = rp.plan_row(
        name="9761 Gertrude | Jent Construction",
        linked_project_name=linked,
        board="operations")
    assert p["action"] == "rename"
    assert p["new_name"] == linked


def test_ops_already_matches_linked():
    linked = ("9761 Gertrude Lane, Cincinnati, OH 45231 | Jent Construction | "
              "Bryant residence")
    p = rp.plan_row(name=linked, linked_project_name=linked)
    assert p["action"] == "skip_standard"


def test_summarize_and_candidates():
    plans = [
        rp.plan_row(name=STANDARD),
        rp.plan_row(name="9195 Silva | Willow Creek",
                    location="9195 Silva Drive, Cincinnati, OH 45241",
                    job_title="Smith residence"),
        rp.plan_row(name="CO.2 - 9195 Silva | Willow Creek",
                    parent_name=STANDARD),
        # geo+builder but no job title → incomplete
        rp.plan_row(
            name="9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek"),
        rp.plan_row(name="100 Main | Acme"),
    ]
    s = rp.summarize(plans)
    assert s["total"] == 5
    assert s["rename"] == 2  # short title + CO cascade
    assert s["skip_incomplete"] == 2
    assert s["skip_standard"] == 1
    cands = rp.rename_candidates(plans)
    assert len(cands) == 2
    assert any(c["new_name"].startswith("CO.2 - ") for c in cands)


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
