"""GVC company pricing — rates we will stand behind.

Source of truth for residential drywall sheet rates:
  gvc-takeoff docs/PRICING-RULE-AUG2026.md
  Validated Aug 2026 on Kavouras (+0.3%) and Delk (+0.25%).

T&M / small-project bands from the JDC pricing reference sheet
(GVC_JDC_Pricing_Reference_2026-07-17.pdf).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

# Aug 2026 two-part residential rate (ordered board SF).
LABOR_RATE_PER_SF = 1.17
MATERIAL_RATE_PER_SF = 0.70  # only when GVC supplies board

# JDC small-project / T&M bands
TM_HOURLY = 70.0
TM_TRIP = 250.0
TM_MAT_MARKUP = 1.4
TM_MIN_INVOICE = 750.0

BillingModel = Literal["by_sheet", "tm"]
MaterialsBy = Literal["gvc", "builder"]


def all_in_rate(materials_by: MaterialsBy = "gvc") -> float:
    """Labor always; material adder only when GVC supplies board."""
    if materials_by == "builder":
        return LABOR_RATE_PER_SF
    return LABOR_RATE_PER_SF + MATERIAL_RATE_PER_SF


def price_by_sheet(
    ordered_board_sf: float,
    *,
    materials_by: MaterialsBy = "gvc",
) -> dict[str, Any]:
    """Two-part sheet pricing on ordered board SF (production × 1.10)."""
    sf = max(0.0, float(ordered_board_sf or 0))
    labor = round(sf * LABOR_RATE_PER_SF, 2)
    material_rate = MATERIAL_RATE_PER_SF if materials_by == "gvc" else 0.0
    material = round(sf * material_rate, 2)
    total = round(labor + material, 2)
    label = (
        f"Labor ${labor:,.2f} @ {LABOR_RATE_PER_SF} · "
        f"Board ${material:,.2f} @ {material_rate} "
        f"({'GVC-supplied' if materials_by == 'gvc' else 'builder-supplied'})"
    )
    return {
        "model": "by_sheet",
        "ordered_board_sf": sf,
        "materials_by": materials_by,
        "labor_rate": LABOR_RATE_PER_SF,
        "material_rate": material_rate,
        "labor": labor,
        "material": material,
        "total": total,
        "price_label": label,
        "source": "PRICING-RULE-AUG2026",
    }


def price_tm(
    hours: float,
    *,
    material_cost: float = 0.0,
    include_trip: bool = True,
) -> dict[str, Any]:
    """T&M / touch-up pricing at JDC bands."""
    hrs = max(0.0, float(hours or 0))
    mat = max(0.0, float(material_cost or 0))
    labor = round(hrs * TM_HOURLY, 2)
    mat_billed = round(mat * TM_MAT_MARKUP, 2)
    trip = TM_TRIP if include_trip else 0.0
    subtotal = round(labor + mat_billed + trip, 2)
    total = max(subtotal, TM_MIN_INVOICE) if (hrs > 0 or mat > 0 or include_trip) else 0.0
    return {
        "model": "tm",
        "hours": hrs,
        "hourly": TM_HOURLY,
        "labor": labor,
        "material_cost": mat,
        "material_markup": TM_MAT_MARKUP,
        "material_billed": mat_billed,
        "trip": trip,
        "min_invoice": TM_MIN_INVOICE,
        "subtotal": subtotal,
        "total": round(total, 2),
        "price_label": (
            f"T&M ${hrs:g}h @ ${TM_HOURLY:.0f}/hr + trip ${trip:.0f} "
            f"+ mat×{TM_MAT_MARKUP} (min ${TM_MIN_INVOICE:.0f})"
        ),
        "source": "JDC reference sheet · Jul 17, 2026",
    }


def infer_billing_model(
    *,
    board_count: Optional[float] = None,
    name: str = "",
    payroll_total: float = 0.0,
) -> BillingModel:
    """Heuristic: tiny / patch / touch-up → T&M; else by-sheet."""
    n = (name or "").lower()
    if any(k in n for k in ("touch", "patch", "repair", "service", "punch")):
        return "tm"
    if board_count is not None and float(board_count) > 0 and float(board_count) <= 25:
        return "tm"
    if board_count is None and payroll_total and payroll_total < TM_MIN_INVOICE:
        return "tm"
    return "by_sheet"


def build_worksheet(
    *,
    job_name: str,
    model: BillingModel,
    ordered_board_sf: float = 0.0,
    materials_by: MaterialsBy = "gvc",
    hours: float = 0.0,
    material_cost: float = 0.0,
    payroll_labor_cost: float = 0.0,
    board_count: Optional[float] = None,
    extras: Optional[dict] = None,
) -> dict[str, Any]:
    """Internal costing worksheet for office review (never auto-sent)."""
    if model == "tm":
        priced = price_tm(hours, material_cost=material_cost)
    else:
        priced = price_by_sheet(ordered_board_sf, materials_by=materials_by)
    sheet: dict[str, Any] = {
        "job_name": job_name,
        "payroll_labor_cost": round(float(payroll_labor_cost or 0), 2),
        "board_count": board_count,
        "proposed_invoice_total": priced["total"],
        "pricing": priced,
        "status": "draft_worksheet",
        "auto_send": False,
        "notes": [
            "Staged for human review — never auto-sent.",
            "Company rates: labor $1.17/SF; +$0.70/SF only when GVC supplies board.",
        ],
    }
    if extras:
        sheet["extras"] = extras
    return sheet
