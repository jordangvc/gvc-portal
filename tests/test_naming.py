"""
GVC job-naming standard — city/state/ZIP required (Jordan 2026-08-05).
=========================================================================
Self-running (pytest OR `python tests/test_naming.py`). Pure, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.jobstart import naming as n  # noqa: E402


def test_to_standard_keeps_city_state_zip():
    out = n.to_standard(
        "9761 Gertrude Lane, Cincinnati OH 45231 - Bryant - Jent Construction - New House")
    assert out["ok"] is True
    assert out["name"] == (
        "9761 Gertrude Lane, Cincinnati, OH 45231 | Bryant | Jent Construction")
    assert out["city"] == "Cincinnati"
    assert out["state"] == "OH"
    assert out["zip"] == "45231"


def test_to_standard_uses_location_hint_when_title_is_short():
    out = n.to_standard(
        "9195 Silva | Willow Creek",
        location_hint="9195 Silva Drive, Cincinnati, OH 45241")
    assert out["ok"] is True
    assert out["name"] == "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek"
    assert n.is_standard(out["name"]) is True


def test_short_pipe_name_is_not_standard_anymore():
    assert n.is_standard("9195 Silva | Willow Creek") is False
    assert n.is_standard(
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek") is True


def test_missing_geo_asks_does_not_guess():
    out = n.to_standard("9195 Silva | Willow Creek")
    assert out["ok"] is False
    assert "city/state/ZIP" in out["note"]
    assert out["builder"] == "Willow Creek"


def test_missing_builder_asks():
    out = n.to_standard(
        "9195 Silva Drive, Cincinnati, OH 45241",
        location_hint="9195 Silva Drive, Cincinnati, OH 45241")
    assert out["ok"] is False
    assert "builder" in out["note"].lower()


def test_compose_job_name_for_estimate_path():
    label = n.compose_job_name(
        "9195 Silva Drive, Cincinnati, OH 45241", "Willow Creek")
    assert label == "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek"


def test_descriptor_still_stripped():
    out = n.to_standard(
        "21435 Abbys Lane, Brookville, IN 47012 - Greg Gavin - New House")
    assert out["ok"] is True
    assert "New House" not in out["name"]
    assert out["name"].endswith("| Greg Gavin")


def test_co_prefix_preserved():
    out = n.to_standard(
        "CO_9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert out["ok"] is True
    assert out["name"].startswith("CO_")


def test_legacy_short_matches_new_long():
    short = "9195 Silva | Willow Creek"
    long = "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek"
    assert n.match_score(short, long) >= n.MATCH_THRESHOLD
    hit = n.best_match(long, [{"id": 1, "name": short},
                              {"id": 2, "name": "3776 Susanna | Martin"}])
    assert hit is not None
    assert hit["id"] == 1


def test_different_jobs_same_builder_do_not_match():
    a = "21435 Abbys, Brookville, IN 47012 | Greg Gavin"
    b = "23946 Grubbs, Brookville, IN 47012 | Greg Cross"
    assert n.match_score(a, b) < n.MATCH_THRESHOLD


def test_parse_location_city_state_zip_variants():
    a = n.parse_location("9761 Gertrude Lane, Cincinnati, OH 45231")
    b = n.parse_location("9761 Gertrude Lane, Cincinnati OH 45231")
    assert a["city"] == "Cincinnati" and a["state"] == "OH" and a["zip"] == "45231"
    assert b["city"] == "Cincinnati" and b["state"] == "OH" and b["zip"] == "45231"


def test_folder_match_still_works_with_new_titles():
    job = "9761 Gertrude Lane, Cincinnati, OH 45231 | Bryant | Jent Construction"
    folder = "331 - Jent - Bryant Res - Sent"
    assert n.folder_match_score(job, folder) >= n.FOLDER_MATCH_THRESHOLD


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
