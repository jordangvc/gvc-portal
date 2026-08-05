"""Focused tests for the Bid Board bulk-rename script."""
from __future__ import annotations

from scripts import backfill_job_rename_bids as bids
from shared.boards import BID_BOARD_ID


def _column(column_id: str, text: str) -> dict:
    return {"id": column_id, "text": text}


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
                        "name": "100 Main | Open Builder",
                        "column_values": [
                            _column(bids.JOBSTART_BID_STAGE_COL, "Estimate Sent"),
                        ],
                    },
                    {
                        "id": "2",
                        "name": "200 Main | Lost Builder",
                        "column_values": [
                            _column(bids.JOBSTART_BID_STAGE_COL, "Project Lost"),
                        ],
                    },
                    {
                        "id": "3",
                        "name": "300 Main | Accepted Builder",
                        "column_values": [
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
                    "name": "400 Main | Won Builder",
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
    assert monday.calls[0]["variables"] == {
        "boardId": [str(BID_BOARD_ID)],
        "cursor": None,
        "cols": list(bids.BID_READ_COLUMNS),
    }
    assert monday.calls[1]["variables"]["cursor"] == "page-2"


def test_build_plans_uses_location_and_customer_text():
    plans = bids.build_plans([{
        "item_id": 10,
        "name": "9195 Silva",
        "location": "9195 Silva Drive, Cincinnati, OH 45241",
        "customer": "Willow Creek",
        "stage": "Accepted",
    }])

    assert plans[0]["action"] == "rename"
    assert plans[0]["new_name"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert plans[0]["board"] == "bid_board"
    assert plans[0]["stage"] == "Accepted"


def test_apply_renames_candidates_and_waits_between_writes(monkeypatch):
    rows = [
        {
            "item_id": 10,
            "name": "9195 Silva",
            "location": "9195 Silva Drive, Cincinnati, OH 45241",
            "customer": "Willow Creek",
            "stage": "Accepted",
        },
        {
            "item_id": 11,
            "name": "3776 Susanna",
            "location": "3776 Susanna, Lawrenceburg, IN 47025",
            "customer": "Martin",
            "stage": "Won",
        },
        {
            "item_id": 12,
            "name": "1 Oak Street, Cincinnati, OH 45202 | Acme",
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
    assert writes[0][3] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert writes[1][3] == (
        "3776 Susanna, Lawrenceburg, IN 47025 | Martin")
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
    assert calls == [{"apply": False, "limit": 7}]


def test_limit_must_be_positive():
    parser = bids._build_parser()

    try:
        parser.parse_args(["--limit", "0"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("--limit 0 should be rejected")
