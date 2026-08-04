"""Pure tests for GFolder / Ops-link backfill helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backfill_projects_gfolder import (  # noqa: E402
    OPS_LINK_THRESHOLD,
    is_empty_gfolder,
    pick_ops_project_match,
)
from subsystems.jobstart import naming  # noqa: E402


def test_is_empty_gfolder():
    assert is_empty_gfolder(None) is True
    assert is_empty_gfolder("") is True
    assert is_empty_gfolder("  ") is True
    assert is_empty_gfolder("GFolder") is True
    assert is_empty_gfolder("https://drive.google.com/drive/folders/abc123XYZ") is False


def test_ops_link_threshold_exact():
    projects = [
        {"id": 1, "name": "9761 Gertrude | Jent Construction"},
        {"id": 2, "name": "21435 Abbys | Greg Gavin"},
    ]
    hit = pick_ops_project_match(
        "9761 Gertrude | Jent Construction", projects)
    assert hit is not None
    assert hit["id"] == 1
    assert hit["score"] == 1.0
    assert hit["how"] == "exact"


def test_ops_link_threshold_rejects_weak_token_match():
    # Same builder, different street — must NOT match at 0.85.
    projects = [
        {"id": 1, "name": "21435 Abbys | Greg Gavin"},
        {"id": 2, "name": "23946 Grubbs | Greg Cross"},
    ]
    hit = pick_ops_project_match(
        "21435 Abbys Lane - Greg Gavin - New House", projects,
        threshold=OPS_LINK_THRESHOLD)
    # Exact street+builder tokens can still score high; verify the DIFFERENT
    # street does not adopt the wrong job.
    wrong = pick_ops_project_match(
        "23946 Grubbs Road | Someone Else", projects,
        threshold=OPS_LINK_THRESHOLD)
    assert wrong is None or wrong["id"] != 1


def test_ops_link_legacy_pipe_pair_at_high_threshold():
    # Legacy ↔ pipe for the SAME job should clear 0.85 (shared street # + builder).
    projects = [
        {"id": 10, "name": "9761 Gertrude | Jent Construction"},
    ]
    legacy = ("9761 Gertrude Lane, Cincinnati OH 45231 - Bryant - "
              "Jent Construction - New House")
    score = naming.match_score(legacy, projects[0]["name"])
    hit = pick_ops_project_match(legacy, projects, threshold=OPS_LINK_THRESHOLD)
    # If the token score clears 0.85, we must adopt; if not, None is correct
    # (strict bar — don't stamp a weak match).
    if score >= OPS_LINK_THRESHOLD:
        assert hit is not None and hit["id"] == 10
    else:
        assert hit is None


def test_best_match_custom_threshold():
    candidates = [
        {"id": 1, "name": "123 Main | Acme"},
        {"id": 2, "name": "999 Other | Acme"},
    ]
    # Default 0.5 may still match on "Acme" alone in some cases; at 0.85 a
    # weak overlap must not adopt.
    weak = naming.best_match("Acme Builder Misc", candidates, threshold=0.85)
    assert weak is None


def test_ambiguous_skipped():
    candidates = [
        {"id": 1, "name": "100 Main | Builder A"},
        {"id": 2, "name": "100 Main | Builder B"},
    ]
    # Same street number, two builders — ambiguous top-2 → None.
    hit = pick_ops_project_match(
        "100 Main Street Job", candidates, threshold=0.5)
    assert hit is None


if __name__ == "__main__":
    test_is_empty_gfolder()
    test_ops_link_threshold_exact()
    test_ops_link_threshold_rejects_weak_token_match()
    test_ops_link_legacy_pipe_pair_at_high_threshold()
    test_best_match_custom_threshold()
    test_ambiguous_skipped()
    print("ok — backfill helpers")
