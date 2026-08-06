"""
GVC job-naming standard — 3-part with Job Title (Jordan 2026-08-06).
=========================================================================
Self-running (pytest OR `python tests/test_naming.py`). Pure, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.jobstart import naming as n  # noqa: E402


def test_to_standard_keeps_city_state_zip_and_job_title():
    out = n.to_standard(
        "9761 Gertrude Lane, Cincinnati OH 45231 - Bryant - Jent Construction - New House")
    assert out["ok"] is True
    assert out["name"] == (
        "9761 Gertrude Lane, Cincinnati, OH 45231 | Bryant | Jent Construction")
    assert out["city"] == "Cincinnati"
    assert out["state"] == "OH"
    assert out["zip"] == "45231"
    assert out["builder"] == "Bryant"
    assert out["job_title"] == "Jent Construction"


def test_to_standard_uses_location_hint_and_job_title_hint():
    out = n.to_standard(
        "9195 Silva | Willow Creek",
        location_hint="9195 Silva Drive, Cincinnati, OH 45241",
        job_title_hint="Smith residence")
    assert out["ok"] is True
    assert out["name"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence")
    assert out["job_title"] == "Smith residence"
    assert n.is_standard(out["name"]) is True


def test_two_part_with_geo_is_not_standard():
    assert n.is_standard("9195 Silva | Willow Creek") is False
    assert n.is_standard(
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek") is False
    assert n.is_standard(
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence"
    ) is True


def test_missing_geo_asks_does_not_guess():
    out = n.to_standard("9195 Silva | Willow Creek",
                        job_title_hint="Smith residence")
    assert out["ok"] is False
    assert "city/state/ZIP" in out["note"]
    assert out["builder"] == "Willow Creek"
    assert out["job_title"] == "Smith residence"


def test_missing_builder_asks():
    out = n.to_standard(
        "9195 Silva Drive, Cincinnati, OH 45241",
        location_hint="9195 Silva Drive, Cincinnati, OH 45241",
        job_title_hint="Smith residence")
    assert out["ok"] is False
    assert "builder" in out["note"].lower()


def test_missing_job_title_asks():
    out = n.to_standard(
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek",
        location_hint="9195 Silva Drive, Cincinnati, OH 45241")
    assert out["ok"] is False
    assert "job title" in out["note"].lower()
    assert out["builder"] == "Willow Creek"
    assert out["job_title"] is None
    assert n.is_standard(out["name"]) is False


def test_format_job_title_residential():
    assert n.format_job_title(
        project_type="Residential", homeowner_last="Smith") == "Smith residence"
    assert n.format_job_title(
        project_type="res", homeowner_last="John Smith") == "Smith residence"
    assert n.format_job_title(
        project_type="Residential",
        homeowner_last="Smith residence") == "Smith residence"
    assert n.format_job_title(project_type="Residential") is None


def test_format_job_title_commercial():
    assert n.format_job_title(
        project_type="Commercial",
        business_name="First Financial Bank") == "First Financial Bank"
    assert n.format_job_title(project_type="Commercial") is None


def test_format_job_title_unknown_type_prefers_business():
    assert n.format_job_title(
        business_name="Acme Corp", homeowner_last="Smith") == "Acme Corp"
    assert n.format_job_title(homeowner_last="Smith") == "Smith residence"
    assert n.format_job_title() is None


def test_to_standard_formats_job_title_from_homeowner():
    out = n.to_standard(
        "9195 Silva | Willow Creek",
        location_hint="9195 Silva Drive, Cincinnati, OH 45241",
        project_type="Residential",
        homeowner_last="Smith")
    assert out["ok"] is True
    assert out["job_title"] == "Smith residence"
    assert out["name"].endswith("| Smith residence")


def test_compose_job_name_for_estimate_path():
    label = n.compose_job_name(
        "9195 Silva Drive, Cincinnati, OH 45241", "Willow Creek",
        job_title="Smith residence")
    assert label == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence")


def test_descriptor_still_stripped_needs_job_title():
    out = n.to_standard(
        "21435 Abbys Lane, Brookville, IN 47012 - Greg Gavin - New House")
    assert out["ok"] is False
    assert "New House" not in out["name"]
    assert out["builder"] == "Greg Gavin"
    assert "job title" in out["note"].lower()

    out2 = n.to_standard(
        "21435 Abbys Lane, Brookville, IN 47012 - Greg Gavin - New House",
        job_title_hint="Gavin residence")
    assert out2["ok"] is True
    assert out2["name"] == (
        "21435 Abbys Lane, Brookville, IN 47012 | Greg Gavin | Gavin residence")


def test_co_prefix_preserved():
    out = n.to_standard(
        "CO_9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence")
    assert out["ok"] is True
    assert out["name"].startswith("CO_")
    assert out["job_title"] == "Smith residence"


def test_legacy_short_matches_new_long_including_residence():
    short = "9195 Silva | Willow Creek"
    long = ("9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | "
            "Smith residence")
    assert n.match_score(short, long) >= n.MATCH_THRESHOLD
    # "residence" is noise — must not block a 2-part↔3-part match
    assert "residence" not in n.tokens(long)
    hit = n.best_match(long, [{"id": 1, "name": short},
                              {"id": 2, "name": "3776 Susanna | Martin"}])
    assert hit is not None
    assert hit["id"] == 1


def test_different_jobs_same_builder_do_not_match():
    a = "21435 Abbys, Brookville, IN 47012 | Greg Gavin | Abbys residence"
    b = "23946 Grubbs, Brookville, IN 47012 | Greg Cross | Grubbs residence"
    assert n.match_score(a, b) < n.MATCH_THRESHOLD


def test_parse_location_city_state_zip_variants():
    a = n.parse_location("9761 Gertrude Lane, Cincinnati, OH 45231")
    b = n.parse_location("9761 Gertrude Lane, Cincinnati OH 45231")
    assert a["city"] == "Cincinnati" and a["state"] == "OH" and a["zip"] == "45231"
    assert b["city"] == "Cincinnati" and b["state"] == "OH" and b["zip"] == "45231"


def test_folder_match_still_works_with_new_titles():
    job = ("9761 Gertrude Lane, Cincinnati, OH 45231 | Bryant | "
           "Jent Construction")
    folder = "331 - Jent - Bryant Res - Sent"
    assert n.folder_match_score(job, folder) >= n.FOLDER_MATCH_THRESHOLD


def test_empty_raw_with_hints_asks_for_job_title():
    out = n.to_standard(
        "",
        location_hint="9195 Silva Drive, Cincinnati, OH 45241",
        builder_hint="Willow Creek")
    assert out["ok"] is False
    assert "job title" in out["note"].lower()
    out2 = n.to_standard(
        "",
        location_hint="9195 Silva Drive, Cincinnati, OH 45241",
        builder_hint="Willow Creek",
        job_title_hint="Smith residence")
    assert out2["ok"] is True
    assert out2["name"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence")


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
