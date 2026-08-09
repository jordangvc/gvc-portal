"""Units, precision, and conversion snapshots.

Rules (docs/inventory/DATA_MODEL.md §Units):
- Every quantity item has ONE base unit; balances are stored in it.
- Optional per-item conversions translate an entered unit into the base
  unit (1 box = 5000 each). The entered qty/unit AND the normalized base
  qty are both stored on the transaction line, with the factor used —
  invariant 15: later config edits never change history.
- No silent rounding: a quantity with more precision than the unit allows
  is a validation error naming the field.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from subsystems.inventory.model import InventoryError, dec_str, require

# unit id -> {label, precision (max decimal places), fractional (allow <1 and
# non-integers at all)}. Organizations may add more via the catalog doc's
# `units` map; these ship as the defaults the spec lists.
DEFAULT_UNITS: dict[str, dict] = {
    "each":     {"label": "Each",     "precision": 0, "fractional": False},
    "box":      {"label": "Box",      "precision": 1, "fractional": True},
    "open_box": {"label": "Open box", "precision": 0, "fractional": False},
    "case":     {"label": "Case",     "precision": 1, "fractional": True},
    "bucket":   {"label": "Bucket",   "precision": 0, "fractional": False},
    "roll":     {"label": "Roll",     "precision": 0, "fractional": False},
    "bundle":   {"label": "Bundle",   "precision": 0, "fractional": False},
    "set":      {"label": "Set",      "precision": 0, "fractional": False},
    "pair":     {"label": "Pair",     "precision": 0, "fractional": False},
    "foot":     {"label": "Foot",     "precision": 2, "fractional": True},
    "sheet":    {"label": "Sheet",    "precision": 0, "fractional": False},
    "bag":      {"label": "Bag",      "precision": 0, "fractional": False},
    "tube":     {"label": "Tube",     "precision": 0, "fractional": False},
    "gallon":   {"label": "Gallon",   "precision": 2, "fractional": True},
}


def units_registry(catalog: dict) -> dict[str, dict]:
    """Defaults merged with org-defined units (org wins on collision)."""
    merged = {k: dict(v) for k, v in DEFAULT_UNITS.items()}
    for uid, u in (catalog.get("units") or {}).items():
        if isinstance(u, dict):
            merged[str(uid)] = {
                "label": str(u.get("label") or uid),
                "precision": int(u.get("precision") or 0),
                "fractional": bool(u.get("fractional")),
            }
    return merged


def parse_qty(raw, *, field: str = "qty") -> Decimal:
    try:
        q = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise InventoryError("INVALID_QTY", f"'{raw}' is not a number.",
                             field=field)
    require(q.is_finite(), "INVALID_QTY", "Quantity must be finite.",
            field=field)
    require(q > 0, "INVALID_QTY", "Quantity must be greater than zero.",
            field=field,
            advice="To remove stock, use a pick-up / adjustment, not a "
                   "negative quantity.")
    return q


def validate_precision(qty: Decimal, unit_id: str, units: dict[str, dict],
                       *, field: str = "qty") -> None:
    unit = units.get(unit_id)
    require(unit is not None, "UNKNOWN_UNIT",
            f"Unit '{unit_id}' is not configured.", field="unit")
    exp = -qty.as_tuple().exponent if qty.as_tuple().exponent < 0 else 0
    # Normalize e.g. 2.50 → 2.5 before judging precision.
    exp = -qty.normalize().as_tuple().exponent
    exp = max(0, exp)
    require(exp <= unit["precision"], "QTY_PRECISION",
            f"{qty} has more decimals than '{unit_id}' allows "
            f"(max {unit['precision']}).",
            field=field,
            advice="Enter a coarser amount or switch to a smaller unit.")
    if not unit["fractional"]:
        require(qty == qty.to_integral_value(), "QTY_PRECISION",
                f"'{unit_id}' does not allow fractional amounts.",
                field=field)


def conversion_factor(item: dict, unit_id: str) -> Decimal:
    """Factor from `unit_id` into the item's base unit. Base unit → 1."""
    if unit_id == item.get("base_unit"):
        return Decimal(1)
    conv = (item.get("conversions") or {}).get(unit_id)
    if conv is None:
        raise InventoryError(
            "NO_CONVERSION",
            f"'{item.get('name', item.get('id'))}' has no conversion from "
            f"'{unit_id}' to its base unit '{item.get('base_unit')}'.",
            field="unit",
            advice="Enter the amount in the base unit, or have a manager "
                   "add the conversion on the item.")
    return Decimal(str(conv))


def normalize_line_qty(item: dict, raw_qty, unit_id: str,
                       units: dict[str, dict]) -> dict:
    """Validate + snapshot: returns the pieces a transaction line stores."""
    qty = parse_qty(raw_qty)
    validate_precision(qty, unit_id, units)
    factor = conversion_factor(item, unit_id)
    base = (qty * factor).normalize()
    return {
        "entered_qty": dec_str(qty),
        "entered_unit": unit_id,
        "factor": dec_str(factor),
        "base_qty": dec_str(base),
    }
