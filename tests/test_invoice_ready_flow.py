"""P5 Ready-to-Invoice consumer tests — company pricing + dry-run zero-writes."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import pricing as gvc_pricing
from orchestrators.invoice_ready_flow import (
    build_item_worksheet,
    check_ready_to_invoice,
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
    """Swap monday adapters for fakes; return a restore callable.

    Earlier versions left stub modules in ``sys.modules`` and poisoned later
    tests that expect the real ``adapters.monday.client.MondayClient``.
    """
    saved = {k: sys.modules.get(k) for k in _STUB_KEYS}
    billing = types.ModuleType("adapters.monday.billing")
    billing.fetch_ready_to_invoice = lambda _mc: fake_rows
    payroll_mod = types.ModuleType("adapters.monday.payroll")
    payroll_mod.fetch_payroll_for_project = lambda _mc, _pid: fake_payroll
    client_mod = types.ModuleType("adapters.monday.client")
    client_mod.MondayClient = lambda: mc
    monday_pkg = sys.modules.get("adapters.monday") or types.ModuleType("adapters.monday")
    adapters_pkg = sys.modules.get("adapters") or types.ModuleType("adapters")
    sys.modules["adapters"] = adapters_pkg
    sys.modules["adapters.monday"] = monday_pkg
    sys.modules["adapters.monday.billing"] = billing
    sys.modules["adapters.monday.payroll"] = payroll_mod
    sys.modules["adapters.monday.client"] = client_mod

    def _restore() -> None:
        for key, prior in saved.items():
            if prior is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = prior

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
