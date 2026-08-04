"""Unit tests for adapters/monday/search.py (no Monday network)."""
from __future__ import annotations

import types

from adapters.monday import cache as monday_cache
from adapters.monday import search as ms


def setup_function() -> None:
    monday_cache.clear()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_tokenize_query_splits_and_drops_short():
    assert ms.tokenize_query("") == []
    assert ms.tokenize_query("  a  ") == []
    assert ms.tokenize_query("9761 Gertrude, Cincinnati OH") == [
        "9761", "gertrude", "cincinnati", "oh",
    ]
    assert ms.tokenize_query("Steele Properties | Greg Gavin") == [
        "steele", "properties", "greg", "gavin",
    ]


def test_score_match_prefers_exact_project_number():
    exact = {
        "name": "Other Job",
        "project_number": "2026-0804-001",
        "match_fields": ["project_number"],
        "builder": "",
        "supervisor": "",
        "location": "",
    }
    name_hit = {
        "name": "2026-0804-001 something",
        "project_number": "",
        "match_fields": ["name"],
        "builder": "",
        "supervisor": "",
        "location": "",
    }
    q = "2026-0804-001"
    assert ms.score_match(q, exact, id_field="project_number") > \
        ms.score_match(q, name_hit, id_field="project_number")


def test_score_match_estimate_number_and_empty_q():
    row = {
        "name": "Bid A",
        "estimate_number": "2026-0729-003",
        "match_fields": ["estimate_number"],
    }
    assert ms.score_match("2026-0729-003", row, id_field="estimate_number") >= 1000
    assert ms.score_match("", row, id_field="estimate_number") == 0.0
    assert ms.score_match("   ", row, id_field="estimate_number") == 0.0


def test_shape_project_item_maps_columns():
    item = {
        "id": "42",
        "name": "9761 Gertrude | Jent",
        "group": {"title": "Active"},
        "column_values": [
            {"id": ms.P_COL_PROJECT_NUMBER, "text": "2026-0801-002"},
            {"id": ms.P_COL_BUILDER, "text": "Jent Construction"},
            {"id": ms.P_COL_SUPERVISOR, "text": "Rob"},
            {"id": ms.P_COL_LOCATION, "text": "9761 Gertrude, Cincinnati OH"},
            {"id": ms.P_COL_INVOICE_STATUS, "text": "Ready"},
        ],
    }
    row = ms.shape_project_item(item, ["builder", "location"])
    assert row["item_id"] == 42
    assert row["name"] == "9761 Gertrude | Jent"
    assert row["group"] == "Active"
    assert row["project_number"] == "2026-0801-002"
    assert row["builder"] == "Jent Construction"
    assert row["supervisor"] == "Rob"
    assert row["location"] == "9761 Gertrude, Cincinnati OH"
    assert row["invoice_status"] == "Ready"
    assert row["match_fields"] == ["builder", "location"]
    assert str(ms.PROJECTS_BOARD_ID) in row["url"]
    assert row["url"].endswith("/pulses/42")


def test_shape_bid_item_maps_columns():
    item = {
        "id": "99",
        "name": "937 Madison Ridge | Steele",
        "column_values": [
            {"id": ms.B_COL_ESTIMATE_NUMBER, "text": "2026-0714-010"},
            {"id": ms.B_COL_STAGE, "text": "Accepted"},
            {"id": ms.B_COL_LOCATION, "text": "937 Madison Ridge Lawrenceburg"},
            {"id": ms.B_COL_CUSTOMER, "text": "Steele Properties"},
        ],
    }
    row = ms.shape_bid_item(item, ["customer"])
    assert row["item_id"] == 99
    assert row["estimate_number"] == "2026-0714-010"
    assert row["stage"] == "Accepted"
    assert row["location"] == "937 Madison Ridge Lawrenceburg"
    assert row["customer"] == "Steele Properties"
    assert row["match_fields"] == ["customer"]
    assert str(ms.BID_BOARD_ID) in row["url"]


def test_merge_leg_hits_accumulates_match_fields():
    results: dict[int, dict] = {}
    item = {
        "id": "7",
        "name": "Job Seven",
        "group": {"title": "G"},
        "column_values": [
            {"id": ms.P_COL_PROJECT_NUMBER, "text": "PN-7"},
            {"id": ms.P_COL_BUILDER, "text": "Acme"},
            {"id": ms.P_COL_SUPERVISOR, "text": ""},
            {"id": ms.P_COL_LOCATION, "text": "Cincinnati"},
            {"id": ms.P_COL_INVOICE_STATUS, "text": ""},
        ],
    }
    ms.merge_leg_hits(results, [item], "builder", ms.shape_project_item)
    ms.merge_leg_hits(results, [item], "location", ms.shape_project_item)
    ms.merge_leg_hits(results, [item], "builder", ms.shape_project_item)  # no dupe
    assert list(results) == [7]
    assert results[7]["match_fields"] == ["builder", "location"]
    assert results[7]["builder"] == "Acme"


