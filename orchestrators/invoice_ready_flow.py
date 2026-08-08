"""
Ready-to-Invoice consumer (P5) — Operations queue → costing worksheet → draft.

Company rates: labor $1.17/SF + $0.70/SF when GVC supplies board
(gvc-takeoff docs/PRICING-RULE-AUG2026.md). T&M: JDC $70/hr, $250 trip,
1.4 mat markup, $750 min. Never auto-sends.

POST /v1/tasks/check-ready-to-invoice (X-API-Key). dry_run defaults True.
"""
from __future__ import annotations

from typing import Any, Optional

from shared import pricing as gvc_pricing

SF_PER_BOARD_DEFAULT = 48.0


def _materials_by_default(builder: Optional[str]) -> str:
    name = (builder or "").lower()
    if any(b in name for b in ("wieland", "zicka", "berkey")):
        return "builder"
    return "gvc"


def _ordered_board_sf(board_count: Optional[float], payroll: dict) -> float:
    if board_count and board_count > 0:
        return float(board_count) * SF_PER_BOARD_DEFAULT
    total_sf = 0.0
    for row in payroll.get("rows") or []:
        raw = row.get("square_footage")
        if raw:
            try:
                total_sf += float(str(raw).replace(",", "").split()[0])
            except (TypeError, ValueError, IndexError):
                pass
    return total_sf if total_sf > 0 else 0.0


def _hours_from_payroll(payroll: dict) -> float:
    hours = 0.0
    for row in payroll.get("rows") or []:
        rate = float(row.get("rate") or 0)
        count = float(row.get("count") or 0)
        if 50 <= rate <= 120:
            hours += count
    return hours


def build_item_worksheet(row: dict, payroll: dict) -> dict:
    """Pure worksheet builder — unit-tested without Monday."""
    board_count = payroll.get("board_count_hint")
    materials_by = _materials_by_default(row.get("builder"))
    model = gvc_pricing.infer_billing_model(
        board_count=board_count,
        name=row.get("name") or "",
        payroll_total=float(payroll.get("labor_cost") or 0),
    )
    ordered_sf = _ordered_board_sf(board_count, payroll)
    hours = _hours_from_payroll(payroll)
    if model == "tm" and hours <= 0 and payroll.get("labor_cost"):
        hours = round(float(payroll["labor_cost"]) / gvc_pricing.TM_HOURLY, 2)

    return gvc_pricing.build_worksheet(
        job_name=row.get("name") or "",
        model=model,
        ordered_board_sf=ordered_sf,
        materials_by=materials_by,  # type: ignore[arg-type]
        hours=hours,
        material_cost=0.0,
        payroll_labor_cost=float(payroll.get("labor_cost") or 0),
        board_count=board_count,
        extras={
            "ops_item_id": row.get("item_id"),
            "project_item_id": row.get("project_item_id"),
            "project_number": row.get("project_number"),
            "builder": row.get("builder"),
            "ready_date": row.get("ready_date"),
            "payroll_rows": len(payroll.get("rows") or []),
            "ops_url": row.get("url"),
        },
    )


def check_ready_to_invoice(
    *,
    dry_run: bool = True,
    limit: int = 20,
    mc: Optional[Any] = None,
) -> dict:
    """Sweep Ready-to-Invoice group and build worksheets. Never auto-sends."""
    from adapters.monday.billing import fetch_ready_to_invoice
    from adapters.monday.client import MondayClient
    from adapters.monday import payroll as monday_payroll

    limit = max(1, min(int(limit or 20), 100))
    client = mc or MondayClient()

    try:
        rows = fetch_ready_to_invoice(client)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "dry_run": dry_run,
            "code": "MONDAY_FETCH_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "checked": 0,
            "worksheets": 0,
            "skipped": 0,
            "errors": [],
            "items": [],
        }

    checked = worksheets = skipped = 0
    errors: list[dict] = []
    items: list[dict] = []

    for row in (rows or [])[:limit]:
        checked += 1
        item_id = row.get("item_id")
        try:
            pid = row.get("project_item_id")
            if not pid:
                skipped += 1
                items.append({
                    "ops_item_id": item_id,
                    "name": row.get("name"),
                    "status": "skipped_no_project_link",
                })
                continue

            payroll = monday_payroll.fetch_payroll_for_project(client, int(pid))
            sheet = build_item_worksheet(row, payroll)
            worksheets += 1
            entry = {
                "ops_item_id": item_id,
                "project_item_id": pid,
                "name": row.get("name"),
                "status": "would_stage",
                "proposed_total": sheet["proposed_invoice_total"],
                "model": sheet["pricing"]["model"],
                "price_label": sheet["pricing"].get("price_label"),
                "payroll_labor_cost": sheet["payroll_labor_cost"],
                "worksheet": sheet if dry_run else {
                    "proposed_invoice_total": sheet["proposed_invoice_total"],
                    "model": sheet["pricing"]["model"],
                    "price_label": sheet["pricing"].get("price_label"),
                },
            }
            if not dry_run:
                # Portal GCS staging only — never Stripe, never auto-send.
                from subsystems.invoice import ready_stage
                stored = ready_stage.save_worksheet(item_id, sheet)
                entry["status"] = "staged"
                entry["staged"] = True
                entry["staged_at"] = stored.get("staged_at")
                entry["note"] = (
                    "Worksheet staged in the portal for Billing Hub / Invoice "
                    "prefill. Human reviews and sends — never auto-sent."
                )
            items.append(entry)
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "ops_item_id": item_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {
        "ok": len(errors) == 0,
        "dry_run": dry_run,
        "checked": checked,
        "worksheets": worksheets,
        "skipped": skipped,
        "errors": errors,
        "items": items,
        "pricing_rule": "PRICING-RULE-AUG2026",
        "labor_rate": gvc_pricing.LABOR_RATE_PER_SF,
        "material_rate": gvc_pricing.MATERIAL_RATE_PER_SF,
        "auto_send": False,
    }
