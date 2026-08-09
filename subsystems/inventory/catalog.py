"""Item catalog — PURE operations over the catalog doc.

Doc shape (versioned; see docs/inventory/DATA_MODEL.md):
{
  "schema": 1,
  "items": {item_id: {id, name, tracking ("quantity"|"asset"|"kit"),
            base_unit, conversions {unit: factor}, category, aliases [..],
            barcodes [..], scan_token, image_url, notes,
            min_qty {location_id: qty}, archived bool, merged_into id|None,
            kit_components [{item_id, qty}]  (tracking == "kit" templates),
            created_at, updated_at}},
  "units": {unit_id: {label, precision, fractional}},   # org additions
  "categories": [..],
  "next_seq": int,
}
"""
from __future__ import annotations

import copy

from subsystems.inventory.model import (
    InventoryError, TRACKING_MODES, new_token, now_iso, require, slugify,
)
from subsystems.inventory.units import DEFAULT_UNITS, units_registry


def ensure_shape(doc: dict) -> dict:
    doc.setdefault("schema", 1)
    doc.setdefault("items", {})
    doc.setdefault("units", {})
    doc.setdefault("categories", [])
    doc.setdefault("next_seq", 1)
    return doc


def get_item(doc: dict, item_id: str, *, allow_archived: bool = False) -> dict:
    item = (doc.get("items") or {}).get(item_id)
    require(item is not None, "UNKNOWN_ITEM",
            f"Item '{item_id}' does not exist.", field="item_id")
    if item.get("merged_into"):
        # Follow one merge hop so old labels/links keep working.
        target = (doc.get("items") or {}).get(item["merged_into"])
        if target is not None:
            item = target
    require(allow_archived or not item.get("archived"), "ITEM_ARCHIVED",
            f"'{item.get('name')}' is archived and can't be used in new "
            "transactions.", field="item_id",
            advice="A manager can restore it from Inventory admin.")
    return item


