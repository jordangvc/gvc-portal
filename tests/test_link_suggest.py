"""
Morning link-suggest pure scorers — Ops↔Projects / Drive folder heuristics.
=========================================================================
Self-running (pytest OR `python tests/test_link_suggest.py`). No Monday,
no Drive, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.morning import link_suggest as ls  # noqa: E402
from subsystems.jobstart import naming  # noqa: E402


def test_score_name_match_exact_pipe_vs_legacy():
    # Same job, different naming conventions — street # + builder carry it.
    a = "9761 Gertrude | Bryant | Jent"
    b = "9761 Gertrude Lane, Cincinnati OH 45231 - Bryant - Jent Construction - New House"
    score = ls.score_name_match(a, b)
    assert score >= naming.MATCH_THRESHOLD, score
    assert score == naming.match_score(a, b)


def test_score_name_match_different_jobs_same_builder():
    # Two distinct street numbers for the same builder must NOT look alike.
    a = "21435 Abbys | Greg Gavin"
    b = "23946 Grubbs | Greg Cross"
    score = ls.score_name_match(a, b)
    assert score < naming.MATCH_THRESHOLD, score


def test_suggest_project_exact_match():
    candidates = [
        {"id": 1, "name": "9195 Silva | Willow Creek"},
        {"id": 2, "name": "3776 Susanna | Martin"},
    ]
    out = ls.suggest_project_for_ops("9195 Silva | Willow Creek", candidates)
    assert out["ambiguous"] is False
    assert out["match"]["id"] == 1
    assert out["score"] == 1.0


def test_suggest_project_token_match():
    candidates = [
        {"id": 10, "name": "9761 Gertrude Lane - Bryant - Jent Construction"},
        {"id": 11, "name": "123 Main | Other Builder"},
    ]
    out = ls.suggest_project_for_ops(
        "9761 Gertrude | Bryant | Jent", candidates)
    assert out["ambiguous"] is False
    assert out["match"]["id"] == 10
    assert out["score"] >= naming.MATCH_THRESHOLD


def test_suggest_project_ambiguous_top_two():
    # Near-identical names → refuse rather than guess.
    candidates = [
        {"id": 1, "name": "100 Main | Acme Builders"},
        {"id": 2, "name": "100 Main | Acme Builder"},
    ]
    out = ls.suggest_project_for_ops("100 Main | Acme", candidates)
    # Either below threshold with no match, or flagged ambiguous.
    if out["score"] >= naming.MATCH_THRESHOLD:
        assert out["ambiguous"] is True
        assert out["match"] is None
        assert len(out.get("runners_up") or []) == 2


def test_suggest_project_empty_inputs():
    assert ls.suggest_project_for_ops("", [{"id": 1, "name": "x"}])["match"] is None
    assert ls.suggest_project_for_ops("9195 Silva | Willow", [])["match"] is None


def test_suggest_drive_folder_picks_best():
    folders = [
        {"id": "aaa", "name": "Other Job Folder", "url": "https://drive.google.com/drive/folders/aaa"},
        {"id": "bbb", "name": "9761 Gertrude | Bryant", "url": "https://drive.google.com/drive/folders/bbb"},
    ]
    out = ls.suggest_drive_folder("9761 Gertrude | Bryant | Jent", folders)
    assert out["ambiguous"] is False
    assert out["match"]["id"] == "bbb"


def test_suggest_drive_folder_below_threshold():
    folders = [
        {"id": "zzz", "name": "Completely Unrelated Name", "url": "u"},
    ]
    out = ls.suggest_drive_folder("9761 Gertrude | Bryant", folders)
    assert out["match"] is None
    assert out["ambiguous"] is False


def test_photo_ready_status_shape():
    # Thin compatible helper lives on the Monday adapter — keep the contract
    # the UI / morning routes expect.
    from adapters.monday import jobcheck as mj

    ready = mj.photo_ready_status({
        "folder_id": "abc123",
        "gfolder_url": "https://drive.google.com/drive/folders/abc123",
        "project_item_id": 99,
        "error": None,
    })
    assert ready["photo_ready"] is True
    assert ready["reason"] is None
    assert ready["photo_block_reason"] is None
    assert ready["folder_id"] == "abc123"

    blocked = mj.photo_ready_status({
        "folder_id": None,
        "gfolder_url": None,
        "project_item_id": None,
        "error": "No linked Projects item.",
    })
    assert blocked["photo_ready"] is False
    assert "No linked Projects item" in (blocked["reason"] or "")
    assert blocked["reason"] == blocked["photo_block_reason"]


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f" FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
