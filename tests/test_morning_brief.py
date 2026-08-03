"""
Morning Brief — pure rules (relevance, attention, financial strip).
=========================================================================
Self-running:  python tests/test_morning_brief.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.monday import morning as mm  # noqa: E402
from shared import access, boards  # noqa: E402


def test_morning_is_baseline_feature():
    assert "morning" in access.FEATURES
    assert "morning" in access.BASELINE


def test_financial_ids_hard_excluded():
    for cid in ("board_counts", "numeric_mm3fcjmn", "color_mm2xd40t"):
        assert cid in boards.MORNING_HARD_EXCLUDED_IDS
    for cid in boards.MORNING_READ_COLUMN_IDS:
        assert cid not in boards.MORNING_HARD_EXCLUDED_IDS


def test_relevance_by_email():
    row = {
        "ops_owners": [{"id": "1", "name": "Mark W", "email": "mark@greenvalleycontractors.com"}],
        "ops_owner_text": "Mark W",
    }
    assert mm.is_personally_relevant(
        row, email="mark@greenvalleycontractors.com", display_name="Mark W")
    assert not mm.is_personally_relevant(
        row, email="ethan@greenvalleycontractors.com", display_name="Ethan")


def test_relevance_by_display_name():
    row = {
        "ops_owners": [{"id": "1", "name": "Robert R", "email": None}],
        "ops_owner_text": "Robert R",
    }
    assert mm.is_personally_relevant(
        row, email="robert@greenvalleycontractors.com", display_name="Robert R")


def test_attention_blocked_and_overdue():
    assert mm.is_attention({"blocked": "Materials", "overdue": None})
    assert mm.is_attention({"blocked": None, "overdue": "Overdue"})
    assert not mm.is_attention({"blocked": "Clear", "overdue": ""})
    assert not mm.is_attention({"blocked": None, "overdue": None})


def test_assert_no_financial_keys():
    mm.assert_no_financial_keys({"my_projects": [{"name": "x", "stage": "Hang"}]})
    try:
        mm.assert_no_financial_keys({"board_counts": 12})
        raise AssertionError("should have refused")
    except AssertionError as e:
        assert "financial" in str(e).lower()


def main():
    tests = [
        test_morning_is_baseline_feature,
        test_financial_ids_hard_excluded,
        test_relevance_by_email,
        test_relevance_by_display_name,
        test_attention_blocked_and_overdue,
        test_assert_no_financial_keys,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    if failed:
        print(f"\n{failed} failed")
        sys.exit(1)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