def test_rank_and_cap_orders_and_limits():
    rows = [
        {"name": "B", "project_number": "", "match_fields": ["name"],
         "builder": "", "supervisor": "", "location": ""},
        {"name": "A", "project_number": "EXACT-1", "match_fields": ["project_number"],
         "builder": "", "supervisor": "", "location": ""},
        {"name": "C", "project_number": "", "match_fields": ["builder"],
         "builder": "EXACT-1 Builder", "supervisor": "", "location": ""},
    ]
    out = ms.rank_and_cap(rows, "EXACT-1", id_field="project_number", limit=2)
    assert len(out) == 2
    assert out[0]["project_number"] == "EXACT-1"
    assert out[0]["name"] == "A"


# ---------------------------------------------------------------------------
# Public API — empty / short query + cached uncached path
# ---------------------------------------------------------------------------

def test_search_projects_rich_empty_and_short_q():
    class Boom:
        def _query(self, *a, **k):
            raise AssertionError("should not call Monday")

    assert ms.search_projects_rich(Boom(), "") == []
    assert ms.search_projects_rich(Boom(), "x") == []
    assert ms.search_bids_rich(Boom(), " ") == []
    assert ms.search_bids_rich(Boom(), "a") == []


def _board_page(*items):
    return {"boards": [{"items_page": {"items": list(items)}}]}


def test_search_projects_rich_uncached_merges_legs(monkeypatch):
    """Fake MondayClient routes by column_id; merge + ranking asserted."""
    item_name = {
        "id": "1",
        "name": "21435 Abbys | Greg Gavin",
        "group": {"title": "Active"},
        "column_values": [
            {"id": ms.P_COL_PROJECT_NUMBER, "text": "2026-0101-001"},
            {"id": ms.P_COL_BUILDER, "text": "Greg Gavin"},
            {"id": ms.P_COL_SUPERVISOR, "text": "Mark"},
            {"id": ms.P_COL_LOCATION, "text": "21435 Abbys Way, Cincinnati OH"},
            {"id": ms.P_COL_INVOICE_STATUS, "text": "Open"},
        ],
    }
    item_builder_only = {
        "id": "2",
        "name": "Other | Greg Gavin",
        "group": {"title": "Active"},
        "column_values": [
            {"id": ms.P_COL_PROJECT_NUMBER, "text": "2026-0102-002"},
            {"id": ms.P_COL_BUILDER, "text": "Greg Gavin Homes"},
            {"id": ms.P_COL_SUPERVISOR, "text": ""},
            {"id": ms.P_COL_LOCATION, "text": "Somewhere KY"},
            {"id": ms.P_COL_INVOICE_STATUS, "text": ""},
        ],
    }
    item_exact_num = {
        "id": "3",
        "name": "Unrelated",
        "group": {"title": "Done"},
        "column_values": [
            {"id": ms.P_COL_PROJECT_NUMBER, "text": "Greg"},
            {"id": ms.P_COL_BUILDER, "text": ""},
            {"id": ms.P_COL_SUPERVISOR, "text": ""},
            {"id": ms.P_COL_LOCATION, "text": ""},
            {"id": ms.P_COL_INVOICE_STATUS, "text": ""},
        ],
    }

    by_col = {
        "name": _board_page(item_name),
        ms.P_COL_PROJECT_NUMBER: _board_page(item_exact_num),
        ms.P_COL_BUILDER: _board_page(item_name, item_builder_only),
        ms.P_COL_SUPERVISOR: _board_page(),
        ms.P_COL_LOCATION: _board_page(item_name),
    }
    calls: list[str] = []

    class FakeClient:
        def __init__(self, token=None):
            self.session = types.SimpleNamespace(
                headers={"Authorization": token or "t"}
            )

        def _query(self, query, variables):
            col = variables["columnId"]
            calls.append(col)
            return by_col.get(col, _board_page())

    monkeypatch.setattr(ms, "MondayClient", FakeClient)
    seed = FakeClient()
    out = ms._search_projects_rich_uncached(seed, "Greg", limit=10)

    assert set(calls) >= {
        "name", ms.P_COL_PROJECT_NUMBER, ms.P_COL_BUILDER,
        ms.P_COL_SUPERVISOR, ms.P_COL_LOCATION,
    }
    by_id = {r["item_id"]: r for r in out}
    assert set(by_id) == {1, 2, 3}
    # Exact project# "Greg" ranks first.
    assert out[0]["item_id"] == 3
    assert out[0]["match_fields"] == ["project_number"]
    assert "builder" in by_id[1]["match_fields"]
    assert "location" in by_id[1]["match_fields"]
    assert "name" in by_id[1]["match_fields"]
    assert by_id[2]["match_fields"] == ["builder"]


