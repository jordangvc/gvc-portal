"""Inventory ledger invariants (docs/inventory/DATA_MODEL.md).

Every numbered invariant the design claims is asserted here against the
PURE ledger — no GCS, no network. Runs under pytest OR directly:
``python tests/test_inventory_ledger.py``.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from subsystems.inventory import catalog as cat  # noqa: E402
from subsystems.inventory import ledger as led  # noqa: E402
from subsystems.inventory import locations as locs  # noqa: E402
from subsystems.inventory.model import InventoryError  # noqa: E402


# ------------------------------------------------------------------ fixtures

def _world():
    """catalog + locations + empty ledger with the seed shapes."""
    c: dict = {}
    c, screws = cat.upsert_item(c, {
        "name": "Drywall screws", "base_unit": "box",
        "conversions": {"each": 0.0002, "case": 12},  # 1 case = 12 box
        "aliases": ["screws"], "category": "Materials"}, actor="t")
    c, bead = cat.upsert_item(c, {
        "name": "Corner bead", "base_unit": "each"}, actor="t")
    c, ladder = cat.upsert_item(c, {
        "name": "8-foot ladder", "tracking": "asset",
        "base_unit": "each"}, actor="t")
    c, brace = cat.upsert_item(c, {
        "name": "Scaffold brace", "base_unit": "each"}, actor="t")
    c, board = cat.upsert_item(c, {
        "name": "Walk board", "base_unit": "each"}, actor="t")
    c, kit_tpl = cat.upsert_item(c, {
        "name": "Standard scaffold set", "tracking": "kit",
        "base_unit": "set",
        "kit_components": [{"item_id": brace["id"], "qty": 4},
                           {"item_id": board["id"], "qty": 2}]}, actor="t")

    l: dict = {}
    l, main = locs.upsert_location(l, {"name": "Main Storage",
                                       "kind": "storage"}, actor="t")
    l, truck = locs.upsert_location(l, {"name": "Truck 12",
                                        "kind": "truck"}, actor="t")
    l, job = locs.upsert_location(l, {"name": "Sample Job Site",
                                      "kind": "job_site"}, actor="t")
    l, me = locs.ensure_employee_location(l, "mark@gvc.com", "Mark W")

    led_doc = led.ensure_shape({})
    ids = {"screws": screws["id"], "bead": bead["id"],
           "ladder_item": ladder["id"], "brace": brace["id"],
           "board": board["id"], "kit_tpl": kit_tpl["id"],
           "main": main["id"], "truck": truck["id"], "job": job["id"],
           "me": me["id"]}
    return c, l, led_doc, ids


def _seed_stock(c, l, doc, ids, uuid="seed-1"):
    doc, res = led.post(doc, c, l, {
        "client_uuid": uuid, "type": "INITIAL_LOAD", "actor": "admin@gvc",
        "dst": ids["main"],
        "lines": [
            {"item_id": ids["screws"], "qty": "10", "unit": "box"},
            {"item_id": ids["bead"], "qty": "200", "unit": "each"},
            {"item_id": ids["brace"], "qty": "8", "unit": "each"},
            {"item_id": ids["board"], "qty": "4", "unit": "each"},
        ]})
    assert res["ok"] and not res["already"]
    return doc


# --------------------------------------------------------------- invariants

def test_post_receive_issue_transfer_and_projection():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    assert led.on_hand(doc, ids["screws"], ids["main"]) == Decimal(10)

    doc, res = led.post(doc, c, l, {
        "client_uuid": "t-1", "type": "TRANSFER", "actor": "mark@gvc",
        "src": ids["main"], "dst": ids["truck"],
        "lines": [{"item_id": ids["screws"], "qty": "3", "unit": "box"}]})
    assert led.on_hand(doc, ids["screws"], ids["main"]) == Decimal(7)
    assert led.on_hand(doc, ids["screws"], ids["truck"]) == Decimal(3)

    doc, res = led.post(doc, c, l, {
        "client_uuid": "t-2", "type": "ISSUE", "actor": "mark@gvc",
        "src": ids["truck"], "dst": ids["job"],
        "lines": [{"item_id": ids["screws"], "qty": "1", "unit": "box"}]})
    assert led.on_hand(doc, ids["screws"], ids["truck"]) == Decimal(2)
    assert led.on_hand(doc, ids["screws"], ids["job"]) == Decimal(1)

    # Invariant 6: projection equals ledger replay.
    assert led.rebuild_balances(doc) == doc["balances"]
    # Invariant 18: UTC timestamps.
    assert doc["events"][-1]["posted_at"].endswith("+00:00")


def test_unit_conversion_snapshot_survives_config_change():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    doc, _ = led.post(doc, c, l, {
        "client_uuid": "case-1", "type": "RECEIVE", "actor": "a@gvc",
        "dst": ids["main"],
        "lines": [{"item_id": ids["screws"], "qty": "2", "unit": "case"}]})
    # 2 cases = 24 box, on top of 10.
    assert led.on_hand(doc, ids["screws"], ids["main"]) == Decimal(34)
    line = doc["events"][-1]["lines"][0]
    assert line["factor"] == "12" and line["base_qty"] == "24"
    assert line["entered_qty"] == "2" and line["entered_unit"] == "case"

    # Invariant 15: change the conversion AFTER posting — history frozen.
    c2 = deepcopy(c)
    item = next(i for i in c2["items"].values()
                if i["id"] == ids["screws"])
    item["conversions"]["case"] = 99
    doc2, res = led.reverse(doc, c2, l, doc["events"][-1]["txn_no"],
                            actor="mgr@gvc", reason="mis-scan",
                            client_uuid="rev-case-1")
    # Reversal used the RECORDED deltas (24), not the new factor (99×2).
    assert led.on_hand(doc2, ids["screws"], ids["main"]) == Decimal(10)


def test_idempotent_duplicate_returns_original_and_writes_nothing():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    txn = {"client_uuid": "dup-1", "type": "TRANSFER", "actor": "m@gvc",
           "src": ids["main"], "dst": ids["truck"],
           "lines": [{"item_id": ids["bead"], "qty": "50",
                      "unit": "each"}]}
    doc, first = led.post(doc, c, l, txn)
    doc2, second = led.post(doc, c, l, deepcopy(txn))
    # Invariants 3/16: same result, no double movement, identity no-op.
    assert second["already"] is True
    assert second["txn"]["txn_no"] == first["txn"]["txn_no"]
    assert doc2 is doc
    assert led.on_hand(doc, ids["bead"], ids["truck"]) == Decimal(50)


def test_concurrent_last_unit_cannot_be_taken_twice():
    """Invariant 17 — simulate the CAS: two writers build on the same
    snapshot; the loser re-applies on the winner's doc (what store.mutate
    does after a 412) and must hit INSUFFICIENT_STOCK."""
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    take_all = lambda uid: {  # noqa: E731
        "client_uuid": uid, "type": "ISSUE", "actor": "x@gvc",
        "src": ids["main"], "dst": ids["job"],
        "lines": [{"item_id": ids["board"], "qty": "4", "unit": "each"}]}
    snapshot = deepcopy(doc)
    winner_doc, _ = led.post(snapshot, c, l, take_all("w-1"))
    # Loser retries on the winner's committed state:
    with pytest.raises(InventoryError) as e:
        led.post(winner_doc, c, l, take_all("w-2"))
    assert e.value.code == "INSUFFICIENT_STOCK"


def test_negative_stock_rejected_and_override_gated():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    over = {"client_uuid": "neg-1", "type": "ISSUE", "actor": "m@gvc",
            "src": ids["main"], "dst": ids["job"],
            "lines": [{"item_id": ids["bead"], "qty": "500",
                       "unit": "each"}]}
    with pytest.raises(InventoryError) as e:  # invariant 8
        led.post(doc, c, l, deepcopy(over))
    assert e.value.code == "INSUFFICIENT_STOCK"

    # allow_negative without manager rights still refuses.
    over["allow_negative"] = True
    with pytest.raises(InventoryError):
        led.post(doc, c, l, deepcopy(over), can_override_negative=False)

    # Manager override without a reason refuses; with reason posts and
    # flags the event (invariant 9 — orchestrator raises attention on it).
    with pytest.raises(InventoryError):
        led.post(doc, c, l, deepcopy(over), can_override_negative=True)
    over["reason"] = "final bundle was mislabeled, shipping anyway"
    doc, res = led.post(doc, c, l, over, can_override_negative=True)
    assert res["txn"]["negative_override"] is True
    assert led.on_hand(doc, ids["bead"], ids["main"]) == Decimal(-300)
    assert led.rebuild_balances(doc) == doc["balances"]


def test_posted_events_immutable_and_reversal_links_both_ways():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    balances_pre_transfer = deepcopy(doc["balances"])
    doc, res = led.post(doc, c, l, {
        "client_uuid": "r-1", "type": "TRANSFER", "actor": "m@gvc",
        "src": ids["main"], "dst": ids["truck"],
        "lines": [{"item_id": ids["bead"], "qty": "40", "unit": "each"}]})
    orig_no = res["txn"]["txn_no"]
    before_lines = deepcopy(res["txn"]["lines"])
    assert doc["balances"] != balances_pre_transfer

    with pytest.raises(InventoryError):  # reason mandatory
        led.reverse(doc, c, l, orig_no, actor="mgr@gvc", reason="  ",
                    client_uuid="rv-0")
    doc, rev = led.reverse(doc, c, l, orig_no, actor="mgr@gvc",
                           reason="wrong truck", client_uuid="rv-1")
    # Invariant 2: equal and opposite — balances restored EXACTLY.
    assert doc["balances"] == balances_pre_transfer
    assert led.on_hand(doc, ids["bead"], ids["main"]) == Decimal(200)
    assert led.on_hand(doc, ids["bead"], ids["truck"]) == Decimal(0)
    original = next(e for e in doc["events"] if e["txn_no"] == orig_no)
    # Invariant 1: original lines untouched; only linkage fields changed.
    assert original["lines"] == before_lines
    assert original["reversed_by"] == rev["txn"]["txn_no"]
    assert original["status"] == "reversed"
    assert rev["txn"]["reverses"] == orig_no

    # Double reversal blocked; duplicate reversal uuid is idempotent.
    with pytest.raises(InventoryError) as e:
        led.reverse(doc, c, l, orig_no, actor="mgr@gvc", reason="again",
                    client_uuid="rv-2")
    assert e.value.code == "ALREADY_REVERSED"
    doc2, again = led.reverse(doc, c, l, orig_no, actor="mgr@gvc",
                              reason="retry", client_uuid="rv-1")
    assert again["already"] is True and doc2 is doc
    assert led.rebuild_balances(doc) == doc["balances"]


def test_asset_single_location_and_custody_rules():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    doc, ladder = led.create_asset(doc, c, {
        "item_id": ids["ladder_item"], "location": ids["main"],
        "serial": "WERNER-123"}, actor="a@gvc")

    # Invariant 5: can't move it from somewhere it isn't.
    with pytest.raises(InventoryError) as e:
        led.post(doc, c, l, {
            "client_uuid": "a-1", "type": "TRANSFER", "actor": "m@gvc",
            "src": ids["truck"], "dst": ids["job"],
            "lines": [{"asset_id": ladder["id"]}]})
    assert e.value.code == "ASSET_NOT_AT_SOURCE"

    doc, _ = led.post(doc, c, l, {
        "client_uuid": "a-2", "type": "ASSET_ASSIGNMENT", "actor": "m@gvc",
        "src": ids["main"], "dst": ids["me"],
        "lines": [{"asset_id": ladder["id"]}]})
    # Invariant 4: exactly one current location.
    assert doc["assets"][ladder["id"]]["location"] == ids["me"]
    holdings = led.location_holdings(doc, ids["me"])
    assert [a["id"] for a in holdings["assets"]] == [ladder["id"]]
    assert not any(a["id"] == ladder["id"] for a in
                   led.location_holdings(doc, ids["main"])["assets"])

    # Condition change + reversal restores the prior condition.
    doc, res = led.post(doc, c, l, {
        "client_uuid": "a-3", "type": "CONDITION_CHANGE", "actor": "m@gvc",
        "lines": [{"asset_id": ladder["id"], "condition_to": "damaged",
                   "note": "bent rail"}]})
    assert doc["assets"][ladder["id"]]["condition"] == "damaged"
    doc, _ = led.reverse(doc, c, l, res["txn"]["txn_no"], actor="mgr@gvc",
                         reason="repaired on the spot", client_uuid="a-4")
    assert doc["assets"][ladder["id"]]["condition"] == "available"

    # A retired asset can't move.
    doc, _ = led.post(doc, c, l, {
        "client_uuid": "a-5", "type": "CONDITION_CHANGE", "actor": "m@gvc",
        "lines": [{"asset_id": ladder["id"], "condition_to": "retired"}]})
    with pytest.raises(InventoryError) as e:
        led.post(doc, c, l, {
            "client_uuid": "a-6", "type": "TRANSFER", "actor": "m@gvc",
            "src": ids["me"], "dst": ids["main"],
            "lines": [{"asset_id": ladder["id"]}]})
    assert e.value.code == "ASSET_INACTIVE"


def test_kit_assembly_no_double_count_and_disassembly():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)   # 8 braces + 4 boards at main

    def loose_plus_kits(item_id):
        loose = led.on_hand(doc, item_id, ids["main"]) \
            + led.on_hand(doc, item_id, ids["job"])
        in_kits = sum(Decimal(str(comp["qty"]))
                      for k in doc["kits"].values() if not k["dissolved"]
                      for comp in k["components"]
                      if comp["item_id"] == item_id)
        return loose + in_kits

    total_braces_before = loose_plus_kits(ids["brace"])

    doc, res = led.post(doc, c, l, {
        "client_uuid": "k-1", "type": "KIT_ASSEMBLY", "actor": "m@gvc",
        "kit": {"template_item_id": ids["kit_tpl"],
                "location": ids["main"], "name": "Scaffold set 1"}})
    kit_id = next(iter(doc["kits"]))
    # Invariant 14: components left loose stock…
    assert led.on_hand(doc, ids["brace"], ids["main"]) == Decimal(4)
    assert led.on_hand(doc, ids["board"], ids["main"]) == Decimal(2)
    # …and the loose+kit total is conserved (no double count).
    assert loose_plus_kits(ids["brace"]) == total_braces_before

    # Kit moves as one unit with custody rules.
    doc, _ = led.post(doc, c, l, {
        "client_uuid": "k-2", "type": "TRANSFER", "actor": "m@gvc",
        "src": ids["main"], "dst": ids["job"],
        "lines": [{"kit_id": kit_id}]})
    assert doc["kits"][kit_id]["location"] == ids["job"]

    # Completeness math.
    tpl = cat.get_item(c, ids["kit_tpl"])
    comp = led.kit_completeness(doc["kits"][kit_id], tpl)
    assert all(row["short"] == "0" for row in comp)

    # Partial disassembly at the job returns components to loose stock.
    doc, res = led.post(doc, c, l, {
        "client_uuid": "k-3", "type": "KIT_DISASSEMBLY", "actor": "m@gvc",
        "kit": {"kit_id": kit_id,
                "components": [{"item_id": ids["brace"], "qty": 2}]}})
    assert led.on_hand(doc, ids["brace"], ids["job"]) == Decimal(2)
    comp = led.kit_completeness(doc["kits"][kit_id], tpl)
    brace_row = next(r for r in comp if r["item_id"] == ids["brace"])
    assert brace_row["short"] == "2"   # incomplete kit is now visible

    # Full disassembly dissolves the instance.
    doc, _ = led.post(doc, c, l, {
        "client_uuid": "k-4", "type": "KIT_DISASSEMBLY", "actor": "m@gvc",
        "kit": {"kit_id": kit_id}})
    assert doc["kits"][kit_id]["dissolved"] is True
    assert loose_plus_kits(ids["brace"]) == total_braces_before
    assert led.rebuild_balances(doc) == doc["balances"]

    # Assembling without enough components refuses.
    with pytest.raises(InventoryError) as e:
        led.post(doc, c, l, {
            "client_uuid": "k-5", "type": "KIT_ASSEMBLY", "actor": "m@gvc",
            "kit": {"template_item_id": ids["kit_tpl"],
                    "location": ids["truck"]}})
    assert e.value.code == "INSUFFICIENT_STOCK"


def test_archived_item_blocked_but_history_remains():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    c, _ = cat.set_archived(c, ids["bead"], True, actor="mgr@gvc")
    with pytest.raises(InventoryError) as e:  # invariant 10
        led.post(doc, c, l, {
            "client_uuid": "arch-1", "type": "ISSUE", "actor": "m@gvc",
            "src": ids["main"], "dst": ids["job"],
            "lines": [{"item_id": ids["bead"], "qty": "1",
                       "unit": "each"}]})
    assert e.value.code == "ITEM_ARCHIVED"
    # History still shows the seeded event lines for the archived item.
    assert any(ln["item_id"] == ids["bead"]
               for e2 in doc["events"] for ln in e2["lines"])


def test_manual_adjustment_requires_reason_and_signs_work():
    c, l, doc, ids = _world()
    doc = _seed_stock(c, l, doc, ids)
    with pytest.raises(InventoryError):
        led.post(doc, c, l, {
            "client_uuid": "adj-0", "type": "MANUAL_ADJUSTMENT",
            "actor": "mgr@gvc", "dst": ids["main"],
            "lines": [{"item_id": ids["bead"], "qty": "5", "unit": "each",
                       "sign": -1}]})
    doc, _ = led.post(doc, c, l, {
        "client_uuid": "adj-1", "type": "MANUAL_ADJUSTMENT",
        "actor": "mgr@gvc", "dst": ids["main"],
        "reason": "damaged in the rack, discarded",
        "lines": [{"item_id": ids["bead"], "qty": "5", "unit": "each",
                   "sign": -1}]})
    assert led.on_hand(doc, ids["bead"], ids["main"]) == Decimal(195)
    assert led.rebuild_balances(doc) == doc["balances"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("ALL PASSED")
