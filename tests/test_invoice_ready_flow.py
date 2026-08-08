"""P5 Ready-to-Invoice consumer tests — company pricing + dry-run zero-writes."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import pricing as gvc_pricing
from orchestrators.invoice_ready_flow import (
    build_item_worksheet,
    check_ready_to_invoice,
    ensure_ready_worksheet,
)


def test_by_sheet_gvc_materials_delk_shape():
    r = gvc_pricing.price_by_sheet(17263, materials_by="gvc")
    assert r["labor_rate"] == 1.17
    assert r["material_rate"] == 0.70
    assert abs(r["total"] - 32281.81) < 1.0
    assert "GVC-supplied" in r["price_label"]


def test_by_sheet_builder_materials_kavouras_shape():
    r = gvc_pricing.price_by_sheet(13199, materials_by="builder")
    assert r["material"] == 0.0
    assert abs(r["total"] - 15442.83) < 1.0
    assert "builder-supplied" in r["price_label"]


def test_tm_respects_minimum():
    r = gvc_pricing.price_tm(1.0, material_cost=10.0, include_trip=True)
    assert r["total"] == 750.0


def test_worksheet_wieland_defaults_builder_supply():
    row = {
        "item_id": 1,
        "name": "Kavouras Residence",
        "builder": "Wieland",
        "project_item_id": 99,
        "url": "https://example",
    }
    payroll = {"board_count_hint": 275, "labor_cost": 9000.0, "rows": []}
    sheet = build_item_worksheet(row, payroll)
    assert sheet["pricing"]["model"] == "by_sheet"
    assert sheet["pricing"]["materials_by"] == "builder"
    assert sheet["pricing"]["material_rate"] == 0.0
    assert sheet["auto_send"] is False
    assert abs(sheet["proposed_invoice_total"] - 15444.0) < 1.0


_STUB_KEYS = (
    "adapters.monday.billing",
    "adapters.monday.payroll",
    "adapters.monday.client",
)


def _install_monday_stubs(fake_rows, fake_payroll, mc):
    """Patch invoice_ready_flow's monday bindings; return a restore callable."""
    import orchestrators.invoice_ready_flow as flow

    saved = {
        "fetch": flow.fetch_ready_to_invoice,
        "payroll": flow.monday_payroll.fetch_payroll_for_project,
        "client": flow.MondayClient,
    }
    flow.fetch_ready_to_invoice = lambda _mc: fake_rows  # type: ignore
    flow.monday_payroll.fetch_payroll_for_project = (  # type: ignore
        lambda _mc, _pid: fake_payroll)
    flow.MondayClient = lambda: mc  # type: ignore

    def _restore() -> None:
        flow.fetch_ready_to_invoice = saved["fetch"]  # type: ignore
        flow.monday_payroll.fetch_payroll_for_project = saved["payroll"]  # type: ignore
        flow.MondayClient = saved["client"]  # type: ignore

    return _restore


def test_dry_run_zero_writes():
    fake_rows = [{
        "item_id": 11,
        "name": "TEST — Ready job",
        "builder": "JDC Homes",
        "project_item_id": 22,
        "project_number": "P-1",
        "ready_date": "2026-08-01",
        "url": "https://monday/11",
    }]
    fake_payroll = {
        "project_item_id": 22,
        "rows": [{"item_id": 1, "name": "Hang", "count": 1, "rate": 800,
                  "total": 800, "type": "labor", "square_footage": None}],
        "labor_cost": 800.0,
        "board_count_hint": 360,
        "row_count": 1,
    }
    mc = MagicMock()
    restore = _install_monday_stubs(fake_rows, fake_payroll, mc)
    try:
        out = check_ready_to_invoice(dry_run=True, limit=5, mc=mc)
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert out["auto_send"] is False
        assert out["checked"] == 1
        assert out["worksheets"] == 1
        assert out["labor_rate"] == 1.17
        assert out["material_rate"] == 0.70
        item = out["items"][0]
        assert item["status"] == "would_stage"
        assert item["model"] == "by_sheet"
        assert item["proposed_total"] > 0
    finally:
        restore()


def test_skip_without_project_link():
    fake_rows = [{"item_id": 5, "name": "Orphan", "project_item_id": None}]
    mc = MagicMock()
    restore = _install_monday_stubs(fake_rows, {}, mc)
    try:
        out = check_ready_to_invoice(dry_run=True, mc=mc)
        assert out["skipped"] == 1
        assert out["worksheets"] == 0
    finally:
        restore()


