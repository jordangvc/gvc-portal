"""
Estimate -> Invoice dollar import — pure helpers.
=========================================================================
Confirmed gap (see adapters.monday.client.build_invoice_prefill): the invoice
lookup prefills WHO/WHERE/Project # from the Projects board on purpose — the
office keys dollars from the actual billing materials, not the project
record. This module maps the dollars that were already quoted for the SAME
project so the invoice form can use them:

  * the rounded total Monday shows on the linked Bid Board item (coarse,
    always available when the project has a linked opportunity); and
  * when the as-sent estimate JSON sidecar (`{EST-...}.gvc-est.json`, written
    by subsystems.estimate.revision at finalize) is reachable on Drive, the
    FULL line items — the same data an estimate revision would prefill.

The invoice lookup MAY auto-apply these dollars when the form has no lines
yet (UI-side); the helpers here stay pure and never decide to apply. The
service layer (app.service.ui_invoice_lookup) does the Monday read + Drive
lookup and hands the raw values to build_estimate_import().
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Matches the first number in a string, tolerating "$", thousands separators
# (stripped before matching), and surrounding text ("Total: $12,345.00").
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_monday_total(value: Any) -> Optional[float]:
    """
    Safely parse a Monday numbers-column value into a float, or None when
    nothing usable is found. Handles the shapes Monday actually hands back:
    a bare number, a numeric string, or column `text` like "$12,345.00".
    Never raises — a malformed/missing total degrades to "not available"
    rather than a 500.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    try:
        return round(float(m.group(0)), 2)
    except ValueError:
        return None


def map_line_items(
    line_items: Optional[list[dict]], *, include_optional: bool = False
) -> list[dict]:
    """
    Map estimate JSON line_items -> invoice line dicts. `amount = unit_price *
    quantity`, matching subsystems.invoice.model's accepted line-item shape
    (description + amount) so a mapped row can be pushed straight into
    invoice.line_items. Skips `optional: true` items by default — those are
    quoted-but-not-included alternates, not billable work, unless the caller
    explicitly asks for them.
    """
    out: list[dict] = []
    for li in line_items or []:
        if not isinstance(li, dict):
            continue
        if li.get("optional") and not include_optional:
            continue
        try:
            unit_price = float(li.get("unit_price") or 0)
        except (TypeError, ValueError):
            unit_price = 0.0
        try:
            quantity = float(li.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1.0
        description = (li.get("description") or li.get("name") or "").strip()
        row = {
            "description": description,
            "amount": round(unit_price * quantity, 2),
            "quantity": quantity,
            "unit_price": unit_price,
            "optional": bool(li.get("optional")),
        }
        detail = li.get("detail")
        if detail is not None:
            detail_s = str(detail).strip()
            if detail_s:
                row["detail"] = detail_s
        out.append(row)
    return out


def build_estimate_import(
    *,
    estimate_number: Optional[str] = None,
    monday_total: Any = None,
    sidecar: Optional[dict] = None,
    include_optional: bool = False,
    notes: Optional[list[str]] = None,
) -> dict:
    """
    Assemble the `estimate_import` payload attached to the invoice lookup
    prefill (additive — a new key existing prefill clients simply ignore).

    `sidecar` is the loaded `<EST-...>.gvc-est.json` dict (same shape the
    estimate revision path loads — {"estimate": {"identifier", "line_items",
    ...}}) when Drive had one; `monday_total` is the raw Monday value
    (numeric or text) for the Bid Board's rounded total. Either, both, or
    neither may be available — this never raises.

    Returns:
      {available, estimate_number, monday_total, line_items,
       line_items_total, source, notes}
    """
    out_notes = list(notes or [])
    parsed_total = parse_monday_total(monday_total)

    line_items: list[dict] = []
    source = "none"
    if sidecar:
        raw_items = (sidecar.get("estimate") or {}).get("line_items") or []
        line_items = map_line_items(raw_items, include_optional=include_optional)
        if line_items:
            source = "sidecar"
        sidecar_number = ((sidecar.get("estimate") or {}).get("identifier") or "").strip()
        estimate_number = estimate_number or sidecar_number or None
    if source == "none" and parsed_total is not None:
        source = "monday_only"

    line_items_total = round(sum(li["amount"] for li in line_items), 2) if line_items else None
    available = bool(line_items) or parsed_total is not None

    if not available:
        out_notes.append(
            "No estimate dollars found for this project yet — the linked Bid "
            "Board total and the as-sent JSON sidecar were both unavailable."
        )

    return {
        "available": available,
        "estimate_number": estimate_number,
        "monday_total": parsed_total,
        "line_items": line_items,
        "line_items_total": line_items_total,
        "source": source,
        "notes": out_notes,
    }