def upsert_item(doc: dict, payload: dict, *, actor: str,
                item_id: str = "") -> tuple[dict, dict]:
    """Create or edit an item. Returns (new_doc, item)."""
    new = copy.deepcopy(ensure_shape(doc))
    name = str(payload.get("name") or "").strip()
    require(bool(name), "INVALID_INPUT", "Item name is required.",
            field="name")
    tracking = str(payload.get("tracking") or "quantity")
    require(tracking in TRACKING_MODES, "INVALID_INPUT",
            f"tracking must be one of {TRACKING_MODES}.", field="tracking")
    units = units_registry(new)
    base_unit = str(payload.get("base_unit") or "each")
    require(base_unit in units, "UNKNOWN_UNIT",
            f"Base unit '{base_unit}' is not configured.", field="base_unit")

    conversions = {}
    for uid, factor in (payload.get("conversions") or {}).items():
        require(str(uid) in units, "UNKNOWN_UNIT",
                f"Conversion unit '{uid}' is not configured.",
                field="conversions")
        try:
            f = float(factor)
        except (TypeError, ValueError):
            raise InventoryError("INVALID_INPUT",
                                 f"Conversion factor for '{uid}' must be a "
                                 "number.", field="conversions")
        require(f > 0, "INVALID_INPUT",
                f"Conversion factor for '{uid}' must be positive.",
                field="conversions")
        conversions[str(uid)] = f

    if item_id:
        existing = new["items"].get(item_id)
        require(existing is not None, "UNKNOWN_ITEM",
                f"Item '{item_id}' does not exist.", field="item_id")
        # Invariant: tracking mode is frozen once the item exists — a mode
        # flip re-interprets history. (Spec: prohibited or explicit
        # migration; v1 prohibits.)
        require(existing["tracking"] == tracking, "TRACKING_LOCKED",
                "Tracking mode can't change after creation.",
                field="tracking",
                advice="Archive this item and create a new one in the "
                       "other mode; history stays with the original.")
        item = dict(existing)
    else:
        seq = int(new.get("next_seq") or 1)
        new["next_seq"] = seq + 1
        item_id = f"itm-{seq:04d}-{slugify(name)[:24]}"
        require(item_id not in new["items"], "DUPLICATE_ITEM",
                "Item id collision — retry.", field="item_id")
        item = {"id": item_id, "created_at": now_iso(),
                "scan_token": new_token("I"), "archived": False,
                "merged_into": None, "tracking": tracking}

    aliases = [str(a).strip() for a in (payload.get("aliases") or [])
               if str(a).strip()]
    barcodes = [str(b).strip() for b in (payload.get("barcodes") or [])
                if str(b).strip()]
    kit_components = []
    if tracking == "kit":
        for comp in (payload.get("kit_components") or []):
            cid = str(comp.get("item_id") or "")
            target = new["items"].get(cid)
            require(target is not None, "UNKNOWN_ITEM",
                    f"Kit component '{cid}' does not exist.",
                    field="kit_components")
            require(target["tracking"] == "quantity", "INVALID_INPUT",
                    "Kit templates hold quantity items only in v1.",
                    field="kit_components")
            try:
                q = float(comp.get("qty"))
            except (TypeError, ValueError):
                raise InventoryError("INVALID_INPUT",
                                     "Kit component qty must be a number.",
                                     field="kit_components")
            require(q > 0, "INVALID_INPUT",
                    "Kit component qty must be positive.",
                    field="kit_components")
            kit_components.append({"item_id": cid, "qty": q})
        require(bool(kit_components) or bool(item_id), "INVALID_INPUT",
                "A kit template needs at least one component.",
                field="kit_components")

    item.update({
        "name": name,
        "category": str(payload.get("category") or "").strip(),
        "base_unit": base_unit,
        "conversions": conversions,
        "aliases": aliases,
        "barcodes": barcodes,
        "image_url": str(payload.get("image_url") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
        "min_qty": {str(k): float(v) for k, v in
                    (payload.get("min_qty") or {}).items()},
        "kit_components": kit_components,
        "updated_at": now_iso(),
        "updated_by": actor,
    })
    new["items"][item_id] = item
    cat = item["category"]
    if cat and cat not in new["categories"]:
        new["categories"].append(cat)
    return new, item


def set_archived(doc: dict, item_id: str, archived: bool, *,
                 actor: str) -> tuple[dict, dict]:
    new = copy.deepcopy(ensure_shape(doc))
    item = new["items"].get(item_id)
    require(item is not None, "UNKNOWN_ITEM",
            f"Item '{item_id}' does not exist.", field="item_id")
    item["archived"] = bool(archived)
    item["updated_at"] = now_iso()
    item["updated_by"] = actor
    return new, item


def merge_items(doc: dict, source_id: str, target_id: str, *,
                actor: str) -> tuple[dict, dict]:
    """Merge duplicate SOURCE into TARGET: aliases/barcodes move, source is
    archived with a merged_into pointer so history keeps resolving. Balance
    transfer is the orchestrator's job (a MANUAL_ADJUSTMENT pair), never a
    silent edit here."""
    require(source_id != target_id, "INVALID_INPUT",
            "Can't merge an item into itself.", field="target_id")
    new = copy.deepcopy(ensure_shape(doc))
    src = new["items"].get(source_id)
    dst = new["items"].get(target_id)
    require(src is not None, "UNKNOWN_ITEM",
            f"Item '{source_id}' does not exist.", field="source_id")
    require(dst is not None, "UNKNOWN_ITEM",
            f"Item '{target_id}' does not exist.", field="target_id")
    require(src["tracking"] == dst["tracking"], "INVALID_INPUT",
            "Items with different tracking modes can't be merged.",
            field="target_id")
    require(not dst.get("merged_into"), "INVALID_INPUT",
            "Target item was itself merged away.", field="target_id")
    for alias in [src["name"], *src.get("aliases", [])]:
        if alias and alias not in dst.get("aliases", []) \
                and alias != dst["name"]:
            dst.setdefault("aliases", []).append(alias)
    for bc in src.get("barcodes", []):
        if bc not in dst.get("barcodes", []):
            dst.setdefault("barcodes", []).append(bc)
    src["archived"] = True
    src["merged_into"] = target_id
    for it in (src, dst):
        it["updated_at"] = now_iso()
        it["updated_by"] = actor
    return new, dst


def resolve_barcode(doc: dict, code: str) -> dict | None:
    """Find the item owning a manufacturer barcode or scan token."""
    code = (code or "").strip()
    for item in (doc.get("items") or {}).values():
        if item.get("merged_into"):
            continue
        if code == item.get("scan_token") or code in (item.get("barcodes")
                                                      or []):
            return item
    return None


def low_stock_hits(doc: dict, balances: dict) -> list[dict]:
    """[{item_id, name, location_id, min_qty, on_hand}] for every rule
    currently violated. `balances` = ledger doc's projection."""
    hits = []
    for item in (doc.get("items") or {}).values():
        if item.get("archived") or item.get("merged_into"):
            continue
        for loc_id, min_q in (item.get("min_qty") or {}).items():
            on_hand = float((balances.get(item["id"]) or {}).get(loc_id, 0))
            if on_hand < float(min_q):
                hits.append({"item_id": item["id"], "name": item["name"],
                             "location_id": loc_id, "min_qty": float(min_q),
                             "on_hand": on_hand})
    return hits