def test_live_mode_stages_worksheet_in_portal():
    """dry_run=False persists to ready_stage (memory fallback) — never Stripe."""
    from subsystems.invoice import ready_stage

    ready_stage.clear_memory_for_tests()
    fake_rows = [{
        "item_id": 11,
        "name": "TEST — Ready job",
        "builder": "JDC Homes",
        "project_item_id": 22,
        "project_number": "P-1",
        "ready_date": "2026-08-01",
        "url": "https://monday/11",
    }]
    fake_payroll = {
        "project_item_id": 22,
        "rows": [{"item_id": 1, "name": "Hang", "count": 1, "rate": 800,
                  "total": 800, "type": "labor", "square_footage": None}],
        "labor_cost": 800.0,
        "board_count_hint": 360,
        "row_count": 1,
    }
    mc = MagicMock()
    restore = _install_monday_stubs(fake_rows, fake_payroll, mc)
    try:
        out = check_ready_to_invoice(dry_run=False, limit=5, mc=mc)
        assert out["ok"] is True
        assert out["dry_run"] is False
        assert out["auto_send"] is False
        item = out["items"][0]
        assert item["status"] == "staged"
        assert item["staged"] is True
        assert item["proposed_total"] > 0
        stored = ready_stage.get_worksheet(11)
        assert stored is not None
        assert stored["proposed_invoice_total"] == item["proposed_total"]
        lines = ready_stage.worksheet_to_line_items(stored)
        assert lines
        assert all("amount" in li and "description" in li for li in lines)
        # Idempotent second sweep
        out2 = check_ready_to_invoice(dry_run=False, limit=5, mc=mc)
        assert out2["items"][0]["staged"] is True
        assert ready_stage.get_worksheet(11)["proposed_invoice_total"] == item["proposed_total"]
    finally:
        restore()
        ready_stage.clear_memory_for_tests()


def test_worksheet_to_line_items_by_sheet_and_tm():
    from subsystems.invoice import ready_stage

    by_sheet = {
        "job_name": "Demo",
        "proposed_invoice_total": 187.0,
        "pricing": {
            "model": "by_sheet",
            "ordered_board_sf": 100.0,
            "labor_rate": 1.17,
            "material_rate": 0.70,
            "labor": 117.0,
            "material": 70.0,
            "price_label": "label",
        },
    }
    lines = ready_stage.worksheet_to_line_items(by_sheet)
    assert len(lines) == 2
    assert lines[0]["amount"] == 117.0
    assert lines[1]["amount"] == 70.0

    tm = {
        "job_name": "Patch",
        "proposed_invoice_total": 750.0,
        "pricing": {"model": "tm", "price_label": "T&M", "total": 750.0},
    }
    tm_lines = ready_stage.worksheet_to_line_items(tm)
    assert len(tm_lines) == 1
    assert tm_lines[0]["amount"] == 750.0


def test_ensure_ready_worksheet_reuses_staged():
    from subsystems.invoice import ready_stage
    from unittest import mock

    ready_stage.clear_memory_for_tests()
    try:
        sheet = {
            "job_name": "Staged",
            "proposed_invoice_total": 100.0,
            "status": "staged",
            "pricing": {
                "model": "tm", "price_label": "T&M", "total": 100.0,
            },
            "lines": [],
        }
        ready_stage.save_worksheet(42, sheet, actor="test")
        with mock.patch.object(
            monday_jobcheck_mod(), "get_item_values"
        ) as get_item:
            out = ensure_ready_worksheet(42, persist=False, mc=MagicMock())
            get_item.assert_not_called()
        assert out["ok"] is True
        assert out["source"] == "staged"
        assert out["proposed_total"] == 100.0
        assert out["line_items"]
    finally:
        ready_stage.clear_memory_for_tests()


def monday_jobcheck_mod():
    import adapters.monday.jobcheck as m
    return m


def test_ensure_ready_worksheet_computes_when_unstaged():
    from subsystems.invoice import ready_stage
    from unittest import mock
    import adapters.monday.jobcheck as monday_jobcheck
    import adapters.monday.payroll as monday_payroll

    ready_stage.clear_memory_for_tests()
    mc = MagicMock()
    try:
        with mock.patch.object(
            monday_jobcheck, "get_item_values",
            return_value={
                "item_id": 7, "name": "Test Job", "url": "https://m/7",
                "group_id": "g", "group_title": "Ready", "values": {},
            },
        ), mock.patch.object(
            monday_jobcheck, "get_linked_project_gfolder",
            return_value={"project_item_id": 99, "error": None},
        ), mock.patch.object(
            monday_payroll, "fetch_payroll_for_project",
            return_value={
                "project_item_id": 99,
                "rows": [{"count": 1, "rate": 800, "square_footage": None}],
                "labor_cost": 800.0,
                "board_count_hint": 360,
            },
        ):
            out = ensure_ready_worksheet(7, persist=True, mc=mc)
        assert out["ok"] is True
        assert out["source"] == "computed"
        assert out["proposed_total"] and out["proposed_total"] > 0
        assert out["line_items"]
        assert ready_stage.get_worksheet(7) is not None
    finally:
        ready_stage.clear_memory_for_tests()


def test_ensure_ready_worksheet_no_project_link():
    from subsystems.invoice import ready_stage
    from unittest import mock
    import adapters.monday.jobcheck as monday_jobcheck

    ready_stage.clear_memory_for_tests()
    try:
        with mock.patch.object(
            monday_jobcheck, "get_item_values",
            return_value={
                "item_id": 8, "name": "Orphan", "url": "https://m/8",
                "group_id": "g", "group_title": "Ready", "values": {},
            },
        ), mock.patch.object(
            monday_jobcheck, "get_linked_project_gfolder",
            return_value={"project_item_id": None, "error": "empty"},
        ):
            out = ensure_ready_worksheet(8, persist=False, mc=MagicMock())
        assert out["ok"] is False
        assert out["code"] == "NO_PROJECT_LINK"
        assert "/ui/jobcheck?item=8" in (out.get("jobcheck_href") or "")
    finally:
        ready_stage.clear_memory_for_tests()


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    raise SystemExit(1 if failed else 0)
