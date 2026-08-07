"""Focused tests for the Bid Board bulk-rename script."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import backfill_job_rename_bids as bids
from shared.boards import BID_BOARD_ID


STANDARD_SILVA = (
    "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence"
)
STANDARD_SUSANNA = (
    "3776 Susanna, Lawrenceburg, IN 47025 | Martin | Martin residence"
)


def _column(column_id: str, text: str, **extra) -> dict:
    return {"id": column_id, "text": text, **extra}


class FakeMonday:
    def __init__(self, pages: list[dict]):
        self.pages = list(pages)
        self.calls: list[dict] = []

    def _query(self, query: str, variables: dict) -> dict:
        self.calls.append({"query": query, "variables": variables})
        return self.pages.pop(0)


def test_fetch_pages_skips_dead_and_prioritizes_accepted_won():
    first_page = {
        "boards": [{
            "items_page": {
                "cursor": "page-2",
                "items": [
                    {
                        "id": "1",
                        "name": "100 Main | Open Builder | Open remodel",
                        "column_values": [
                            _column(bids.JOBSTART_BID_STAGE_COL, "Estimate Sent"),
                        ],
                    },
                    {
                        "id": "2",
                        "name": "200 Main | Lost Builder | Lost remodel",
                        "column_values": [
                            _column(bids.JOBSTART_BID_STAGE_COL, "Project Lost"),
                        ],
                    },
                    {
                        "id": "3",
                        "name": "300 Main | Accepted Builder | Accepted remodel",
                        "column_values": [
                            _column(
                                bids.JOBSTART_BID_LOCATION_COL,
                                "",
                                value=json.dumps({
                                    "address": (
                                        "300 Main Street, Cincinnati, OH 45202"
                                    ),
                                }),
                            ),
                            _column(bids.JOBSTART_BID_STAGE_COL, "Accepted"),
                        ],
                    },
                ],
            },
        }],
    }
    second_page = {
        "boards": [{
            "items_page": {
                "cursor": None,
                "items": [{
                    "id": "4",
                    "name": "400 Main | Won Builder | Won remodel",
                    "column_values": [
                        _column(bids.JOBSTART_BID_STAGE_COL, "Won Deal"),
                    ],
                }],
            },
        }],
    }
    monday = FakeMonday([first_page, second_page])

    rows, dead_skipped = bids.fetch_bid_rows(monday, limit=2)

    assert [row["item_id"] for row in rows] == [3, 4]
    assert dead_skipped == 1
    assert len(monday.calls) == 2
    assert "value" in monday.calls[0]["query"]
    assert monday.calls[0]["variables"] == {
        "boardId": [str(BID_BOARD_ID)],
        "cursor": None,
        "cols": list(bids.BID_READ_COLUMNS),
    }
    assert monday.calls[1]["variables"]["cursor"] == "page-2"
    assert "45202" in rows[0]["location_value_json"]


def test_build_plans_uses_location_and_customer_text():
    # Job title must already be in the title (or supplied via planner kwargs);
    # the bid script reads name + location + customer only.
    plans = bids.build_plans([{
        "item_id": 10,
        "name": "9195 Silva | Willow Creek | Smith residence",
        "location": "9195 Silva Drive, Cincinnati, OH 45241",
        "customer": "Willow Creek",
        "stage": "Accepted",
    }])

    assert plans[0]["action"] == "rename"
    assert plans[0]["new_name"] == STANDARD_SILVA
    assert plans[0]["board"] == "bid_board"
    assert plans[0]["stage"] == "Accepted"


def test_build_plans_uses_location_json_before_geocoding():
    plans = bids.build_plans([{
        "item_id": 10,
        "name": "9195 Silva | Willow Creek | Smith residence",
        "location": "",
        "location_value_json": json.dumps({
            "lat": "39.246",
            "lng": "-84.312",
            "address": "9195 Silva Drive, Cincinnati, OH 45241",
        }),
        "customer": "Willow Creek",
        "stage": "Accepted",
    }])

    assert plans[0]["action"] == "rename"
    assert plans[0]["new_name"] == STANDARD_SILVA
    assert plans[0]["lookup_sources"] == ["monday_location"]


def test_build_plans_geocodes_incomplete_bid():
    def fixed_cincinnati(street: str) -> dict:
        assert street == "9195 Silva"
        return {
            "street": "9195 Silva Drive",
            "city": "Cincinnati",
            "state": "OH",
            "zip": "45241",
            "hint": "9195 Silva Drive, Cincinnati, OH 45241",
            "display_name": "9195 Silva Drive, Cincinnati, Ohio",
        }

    plans = bids.build_plans(
        [{
            "item_id": 10,
            "name": "9195 Silva | Willow Creek | Smith residence",
            "location": "",
            "location_value_json": "",
            "customer": "Willow Creek",
            "stage": "Accepted",
        }],
        geocode_street_fn=fixed_cincinnati,
        reverse_geocode_fn=lambda *_args: None,
    )

    assert plans[0]["action"] == "rename"
    assert plans[0]["new_name"] == STANDARD_SILVA
    assert "nominatim_tri_state" in plans[0]["lookup_sources"]


def test_apply_renames_candidates_and_waits_between_writes(monkeypatch):
    rows = [
        {
            "item_id": 10,
            "name": "9195 Silva | Willow Creek | Smith residence",
            "location": "9195 Silva Drive, Cincinnati, OH 45241",
            "customer": "Willow Creek",
            "stage": "Accepted",
        },
        {
            "item_id": 11,
            "name": "3776 Susanna | Martin | Martin residence",
            "location": "3776 Susanna, Lawrenceburg, IN 47025",
            "customer": "Martin",
            "stage": "Won",
        },
        {
            # already 3-part standard — skip_standard, not written
            "item_id": 12,
            "name": (
                "1 Oak Street, Cincinnati, OH 45202 | Acme | Office remodel"
            ),
            "location": "",
            "customer": "Acme",
            "stage": "Estimate Sent",
        },
    ]
    writes: list[tuple] = []
    sleeps: list[float] = []
    monkeypatch.setattr(
        bids, "fetch_bid_rows", lambda mc, limit=None: (rows, 4))
    monkeypatch.setattr(
        bids,
        "rename_item_name",
        lambda mc, board_id, item_id, name:
            writes.append((mc, board_id, item_id, name)),
    )
    monkeypatch.setattr(bids.time, "sleep", sleeps.append)
    monday = object()

    result = bids.run(apply=True, limit=None, mc=monday)

    assert result == 0
    assert [(write[1], write[2]) for write in writes] == [
        (BID_BOARD_ID, 10),
        (BID_BOARD_ID, 11),
    ]
    assert writes[0][3] == STANDARD_SILVA
    assert writes[1][3] == STANDARD_SUSANNA
    assert sleeps == [0.2]


def test_main_dry_run_wins_when_both_flags_are_set(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        bids,
        "run",
        lambda **kwargs: calls.append(kwargs) or 0,
    )

    result = bids.main(["--apply", "--dry-run", "--limit", "7"])

    assert result == 0
    assert calls == [{"apply": False, "limit": 7, "geocode": True}]


def test_main_no_geocode_disables_nominatim(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        bids,
        "run",
        lambda **kwargs: calls.append(kwargs) or 0,
    )

    assert bids.main(["--no-geocode"]) == 0
    assert calls == [{"apply": False, "limit": None, "geocode": False}]


def test_limit_must_be_positive():
    parser = bids._build_parser()

    try:
        parser.parse_args(["--limit", "0"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("--limit 0 should be rejected")