def test_search_projects_rich_leg_failure_is_graceful(monkeypatch):
    item = {
        "id": "5",
        "name": "Cincy Job",
        "group": {"title": "G"},
        "column_values": [
            {"id": ms.P_COL_PROJECT_NUMBER, "text": ""},
            {"id": ms.P_COL_BUILDER, "text": ""},
            {"id": ms.P_COL_SUPERVISOR, "text": ""},
            {"id": ms.P_COL_LOCATION, "text": "Cincinnati"},
            {"id": ms.P_COL_INVOICE_STATUS, "text": ""},
        ],
    }

    class FakeClient:
        def __init__(self, token=None):
            self.session = types.SimpleNamespace(headers={"Authorization": "t"})

        def _query(self, query, variables):
            col = variables["columnId"]
            if col == ms.P_COL_BUILDER:
                raise RuntimeError("monday 500")
            if col == ms.P_COL_LOCATION:
                return _board_page(item)
            return _board_page()

    monkeypatch.setattr(ms, "MondayClient", FakeClient)
    out = ms._search_projects_rich_uncached(FakeClient(), "Cincinnati", limit=5)
    assert len(out) == 1
    assert out[0]["item_id"] == 5
    assert out[0]["match_fields"] == ["location"]


def test_search_bids_rich_uncached_and_public_cache(monkeypatch):
    item = {
        "id": "11",
        "name": "937 Madison | Steele",
        "column_values": [
            {"id": ms.B_COL_ESTIMATE_NUMBER, "text": "2026-0714-010"},
            {"id": ms.B_COL_STAGE, "text": "Sent to Client"},
            {"id": ms.B_COL_LOCATION, "text": "937 Madison Ridge"},
            {"id": ms.B_COL_CUSTOMER, "text": "Steele Properties"},
        ],
    }
    by_col = {
        "name": _board_page(),
        ms.B_COL_ESTIMATE_NUMBER: _board_page(item),
        ms.B_COL_LOCATION: _board_page(item),
        ms.B_COL_CUSTOMER: _board_page(item),
    }
    n_queries = {"n": 0}

    class FakeClient:
        def __init__(self, token=None):
            self.session = types.SimpleNamespace(headers={"Authorization": "t"})

        def _query(self, query, variables):
            n_queries["n"] += 1
            return by_col.get(variables["columnId"], _board_page())

    monkeypatch.setattr(ms, "MondayClient", FakeClient)
    seed = FakeClient()
    out = ms._search_bids_rich_uncached(seed, "2026-0714-010", limit=5)
    assert len(out) == 1
    assert out[0]["estimate_number"] == "2026-0714-010"
    assert set(out[0]["match_fields"]) >= {"estimate_number"}
    assert out[0]["customer"] == "Steele Properties"

    # Public API caches — second call must not re-hit Monday.
    before = n_queries["n"]
    a = ms.search_bids_rich(seed, "2026-0714-010", limit=5)
    mid = n_queries["n"]
    b = ms.search_bids_rich(seed, "2026-0714-010", limit=5)
    assert a == b
    assert n_queries["n"] == mid  # cache hit
    assert mid > before           # first public call did work


def test_column_id_constants_match_known_defaults():
    assert ms.P_COL_PROJECT_NUMBER == "text_mm4fvj91"
    assert ms.P_COL_BUILDER == "text"
    assert ms.P_COL_SUPERVISOR == "text5"
    assert ms.P_COL_LOCATION == "location5"
    assert ms.P_COL_INVOICE_STATUS == "status0"
    assert ms.B_COL_ESTIMATE_NUMBER == "numbers18"
    assert ms.B_COL_LOCATION == "location5"
    assert ms.B_COL_STAGE == "deal_stage"
    assert ms.B_COL_CUSTOMER == "connect_boards5"
