"""
P5 Ready-to-Invoice worksheet staging (portal GCS, never Stripe/auto-send).
=========================================================================
Persists costing worksheets keyed by Operations item id so Billing Hub can
show proposed $ and Invoice can prefill editable lines for human review.

GCS object (state bucket): portal/invoice-ready-worksheets.json
  {
    "version": 1,
    "worksheets": {
      "<ops_item_id>": { ...worksheet..., "staged_at": "ISO…", "staged_by": "task" }
    }
  }

Soft-fail without portal store: in-process map (dev / cold-start). Real
multi-instance persistence needs the state bucket — same rule as grants /
lien alert markers / jobstart drafts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

DOC_VERSION = 1
DEFAULT_OBJECT = "portal/invoice-ready-worksheets.json"

# Process-local fallback when GCS isn't wired.
_MEMORY: dict[str, dict] = {}


def empty_doc() -> dict:
    return {"version": DOC_VERSION, "worksheets": {}}


def _object_name() -> str:
    return os.environ.get("GVC_INVOICE_READY_OBJECT") or DEFAULT_OBJECT


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def worksheet_key(ops_item_id: Any) -> str:
    return str(int(ops_item_id))


def worksheet_to_line_items(sheet: dict) -> list[dict]:
    """
    PURE. Costing worksheet → editable invoice line items (human reviews).

    by_sheet: one labor line + optional board material line.
    tm: one T&M line at the proposed total (min-invoice already applied).
    """
    priced = dict(sheet.get("pricing") or {})
    model = priced.get("model") or sheet.get("model") or "by_sheet"
    job = (sheet.get("job_name") or "Job").strip() or "Job"
    lines: list[dict] = []

    # Invoice UI rows use `amount` (not qty × unit_price).
    if model == "tm":
        total = float(sheet.get("proposed_invoice_total")
                      or priced.get("total") or 0)
        if total > 0:
            lines.append({
                "description": f"T&M — {job}",
                "detail": priced.get("price_label") or "",
                "amount": round(total, 2),
            })
        return lines

    labor = float(priced.get("labor") or 0)
    material = float(priced.get("material") or 0)
    sf = float(priced.get("ordered_board_sf") or 0)
    labor_rate = float(priced.get("labor_rate") or 0)
    material_rate = float(priced.get("material_rate") or 0)
    if labor > 0:
        detail = f"{sf:g} ordered board SF @ ${labor_rate:.2f}/SF"
        lines.append({
            "description": f"Hang + finish labor — {job}",
            "detail": detail,
            "amount": round(labor, 2),
        })
    if material > 0:
        detail = f"{sf:g} ordered board SF @ ${material_rate:.2f}/SF (GVC-supplied)"
        lines.append({
            "description": f"Board material — {job}",
            "detail": detail,
            "amount": round(material, 2),
        })
    if not lines:
        total = float(sheet.get("proposed_invoice_total") or priced.get("total") or 0)
        if total > 0:
            lines.append({
                "description": f"Drywall — {job}",
                "detail": priced.get("price_label") or "",
                "amount": round(total, 2),
            })
    return lines


def summary_from_sheet(sheet: dict) -> dict:
    """PURE. Compact fields for Billing Hub cards / API."""
    priced = dict(sheet.get("pricing") or {})
    return {
        "proposed_total": sheet.get("proposed_invoice_total"),
        "model": priced.get("model") or sheet.get("model"),
        "price_label": priced.get("price_label") or sheet.get("price_label"),
        "staged_at": sheet.get("staged_at"),
        "job_name": sheet.get("job_name"),
        "auto_send": False,
    }


def _read() -> tuple[dict, int]:
    from google.api_core.exceptions import NotFound

    blob = portal_store._blob(_object_name())
    try:
        blob.reload()
        raw = blob.download_as_text()
    except NotFound:
        return empty_doc(), 0
    try:
        doc = json.loads(raw) if raw.strip() else empty_doc()
    except json.JSONDecodeError:
        raise PortalStoreNotConfigured(f"{_object_name()} is not valid JSON.")
    doc.setdefault("worksheets", {})
    return doc, int(blob.generation or 0)


def _write(doc: dict, *, if_generation_match: int) -> int:
    blob = portal_store._blob(_object_name())
    blob.upload_from_string(
        json.dumps(doc, indent=2, sort_keys=True),
        content_type="application/json",
        if_generation_match=if_generation_match,
    )
    blob.reload()
    return int(blob.generation or 0)


def save_worksheet(ops_item_id: Any, sheet: dict, *,
                   actor: str = "task:check-ready-to-invoice") -> dict:
    """
    Idempotent upsert of one worksheet. Returns the stored record.
    Falls back to memory when the portal store isn't configured.
    """
    key = worksheet_key(ops_item_id)
    stamped = dict(sheet)
    stamped["ops_item_id"] = int(ops_item_id)
    stamped["staged_at"] = _now_iso()
    stamped["staged_by"] = actor
    stamped["auto_send"] = False
    stamped["status"] = "staged_worksheet"

    try:
        from google.api_core.exceptions import PreconditionFailed

        doc, gen = _read()
        worksheets = dict(doc.get("worksheets") or {})
        worksheets[key] = stamped
        new_doc = {"version": DOC_VERSION, "worksheets": worksheets}
        try:
            _write(new_doc, if_generation_match=gen)
        except PreconditionFailed:
            doc, gen = _read()
            worksheets = dict(doc.get("worksheets") or {})
            worksheets[key] = stamped
            _write({"version": DOC_VERSION, "worksheets": worksheets},
                   if_generation_match=gen)
        _MEMORY[key] = stamped
        return stamped
    except Exception as exc:  # noqa: BLE001 — soft-fail to memory
        print(f"[ready_stage] GCS save skipped ({type(exc).__name__}: {exc}); "
              f"using in-process store for ops {key}", flush=True)
        _MEMORY[key] = stamped
        return stamped


def get_worksheet(ops_item_id: Any) -> Optional[dict]:
    """Load one staged worksheet. Soft-fail → memory → None."""
    key = worksheet_key(ops_item_id)
    try:
        doc, _ = _read()
        hit = (doc.get("worksheets") or {}).get(key)
        if hit:
            _MEMORY[key] = hit
            return dict(hit)
    except Exception as exc:  # noqa: BLE001
        print(f"[ready_stage] GCS read skipped ({type(exc).__name__}: {exc})",
              flush=True)
    mem = _MEMORY.get(key)
    return dict(mem) if mem else None


def get_summaries(ops_item_ids: list[Any]) -> dict[str, dict]:
    """Batch summaries for Billing Hub enrichment. Missing keys omitted."""
    out: dict[str, dict] = {}
    if not ops_item_ids:
        return out
    keys = {worksheet_key(i) for i in ops_item_ids}
    doc_map: dict[str, dict] = {}
    try:
        doc, _ = _read()
        doc_map = dict(doc.get("worksheets") or {})
    except Exception:  # noqa: BLE001
        doc_map = {}
    for key in keys:
        sheet = doc_map.get(key) or _MEMORY.get(key)
        if sheet:
            out[key] = summary_from_sheet(sheet)
    return out


def clear_memory_for_tests() -> None:
    """Test helper — wipe the in-process fallback."""
    _MEMORY.clear()
