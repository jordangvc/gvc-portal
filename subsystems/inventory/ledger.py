"""The inventory ledger — append-only events + balance projection + asset
custody + kit instances + idempotency, ALL in one doc.

Why one doc: a posting is a single store.mutate() compare-and-swap, so the
event, the balance change, the custody change, and the idempotency record
commit atomically (invariants 6/7/16/17). A concurrent post gets a GCS 412,
reloads, and re-runs THIS pure logic on fresh state — the stock check and
the duplicate-UUID check both re-run, so lost updates and double-posts are
structurally impossible.

Every function here is PURE (doc in → doc out). Money math is Decimal via
strings — floats never touch a balance.

Transaction states: the client cart/outbox is the "draft" state (localStorage,
per DECISIONS.md D6); the server ledger holds only POSTED and REVERSED.
Posted events are never edited — corrections are compensating REVERSAL
transactions that reference the original (invariants 1/2).
"""
from __future__ import annotations

import copy
from decimal import Decimal

from subsystems.inventory import catalog as cat
from subsystems.inventory import locations as locs
from subsystems.inventory import units as un
from subsystems.inventory.model import (
    CONDITIONS, INACTIVE_CONDITIONS, InventoryError, OUTBOUND_TYPES,
    TXN_TYPES, new_token, now_iso, require,
)

MAX_LINES = 200
# Kit moves ride the same custody rule as assets.
_ASSET_MOVE_TYPES = frozenset({"TRANSFER", "ISSUE", "RECEIVE",
                               "ASSET_ASSIGNMENT", "INITIAL_LOAD"})


def ensure_shape(doc: dict) -> dict:
    doc.setdefault("schema", 1)
    doc.setdefault("events", [])
    doc.setdefault("balances", {})       # item_id -> {loc_id: "Decimal"}
    doc.setdefault("assets", {})         # asset_id -> {...}
    doc.setdefault("kits", {})           # kit_id -> {...}
    doc.setdefault("idempotency", {})    # client_uuid -> txn_no
    doc.setdefault("next_txn_no", 1)
    return doc


# ---------------------------------------------------------------- balances

def on_hand(ledger: dict, item_id: str, loc_id: str) -> Decimal:
    raw = ((ledger.get("balances") or {}).get(item_id) or {}).get(loc_id)
    return Decimal(raw) if raw is not None else Decimal(0)


def _bump(balances: dict, item_id: str, loc_id: str, delta: Decimal) -> None:
    row = balances.setdefault(item_id, {})
    new = Decimal(row.get(loc_id, "0")) + delta
    if new == 0:
        row.pop(loc_id, None)
        if not row:
            balances.pop(item_id, None)
    else:
        row[loc_id] = str(new.normalize())


def location_holdings(ledger: dict, loc_id: str) -> dict:
    """Everything at a location: quantity balances, assets, kits."""
    items = {}
    for item_id, row in (ledger.get("balances") or {}).items():
        if loc_id in row:
            items[item_id] = row[loc_id]
    assets = [a for a in (ledger.get("assets") or {}).values()
              if a.get("location") == loc_id
              and a.get("condition") not in INACTIVE_CONDITIONS]
    kits = [k for k in (ledger.get("kits") or {}).values()
            if k.get("location") == loc_id and not k.get("dissolved")]
    return {"items": items, "assets": assets, "kits": kits}


def holdings_empty(ledger: dict, loc_id: str) -> bool:
    h = location_holdings(ledger, loc_id)
    return not h["items"] and not h["assets"] and not h["kits"]


# ------------------------------------------------------------------ assets

def get_asset(ledger: dict, asset_id: str) -> dict:
    asset = (ledger.get("assets") or {}).get(asset_id)
    require(asset is not None, "UNKNOWN_ASSET",
            f"Asset '{asset_id}' does not exist.", field="asset_id")
    return asset


