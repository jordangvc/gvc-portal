"""Unit tests for the estimate -> invoice dollar import helpers (offline, no I/O)."""
from __future__ import annotations

from subsystems.invoice import estimate_import as ei


# ---------------------------------------------------------------------------
# parse_monday_total
# ---------------------------------------------------------------------------

def test_parse_monday_total_numeric_passthrough():
    assert ei.parse_monday_total(12345) == 12345.0
    assert ei.parse_monday_total(1234.5) == 1234.5


def test_parse_monday_total_currency_text():
    assert ei.parse_monday_total("$12,345.00") == 12345.0
    assert ei.parse_monday_total("12345.6") == 12345.6
    assert ei.parse_monday_total(" 1,234 ") == 1234.0


def test_parse_monday_total_negative():
    assert ei.parse_monday_total("-500.25") == -500.25


def test_parse_monday_total_missing_or_junk():
    assert ei.parse_monday_total(None) is None
    assert ei.parse_monday_total("") is None
    assert ei.parse_monday_total("   ") is None
    assert ei.parse_monday_total("n/a") is None
    assert ei.parse_monday_total(True) is None
    assert ei.parse_monday_total(False) is None


# ---------------------------------------------------------------------------
# map_line_items
# ---------------------------------------------------------------------------

def _est_line(**kw):
    base = {"description": "Drywall", "unit_price": 100.0, "quantity": 2}
    base.update(kw)
    return base


def test_map_line_items_computes_amount_from_unit_price_and_quantity():
    out = ei.map_line_items([_est_line()])
    assert out == [{
        "description": "Drywall", "amount": 200.0,
        "quantity": 2.0, "unit_price": 100.0, "optional": False,
    }]


def test_map_line_items_skips_optional_by_default():
    items = [_est_line(description="Base"), _est_line(description="Extra", optional=True)]
    out = ei.map_line_items(items)
    assert [li["description"] for li in out] == ["Base"]


def test_map_line_items_include_optional_true():
    items = [_est_line(description="Base"), _est_line(description="Extra", optional=True)]
    out = ei.map_line_items(items, include_optional=True)
    assert [li["description"] for li in out] == ["Base", "Extra"]
    assert out[1]["optional"] is True


def test_map_line_items_defaults_quantity_to_one():
    out = ei.map_line_items([{"description": "Flat fee", "unit_price": 500}])
    assert out[0]["amount"] == 500.0
    assert out[0]["quantity"] == 1.0


def test_map_line_items_tolerates_bad_numbers_and_non_dicts():
    out = ei.map_line_items([
        "not a dict",
        {"description": "Bad price", "unit_price": "n/a", "quantity": "x"},
    ])
    assert len(out) == 1
    assert out[0]["amount"] == 0.0


def test_map_line_items_empty_or_none_input():
    assert ei.map_line_items(None) == []
    assert ei.map_line_items([]) == []


def test_map_line_items_uses_name_fallback_for_description():
    out = ei.map_line_items([{"name": "Fallback name", "unit_price": 10, "quantity": 1}])
    assert out[0]["description"] == "Fallback name"


# ---------------------------------------------------------------------------
# build_estimate_import
# ---------------------------------------------------------------------------

def test_build_estimate_import_neither_source_available():
    out = ei.build_estimate_import(estimate_number="EST-2026-0804-003")
    assert out["available"] is False
    assert out["source"] == "none"
    assert out["line_items"] == []
    assert out["line_items_total"] is None
    assert out["monday_total"] is None
    assert out["notes"]  # explains why nothing is available


def test_build_estimate_import_monday_total_only():
    out = ei.build_estimate_import(
        estimate_number="EST-2026-0804-003", monday_total="$18,200.00",
    )
    assert out["available"] is True
    assert out["source"] == "monday_only"
    assert out["monday_total"] == 18200.0
    assert out["line_items"] == []
    assert out["line_items_total"] is None
    assert out["notes"] == []


def test_build_estimate_import_sidecar_supplies_line_items_and_wins_number():
    sidecar = {
        "estimate": {
            "identifier": "EST-2026-0804-003",
            "line_items": [
                {"description": "ACT", "unit_price": 15200.0, "quantity": 1},
                {"description": "Drywall", "unit_price": 18300.0, "quantity": 1},
                {"description": "Paint", "unit_price": 6400.0, "quantity": 1, "optional": True},
            ],
        }
    }
    out = ei.build_estimate_import(monday_total="$40,100.00", sidecar=sidecar)
    assert out["available"] is True
    assert out["source"] == "sidecar"
    assert out["estimate_number"] == "EST-2026-0804-003"
    assert [li["description"] for li in out["line_items"]] == ["ACT", "Drywall"]
    assert out["line_items_total"] == 33500.0
    assert out["monday_total"] == 40100.0  # still surfaced alongside the lines


def test_build_estimate_import_sidecar_present_but_no_billable_lines_falls_back_to_monday():
    sidecar = {"estimate": {"identifier": "EST-2026-0804-003", "line_items": [
        {"description": "Optional add-on", "unit_price": 100.0, "quantity": 1, "optional": True},
    ]}}
    out = ei.build_estimate_import(monday_total="1000", sidecar=sidecar)
    assert out["line_items"] == []
    assert out["source"] == "monday_only"
    assert out["available"] is True
    assert out["estimate_number"] == "EST-2026-0804-003"


def test_build_estimate_import_explicit_estimate_number_wins_over_sidecar_identifier():
    sidecar = {"estimate": {"identifier": "EST-OLD", "line_items": [
        {"description": "A", "unit_price": 1, "quantity": 1},
    ]}}
    out = ei.build_estimate_import(estimate_number="EST-2026-0804-003", sidecar=sidecar)
    assert out["estimate_number"] == "EST-2026-0804-003"


def test_build_estimate_import_include_optional_flag_forwarded():
    sidecar = {"estimate": {"line_items": [
        {"description": "Optional", "unit_price": 100.0, "quantity": 1, "optional": True},
    ]}}
    out = ei.build_estimate_import(sidecar=sidecar, include_optional=True)
    assert [li["description"] for li in out["line_items"]] == ["Optional"]
    assert out["source"] == "sidecar"
