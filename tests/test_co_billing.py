"""Unit tests for invoice ← CO billing (top-level CO item model)."""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock, patch

import adapters.monday.co as monday_co


class _FakeMC:
    def __init__(self, items: list[dict]):
        self.items = items
        self.queries: list[tuple[str, dict]] = []
        self.updated: list[dict] = []

    def _query(self, query: str, variables: Optional[dict] = None) -> dict:
        self.queries.append((query, variables or {}))
        # list_co_items path
        if "items_page" in query:
            return {"boards": [{"items_page": {"items": self.items}}]}
        return {}


def _co_item(item_id: int, identifier: str, status: str | None, amount: str | None = None, name: str = "") -> dict:
    cols = [
        {"id": monday_co.P_COL_PROJECT_NUMBER, "text": identifier},
        {"id": monday_co.CO_ITEM_COL_STATUS, "text": status or ""},
        {"id": monday_co.CO_ITEM_COL_AMOUNT, "text": amount or ""},
    ]
    return {"id": str(item_id), "name": name or identifier, "column_values": cols}


def test_is_unbilled_co_status_filters_billed_and_void():
    assert monday_co._is_unbilled_co_status(None) is True
    assert monday_co._is_unbilled_co_status("") is True
    assert monday_co._is_unbilled_co_status("Drafted") is True
    assert monday_co._is_unbilled_co_status("Approved") is True
    assert monday_co._is_unbilled_co_status("Sent") is True
    assert monday_co._is_unbilled_co_status("Billed") is False
    assert monday_co._is_unbilled_co_status("billed") is False
    assert monday_co._is_unbilled_co_status("Void") is False


def test_list_unbilled_co_items_returns_only_open_top_level_cos():
    base = "2026-0616-B2"
    mc = _FakeMC([
        _co_item(11, f"CO.1-{base}", "Approved", "1500.00", "CO.1 - Demo"),
        _co_item(12, f"CO.2-{base}", "Billed", "200.00", "CO.2 - Demo"),
        _co_item(13, f"CO.3-{base}", "Void", "50", "CO.3 - Demo"),
        _co_item(14, f"CO.4-{base}", "Drafted", "99.5", "CO.4 - Demo"),
        _co_item(15, "CO.1-OTHER", "Approved", "1", "wrong base"),
    ])
    rows = monday_co.list_unbilled_co_items(mc, base)
    ids = [r["item_id"] for r in rows]
    assert ids == [11, 14]
    assert rows[0]["co_number"] == f"CO.1-{base}"
    assert rows[0]["amount"] == 1500.0
    assert rows[1]["amount"] == 99.5


def test_mark_billed_item_sets_top_level_status():
    calls: list[tuple] = []

    def _fake_update(mc, board_id, item_id, values, *, name=None):
        calls.append((board_id, item_id, values, name))

    with patch.object(monday_co, "_update_item", side_effect=_fake_update):
        monday_co.mark_billed_item(
            MagicMock(), 99,
            invoice_identifier="GVC-2026-0142",
            invoice_url="https://example.test/inv",
        )
        # idempotent second write
        monday_co.mark_billed_item(
            MagicMock(), 99,
            invoice_identifier="GVC-2026-0142",
            invoice_url="https://example.test/inv",
        )

    assert len(calls) == 2
    for board_id, item_id, values, name in calls:
        assert board_id == monday_co.PROJECTS_BOARD_ID
        assert item_id == 99
        assert name is None
        assert values == {monday_co.CO_ITEM_COL_STATUS: {"label": "Billed"}}


def test_mark_billed_batch_prefers_monday_item_id():
    item_calls: list[int] = []
    sub_calls: list[int] = []

    def fake_item(mc, item_id, *, invoice_identifier, invoice_url=None):
        item_calls.append(item_id)

    def fake_sub(mc, subitem_id, *, invoice_identifier, invoice_url=None):
        sub_calls.append(subitem_id)

    with patch.object(monday_co, "mark_billed_item", side_effect=fake_item), \
         patch.object(monday_co, "mark_billed", side_effect=fake_sub), \
         patch("adapters.monday.client.MondayClient", return_value=MagicMock()), \
         patch("adapters.monday.client.MondayNotConfigured", Exception):
        # Import path used inside mark_billed_batch
        import adapters.monday.client as client_mod
        with patch.object(client_mod, "MondayClient", return_value=MagicMock()):
            report = monday_co.mark_billed_batch(
                [
                    {"monday_item_id": 101, "co_number": "CO.1-X"},
                    {"monday_item_id": 101, "co_number": "CO.1-X"},  # duplicate ok
                    {"monday_subitem_id": 202, "co_number": "LEGACY.CO"},
                    {"co_number": "NO-ID"},  # ignored
                ],
                invoice_identifier="GVC-1",
                invoice_url="https://pay.example/1",
            )

    assert report["co_billed"] == ["CO.1-X", "CO.1-X", "LEGACY.CO"]
    assert report["co_billing_errors"] == []
    assert item_calls == [101, 101]
    assert sub_calls == [202]


def test_mark_billed_batch_empty_refs_is_noop():
    report = monday_co.mark_billed_batch([], invoice_identifier="GVC-1")
    assert report == {"co_billed": [], "co_billing_errors": []}
    report2 = monday_co.mark_billed_batch(
        [{"co_number": "only"}], invoice_identifier="GVC-1"
    )
    assert report2 == {"co_billed": [], "co_billing_errors": []}


def test_mark_billed_batch_skips_when_monday_not_configured():
    import adapters.monday.client as client_mod

    class _NotConfigured(Exception):
        pass

    with patch.object(client_mod, "MondayNotConfigured", _NotConfigured), \
         patch.object(client_mod, "MondayClient", side_effect=_NotConfigured("no token")):
        report = monday_co.mark_billed_batch(
            [{"monday_item_id": 1, "co_number": "CO.1-X"}],
            invoice_identifier="GVC-1",
        )
    assert report["co_billed"] == []
    assert "SKIPPED" in report.get("co_billing_status", "")


def test_mark_billed_batch_continues_after_per_co_failure():
    import adapters.monday.client as client_mod

    def boom(mc, item_id, *, invoice_identifier, invoice_url=None):
        if item_id == 1:
            raise RuntimeError("boom")
        return None

    with patch.object(client_mod, "MondayClient", return_value=MagicMock()), \
         patch.object(monday_co, "mark_billed_item", side_effect=boom):
        report = monday_co.mark_billed_batch(
            [
                {"monday_item_id": 1, "co_number": "CO.1"},
                {"monday_item_id": 2, "co_number": "CO.2"},
            ],
            invoice_identifier="GVC-1",
        )
    assert report["co_billed"] == ["CO.2"]
    assert len(report["co_billing_errors"]) == 1
    assert report["co_billing_errors"][0]["co_number"] == "CO.1"
