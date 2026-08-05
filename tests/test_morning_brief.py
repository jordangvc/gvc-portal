"""
Morning Brief — pure rules (relevance, attention, financial strip).
=========================================================================
Self-running:  python tests/test_morning_brief.py
"""
import os
import re
import sys
from pathlib import Path

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


def test_people_uses_column_text_not_entity_name():
    """Monday PeopleEntity has id+kind only — names come from column text.

    Regression for the live 502: querying ``persons_and_teams { name }``
    fails with "Cannot query field 'name' on type 'PeopleEntity'".
    """
    frag = mm._VALUE_FRAGMENT
    assert "persons_and_teams" in frag
    assert "kind" in frag
    # Must not ask PeopleEntity for name (column-level `text` is fine).
    assert "persons_and_teams { id name }" not in frag.replace("\n", " ")
    assert "persons_and_teams { id kind }" in " ".join(frag.split())

    owners = mm._people({
        "text": "Mark W, Robert R",
        "persons_and_teams": [
            {"id": "11", "kind": "person"},
            {"id": "22", "kind": "person"},
        ],
    })
    assert owners == [
        {"id": "11", "name": "Mark W", "email": None, "kind": "person"},
        {"id": "22", "name": "Robert R", "email": None, "kind": "person"},
    ]
    # Text-only fallback still personalizes via ops_owner_text.
    owners2 = mm._people({"text": "Ethan", "persons_and_teams": []})
    assert owners2 == [{"id": None, "name": "Ethan", "email": None, "kind": None}]


def test_normalize_ops_owner_from_people_value():
    item = {
        "id": "99",
        "name": "Hang drywall",
        "group": {"id": "topics", "title": "Active"},
        "updated_at": "2026-07-01T12:00:00Z",
        "column_values": [
            {
                "id": boards.MORNING_COL_OPS_OWNER,
                "text": "Jordan Faulkner",
                "type": "people",
                "persons_and_teams": [{"id": "7", "kind": "person"}],
            },
            {
                "id": boards.MORNING_COL_BLOCKED,
                "text": "Clear",
                "type": "status",
            },
        ],
    }
    row = mm._normalize(item)
    assert row is not None
    assert row["ops_owners"][0]["name"] == "Jordan Faulkner"
    assert row["ops_owner_text"] == "Jordan Faulkner"
    assert row["updated_at"] == "2026-07-01T12:00:00Z"
    assert mm.is_personally_relevant(
        row, email="jordan@greenvalleycontractors.com",
        display_name="Jordan Faulkner")


def test_normalize_keeps_updated_at_for_long_term_holds():
    """Regression: holds stayed empty because _normalize dropped updated_at."""
    from datetime import datetime, timezone
    item = {
        "id": "55",
        "name": "Waiting permit",
        "updated_at": "2026-07-20T10:00:00Z",
        "group": {"id": "topics", "title": "Active"},
        "column_values": [
            {"id": boards.MORNING_COL_BLOCKED, "text": "Permit", "type": "status"},
            {"id": boards.MORNING_COL_OVERDUE, "text": "", "type": "status"},
        ],
    }
    row = mm._normalize(item)
    assert row is not None
    assert row["updated_at"] == "2026-07-20T10:00:00Z"
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    assert mm.is_attention(row)
    assert mm.is_long_term_hold(row, now=now)


def test_fetch_query_requests_updated_at():
    src = Path(__file__).resolve().parents[1] / "adapters" / "monday" / "morning.py"
    text = src.read_text(encoding="utf-8")
    assert re.search(r"items\s*\{[\s\S]*?updated_at[\s\S]*?column_values", text)


def test_normalize_needs_from_jordan_column():
    item = {
        "id": "77",
        "name": "Need a call",
        "updated_at": "2026-08-01T12:00:00Z",
        "group": {"id": "topics", "title": "Active"},
        "column_values": [
            {"id": boards.MORNING_COL_NEEDS_FROM_JORDAN,
             "text": "Pricing", "type": "status"},
            {"id": boards.MORNING_COL_BLOCKED, "text": "Clear", "type": "status"},
        ],
    }
    row = mm._normalize(item)
    assert row is not None
    assert row["needs_from_jordan"] == "Pricing"
    assert mm.active_needs_from_jordan(row) == "Pricing"
    assert mm.active_needs_from_jordan({"needs_from_jordan": "Clear"}) is None
    assert boards.MORNING_COL_NEEDS_FROM_JORDAN in boards.MORNING_READ_COLUMN_IDS


def main():
    tests = [
        test_morning_is_baseline_feature,
        test_financial_ids_hard_excluded,
        test_relevance_by_email,
        test_relevance_by_display_name,
        test_attention_blocked_and_overdue,
        test_assert_no_financial_keys,
        test_people_uses_column_text_not_entity_name,
        test_normalize_ops_owner_from_people_value,
        test_normalize_keeps_updated_at_for_long_term_holds,
        test_fetch_query_requests_updated_at,
        test_normalize_needs_from_jordan_column,
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