def create_asset(ledger: dict, catalog_doc: dict, payload: dict, *,
                 actor: str) -> tuple[dict, dict]:
    """Register a serialized asset (custody starts at `location`)."""
    new = copy.deepcopy(ensure_shape(ledger))
    item = cat.get_item(catalog_doc, str(payload.get("item_id") or ""))
    require(item["tracking"] == "asset", "INVALID_INPUT",
            f"'{item['name']}' is not asset-tracked.", field="item_id")
    loc_id = str(payload.get("location") or "")
    require(bool(loc_id), "INVALID_INPUT",
            "New assets need a starting location.", field="location")
    seq = sum(1 for a in new["assets"].values()
              if a.get("item_id") == item["id"]) + 1
    asset_id = str(payload.get("asset_id") or "").strip() \
        or f"A-{item['id'].split('-')[1]}-{seq:03d}"
    require(asset_id not in new["assets"], "DUPLICATE_ASSET",
            f"Asset '{asset_id}' already exists.", field="asset_id")
    asset = {
        "id": asset_id, "item_id": item["id"],
        "serial": str(payload.get("serial") or "").strip(),
        "make": str(payload.get("make") or "").strip(),
        "model": str(payload.get("model") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
        "condition": "available",
        "location": loc_id,
        "scan_token": new_token("A"),
        "created_at": now_iso(), "created_by": actor,
    }
    new["assets"][asset_id] = asset
    return new, asset


# ------------------------------------------------------------------- kits

def get_kit(ledger: dict, kit_id: str) -> dict:
    kit = (ledger.get("kits") or {}).get(kit_id)
    require(kit is not None and not kit.get("dissolved"), "UNKNOWN_KIT",
            f"Kit '{kit_id}' does not exist.", field="kit_id")
    return kit


def kit_completeness(kit: dict, template_item: dict) -> list[dict]:
    """[{item_id, expected, present, short}] against the template."""
    have = {c["item_id"]: Decimal(str(c["qty"]))
            for c in kit.get("components", [])}
    out = []
    for comp in template_item.get("kit_components", []):
        expected = Decimal(str(comp["qty"])).normalize()
        present = have.get(comp["item_id"], Decimal(0)).normalize()
        short = max(Decimal(0), expected - present).normalize()
        out.append({"item_id": comp["item_id"], "expected": str(expected),
                    "present": str(present), "short": str(short)})
    return out


# ----------------------------------------------------------------- posting

def _validate_header(txn: dict) -> None:
    require(bool(str(txn.get("client_uuid") or "").strip()), "INVALID_INPUT",
            "client_uuid is required (offline retries depend on it).",
            field="client_uuid")
    require(txn.get("type") in TXN_TYPES, "INVALID_INPUT",
            f"type must be one of {TXN_TYPES}.", field="type")
    require(bool(str(txn.get("actor") or "").strip()), "INVALID_INPUT",
            "actor is required.", field="actor")
    lines = txn.get("lines") or []
    if txn.get("type") not in ("KIT_ASSEMBLY", "KIT_DISASSEMBLY"):
        require(bool(lines), "INVALID_INPUT",
                "A transaction needs at least one line.", field="lines")
    require(len(lines) <= MAX_LINES, "INVALID_INPUT",
            f"Too many lines (max {MAX_LINES}).", field="lines")


def _line_endpoints(txn_type: str, txn: dict, line: dict) -> tuple[str, str]:
    src = str(line.get("src") or txn.get("src") or "")
    dst = str(line.get("dst") or txn.get("dst") or "")
    if txn_type in ("RECEIVE", "INITIAL_LOAD"):
        require(bool(dst), "INVALID_INPUT", "Destination is required.",
                field="dst")
        src = ""
    elif txn_type == "ISSUE":
        require(bool(src), "INVALID_INPUT", "Source is required.",
                field="src")
        require(bool(dst), "INVALID_INPUT",
                "Pick-ups need a destination (you, a truck, a job).",
                field="dst")
    elif txn_type in ("TRANSFER", "ASSET_ASSIGNMENT"):
        require(bool(src) and bool(dst), "INVALID_INPUT",
                "Source and destination are both required.", field="src")
        require(src != dst, "INVALID_INPUT",
                "Source and destination are the same location.", field="dst")
    return src, dst


def post(ledger: dict, catalog_doc: dict, locations_doc: dict, txn: dict, *,
         can_override_negative: bool = False) -> tuple[dict, dict]:
    """Post one transaction atomically. Returns (new_ledger, result).

    Idempotency: a client_uuid the ledger has already posted returns the
    ORIGINAL result with already=True and the SAME doc object (identity ⇒
    store.mutate writes nothing) — invariants 3/16.
    """
    ensure_shape(ledger)
    _validate_header(txn)
    uuid = str(txn["client_uuid"]).strip()

    prior_no = ledger["idempotency"].get(uuid)
    if prior_no is not None:
        prior = next((e for e in ledger["events"]
                      if e["txn_no"] == prior_no), None)
        return ledger, {"ok": True, "already": True, "txn": prior}

    new = copy.deepcopy(ledger)
    txn_type = txn["type"]
    require(txn_type != "REVERSAL", "INVALID_INPUT",
            "Reversals are posted via reverse(), not directly.",
            field="type")

    units = un.units_registry(catalog_doc)
    posted_lines: list[dict] = []
    negative_override_used = False

    if txn_type == "KIT_ASSEMBLY":
        new, kit, posted_lines = _assemble(new, catalog_doc, locations_doc,
                                           txn, units)
    elif txn_type == "KIT_DISASSEMBLY":
        new, kit, posted_lines = _disassemble(new, catalog_doc,
                                              locations_doc, txn, units)
    else:
        for raw in txn.get("lines") or []:
            src, dst = _line_endpoints(txn_type, txn, raw)
            for lid in filter(None, (src, dst)):
                locs.get_location(locations_doc, lid)

            asset_id = str(raw.get("asset_id") or "")
            kit_id = str(raw.get("kit_id") or "")
            if asset_id:
                line = _post_asset_line(new, catalog_doc, txn_type, raw,
                                        asset_id, src, dst)
            elif kit_id:
                line = _post_kit_move_line(new, txn_type, kit_id, src, dst)
            else:
                line, neg = _post_qty_line(
                    new, catalog_doc, txn_type, txn, raw, src, dst, units,
                    can_override_negative=can_override_negative)
                negative_override_used = negative_override_used or neg
            posted_lines.append(line)

    txn_no = f"INV-{int(new['next_txn_no']):06d}"
    new["next_txn_no"] = int(new["next_txn_no"]) + 1
    event = {
        "txn_no": txn_no,
        "client_uuid": uuid,
        "type": txn_type,
        "status": "posted",
        "actor": str(txn["actor"]).strip().lower(),
        "on_behalf_of": str(txn.get("on_behalf_of") or "").strip().lower(),
        "ts": str(txn.get("ts") or now_iso()),
        "posted_at": now_iso(),
        "src": str(txn.get("src") or ""),
        "dst": str(txn.get("dst") or ""),
        "note": str(txn.get("note") or "").strip(),
        "reason": str(txn.get("reason") or "").strip(),
        "job_ref": str(txn.get("job_ref") or "").strip(),
        "device": str(txn.get("device") or "")[:120],
        "lines": posted_lines,
        "negative_override": negative_override_used,
        "reverses": "", "reversed_by": "",
    }
    new["events"].append(event)
    new["idempotency"][uuid] = txn_no
    return new, {"ok": True, "already": False, "txn": event}


def _post_qty_line(new: dict, catalog_doc: dict, txn_type: str, txn: dict,
                   raw: dict, src: str, dst: str, units: dict, *,
                   can_override_negative: bool) -> tuple[dict, bool]:
    item = cat.get_item(catalog_doc, str(raw.get("item_id") or ""))
    require(item["tracking"] == "quantity", "INVALID_INPUT",
            f"'{item['name']}' is {item['tracking']}-tracked — pick the "
            "specific unit instead of a quantity.", field="item_id")
    snap = un.normalize_line_qty(item, raw.get("qty"),
                                 str(raw.get("unit") or item["base_unit"]),
                                 units)
    base = Decimal(snap["base_qty"])
    sign = int(raw.get("sign") or 1)
    require(sign in (1, -1), "INVALID_INPUT", "sign must be 1 or -1.",
            field="sign")

    negative_used = False
    deltas: list[dict] = []  # exactly what this line does to balances —
    #                          rebuild_balances() replays these verbatim.

    if txn_type in ("COUNT_ADJUSTMENT", "MANUAL_ADJUSTMENT"):
        loc = dst or src
        require(bool(loc), "INVALID_INPUT",
                "Adjustments need a location.", field="dst")
        require(bool(str(txn.get("reason") or "").strip()), "INVALID_INPUT",
                "Adjustments require a reason.", field="reason")
        delta = base * sign
        after = on_hand(new, item["id"], loc) + delta
        if after < 0 and not (can_override_negative
                              and txn.get("allow_negative")):
            raise InventoryError(
                "INSUFFICIENT_STOCK",
                f"Adjustment would take '{item['name']}' to "
                f"{after.normalize()} at that location.",
                field="qty")
        negative_used = after < 0
        _bump(new["balances"], item["id"], loc, delta)
        deltas.append({"loc": loc, "delta": str(delta.normalize())})
        src, dst = "", loc
    else:
        require(sign == 1, "INVALID_INPUT",
                "Signed lines are only valid on adjustments.", field="sign")
        if src:  # ISSUE / TRANSFER pull from the source
            avail = on_hand(new, item["id"], src)
            if avail < base:
                if can_override_negative and txn.get("allow_negative"):
                    require(bool(str(txn.get("reason") or "").strip()),
                            "INVALID_INPUT",
                            "A negative-stock override requires a reason.",
                            field="reason")
                    negative_used = True
                else:
                    raise InventoryError(
                        "INSUFFICIENT_STOCK",
                        f"Only {avail.normalize()} {item['base_unit']} of "
                        f"'{item['name']}' at the source — asked for "
                        f"{base.normalize()}.",
                        field="qty",
                        advice="Lower the amount, run a count if the shelf "
                               "disagrees, or have a manager override with "
                               "a reason.")
            _bump(new["balances"], item["id"], src, -base)
            deltas.append({"loc": src, "delta": str((-base).normalize())})
        if dst:  # RECEIVE / INITIAL_LOAD / TRANSFER / ISSUE destination
            _bump(new["balances"], item["id"], dst, base)
            deltas.append({"loc": dst, "delta": str(base.normalize())})

    return ({"kind": "quantity", "item_id": item["id"],
             "item_name": item["name"], **snap, "sign": sign,
             "src": src, "dst": dst, "deltas": deltas,
             "note": str(raw.get("note") or "").strip()},
            negative_used)


def _post_asset_line(new: dict, catalog_doc: dict, txn_type: str, raw: dict,
                     asset_id: str, src: str, dst: str) -> dict:
    asset = get_asset(new, asset_id)
    item = cat.get_item(catalog_doc, asset["item_id"], allow_archived=True)
    if txn_type == "CONDITION_CHANGE":
        to = str(raw.get("condition_to") or "")
        require(to in CONDITIONS, "INVALID_INPUT",
                f"condition_to must be one of {CONDITIONS}.",
                field="condition_to")
        before = asset["condition"]
        asset["condition"] = to
        return {"kind": "asset", "asset_id": asset_id,
                "item_id": item["id"], "item_name": item["name"],
                "condition_from": before, "condition_to": to,
                "src": asset["location"], "dst": asset["location"],
                "note": str(raw.get("note") or "").strip(),
                "photo_url": str(raw.get("photo_url") or "").strip()}
    require(txn_type in _ASSET_MOVE_TYPES, "INVALID_INPUT",
            f"Assets can't appear on a {txn_type} line.", field="asset_id")
    require(asset["condition"] not in INACTIVE_CONDITIONS, "ASSET_INACTIVE",
            f"'{asset_id}' is {asset['condition']} and can't move.",
            field="asset_id",
            advice="A manager can restore its condition first.")
    if txn_type != "INITIAL_LOAD":
        # Invariants 4/5: the asset must actually be where the txn says.
        require(asset["location"] == src, "ASSET_NOT_AT_SOURCE",
                f"'{asset_id}' is not at that source (it's at "
                f"'{asset['location']}').",
                field="asset_id",
                advice="Scan it where it actually is, or run a count.")
    require(bool(dst), "INVALID_INPUT", "Asset moves need a destination.",
            field="dst")
    before = asset["location"]
    asset["location"] = dst
    return {"kind": "asset", "asset_id": asset_id, "item_id": item["id"],
            "item_name": item["name"], "src": before, "dst": dst,
            "note": str(raw.get("note") or "").strip()}


def _post_kit_move_line(new: dict, txn_type: str, kit_id: str, src: str,
                        dst: str) -> dict:
    kit = get_kit(new, kit_id)
    require(txn_type in _ASSET_MOVE_TYPES, "INVALID_INPUT",
            f"Kits can't appear on a {txn_type} line.", field="kit_id")
    if txn_type != "INITIAL_LOAD":
        require(kit["location"] == src, "ASSET_NOT_AT_SOURCE",
                f"Kit '{kit_id}' is not at that source (it's at "
                f"'{kit['location']}').", field="kit_id")
    require(bool(dst), "INVALID_INPUT", "Kit moves need a destination.",
            field="dst")
    before = kit["location"]
    kit["location"] = dst
    return {"kind": "kit", "kit_id": kit_id,
            "item_id": kit.get("template_item_id"),
            "item_name": kit.get("name"), "src": before, "dst": dst}


def _assemble(new: dict, catalog_doc: dict, locations_doc: dict, txn: dict,
              units: dict) -> tuple[dict, dict, list[dict]]:
    """Loose components at `dst` become a kit instance at `dst`. The
    component quantities LEAVE the loose balances — invariant 14: nothing
    is counted both inside a kit and loose."""
    spec = txn.get("kit") or {}
    template = cat.get_item(catalog_doc, str(spec.get("template_item_id")
                                             or ""))
    require(template["tracking"] == "kit", "INVALID_INPUT",
            f"'{template['name']}' is not a kit template.",
            field="template_item_id")
    loc_id = str(spec.get("location") or txn.get("dst") or "")
    locs.get_location(locations_doc, loc_id)
    kit_id = str(spec.get("kit_id") or "").strip() \
        or f"K-{sum(1 for _ in new['kits']) + 1:03d}"
    require(kit_id not in new["kits"], "DUPLICATE_KIT",
            f"Kit '{kit_id}' already exists.", field="kit_id")

    lines, components = [], []
    for comp in template.get("kit_components", []):
        item = cat.get_item(catalog_doc, comp["item_id"])
        need = Decimal(str(comp["qty"]))
        avail = on_hand(new, item["id"], loc_id)
        require(avail >= need, "INSUFFICIENT_STOCK",
                f"Assembling needs {need} {item['base_unit']} of "
                f"'{item['name']}' at the location — only "
                f"{avail.normalize()} there.",
                field="kit",
                advice="Transfer the missing components there first.")
        _bump(new["balances"], item["id"], loc_id, -need)
        components.append({"item_id": item["id"], "qty": float(need)})
        lines.append({"kind": "quantity", "item_id": item["id"],
                      "item_name": item["name"],
                      "entered_qty": str(need), "entered_unit":
                      item["base_unit"], "factor": "1",
                      "base_qty": str(need), "sign": -1,
                      "src": loc_id, "dst": f"kit:{kit_id}",
                      "deltas": [{"loc": loc_id,
                                  "delta": str((-need).normalize())}]})
    kit = {"id": kit_id, "template_item_id": template["id"],
           "name": str(spec.get("name") or template["name"]),
           "location": loc_id, "components": components,
           "condition": "available", "scan_token": new_token("K"),
           "dissolved": False, "created_at": now_iso(),
           "created_by": str(txn.get("actor") or "")}
    new["kits"][kit_id] = kit
    return new, kit, lines


def _disassemble(new: dict, catalog_doc: dict, locations_doc: dict,
                 txn: dict, units: dict) -> tuple[dict, dict, list[dict]]:
    """All or part of a kit's components return to loose stock at the
    kit's current location. Removing everything dissolves the instance."""
    spec = txn.get("kit") or {}
    kit = get_kit(new, str(spec.get("kit_id") or ""))
    loc_id = kit["location"]
    subset = spec.get("components")  # None = everything
    remaining, lines = [], []
    take = {str(c.get("item_id")): Decimal(str(c.get("qty")))
            for c in (subset or [])}
    for comp in kit.get("components", []):
        have = Decimal(str(comp["qty"]))
        want = take.get(comp["item_id"], have if subset is None
                        else Decimal(0))
        want = min(want, have)
        if want > 0:
            item = cat.get_item(catalog_doc, comp["item_id"],
                                allow_archived=True)
            _bump(new["balances"], item["id"], loc_id, want)
            lines.append({"kind": "quantity", "item_id": item["id"],
                          "item_name": item["name"],
                          "entered_qty": str(want),
                          "entered_unit": item["base_unit"], "factor": "1",
                          "base_qty": str(want), "sign": 1,
                          "src": f"kit:{kit['id']}", "dst": loc_id,
                          "deltas": [{"loc": loc_id,
                                      "delta": str(want.normalize())}]})
        if have - want > 0:
            remaining.append({"item_id": comp["item_id"],
                              "qty": float(have - want)})
    require(bool(lines), "INVALID_INPUT",
            "Nothing selected to remove from the kit.", field="components")
    kit["components"] = remaining
    if not remaining:
        kit["dissolved"] = True
    return new, kit, lines


# ---------------------------------------------------------------- reversal

_REVERSIBLE = frozenset({"RECEIVE", "ISSUE", "TRANSFER", "INITIAL_LOAD",
                         "MANUAL_ADJUSTMENT", "COUNT_ADJUSTMENT",
                         "ASSET_ASSIGNMENT", "CONDITION_CHANGE"})


def reverse(ledger: dict, catalog_doc: dict, locations_doc: dict,
            txn_no: str, *, actor: str, reason: str,
            client_uuid: str) -> tuple[dict, dict]:
    """Equal-and-opposite compensating transaction, linked both ways."""
    ensure_shape(ledger)
    require(bool(str(reason or "").strip()), "INVALID_INPUT",
            "A reversal requires a reason.", field="reason")
    prior_no = ledger["idempotency"].get(str(client_uuid).strip())
    if prior_no is not None:
        prior = next((e for e in ledger["events"]
                      if e["txn_no"] == prior_no), None)
        return ledger, {"ok": True, "already": True, "txn": prior}

    original = next((e for e in ledger["events"]
                     if e["txn_no"] == txn_no), None)
    require(original is not None, "UNKNOWN_TXN",
            f"Transaction '{txn_no}' does not exist.", field="txn_no")
    require(not original.get("reversed_by"), "ALREADY_REVERSED",
            f"'{txn_no}' was already reversed by "
            f"{original.get('reversed_by')}.", field="txn_no")
    require(original["type"] in _REVERSIBLE, "NOT_REVERSIBLE",
            f"{original['type']} transactions aren't reversed — post the "
            "opposite operation instead.",
            field="txn_no",
            advice="Kit assembly ↔ disassembly are each other's undo.")

    new = copy.deepcopy(ledger)
    rev_lines = []
    for line in original["lines"]:
        if line["kind"] == "quantity":
            # Undo exactly what the original applied — its recorded deltas,
            # negated. No type-specific reasoning to drift out of sync.
            rev_deltas = []
            for d in line.get("deltas", []):
                delta = -Decimal(d["delta"])
                _bump(new["balances"], line["item_id"], d["loc"], delta)
                rev_deltas.append({"loc": d["loc"],
                                   "delta": str(delta.normalize())})
            rev_lines.append({**line, "src": line.get("dst", ""),
                              "dst": line.get("src", ""),
                              "deltas": rev_deltas})
        elif line["kind"] == "asset":
            asset = new["assets"].get(line["asset_id"])
            require(asset is not None, "UNKNOWN_ASSET",
                    f"Asset '{line['asset_id']}' vanished.", field="txn_no")
            if "condition_to" in line:
                asset["condition"] = line["condition_from"]
                rev_lines.append({**line,
                                  "condition_from": line["condition_to"],
                                  "condition_to": line["condition_from"]})
            else:
                # The asset must still be where the original put it,
                # or the reversal would teleport it (invariant 5).
                require(asset["location"] == line["dst"],
                        "ASSET_NOT_AT_SOURCE",
                        f"'{asset['id']}' has moved since {txn_no}; "
                        "reverse the later move first.", field="txn_no")
                asset["location"] = line["src"]
                rev_lines.append({**line, "src": line["dst"],
                                  "dst": line["src"]})
        else:  # kit move
            kit = new["kits"].get(line["kit_id"])
            require(kit is not None, "UNKNOWN_KIT",
                    f"Kit '{line['kit_id']}' vanished.", field="txn_no")
            require(kit["location"] == line["dst"], "ASSET_NOT_AT_SOURCE",
                    f"Kit '{kit['id']}' has moved since {txn_no}; reverse "
                    "the later move first.", field="txn_no")
            kit["location"] = line["src"]
            rev_lines.append({**line, "src": line["dst"],
                              "dst": line["src"]})

    txn_no_new = f"INV-{int(new['next_txn_no']):06d}"
    new["next_txn_no"] = int(new["next_txn_no"]) + 1
    event = {
        "txn_no": txn_no_new, "client_uuid": str(client_uuid).strip(),
        "type": "REVERSAL", "status": "posted",
        "actor": str(actor).strip().lower(), "on_behalf_of": "",
        "ts": now_iso(), "posted_at": now_iso(),
        "src": original.get("dst", ""), "dst": original.get("src", ""),
        "note": "", "reason": str(reason).strip(), "job_ref": "",
        "device": "", "lines": rev_lines, "negative_override": False,
        "reverses": txn_no, "reversed_by": "",
    }
    new["events"].append(event)
    for e in new["events"]:
        if e["txn_no"] == txn_no:
            e["reversed_by"] = txn_no_new
            e["status"] = "reversed"
    new["idempotency"][str(client_uuid).strip()] = txn_no_new
    return new, {"ok": True, "already": False, "txn": event}


# ------------------------------------------------------------- consistency

def rebuild_balances(ledger: dict) -> dict:
    """Recompute the projection purely from events, by replaying each
    line's recorded `deltas` (the exact balance changes it applied when
    posted). The reconciliation tool (OPERATIONS.md) and the invariant-6
    test both diff this against the stored projection — any divergence
    means the projection was corrupted outside post()/reverse()."""
    balances: dict = {}
    for e in ledger.get("events", []):
        for line in e.get("lines", []):
            if line.get("kind") != "quantity":
                continue
            for d in line.get("deltas", []):
                _bump(balances, line["item_id"], d["loc"],
                      Decimal(d["delta"]))
    return balances
