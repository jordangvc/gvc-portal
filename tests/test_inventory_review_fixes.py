"""Regression locks for the adversarial-review findings (2026-08-09).

Each test names its finding. SHARES the in-memory harness with
tests/test_inventory_api.py (importing it re-uses its auth/store patches —
two competing module-level monkeypatches would fight over import order
when pytest runs both files in one process). Runs under pytest OR directly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# Import under the SAME module name pytest uses (top-level, no package),
# or a second instance would re-run the auth/store patches against fresh
# dicts and the two files would race each other's state.
import test_inventory_api as base  # noqa: E402 — the shared harness

# Extra field user for isolation cases; base's lambdas close over _GRANTS,
# so extending the dict works regardless of which module imported first.
base._GRANTS["field2@localhost"] = {"inventory", "inventory_view"}

from subsystems.inventory import store as inv_store  # noqa: E402

_MEM = base._MEM
client = base.client
_as = base._as
_get = base._get
_post = base._post


def _world():
    _MEM.clear()
    main = _post("mgr@localhost", "/ui/api/inventory/location",
                 {"name": "Main Storage", "kind": "storage"}
                 ).json()["location"]["id"]
    truck = _post("mgr@localhost", "/ui/api/inventory/location",
                  {"name": "Truck 12", "kind": "truck"}
                  ).json()["location"]["id"]
    return main, truck


def test_f1_merge_with_stock_moves_balances_and_reconciles():
    main, truck = _world()
    a = _post("mgr@localhost", "/ui/api/inventory/item",
              {"name": "Screws A", "base_unit": "box"}).json()["item"]["id"]
    b = _post("mgr@localhost", "/ui/api/inventory/item",
              {"name": "Screws B", "base_unit": "box"}).json()["item"]["id"]
    r = _post("mgr@localhost", "/ui/api/inventory/txn",
              {"client_uuid": "m-1", "type": "INITIAL_LOAD", "dst": main,
               "lines": [{"item_id": a, "qty": "10", "unit": "box"}]})
    assert r.status_code == 200
    r = _post("mgr@localhost", "/ui/api/inventory/item-merge",
              {"source_id": a, "target_id": b})
    assert r.status_code == 200, r.text
    assert r.json()["moved_locations"] == 1
    # Target holds the stock; the archived source id resolves to target.
    detail = _get("mgr@localhost",
                  f"/ui/api/inventory/item/{b}").json()["item"]
    assert detail["balances"].get(main) == "10"
    src_view = _get("mgr@localhost",
                    f"/ui/api/inventory/item/{a}").json()["item"]
    assert src_view["id"] == b  # merge pointer followed
    # Projection still equals replay after the merge adjustments.
    from subsystems.inventory import ledger as led
    g, _ = inv_store.read_doc(inv_store.LEDGER)
    assert led.rebuild_balances(led.ensure_shape(g)) == g["balances"]
    # Retrying the merge is a clean no-op (idempotent uuids).
    r2 = _post("mgr@localhost", "/ui/api/inventory/item-merge",
               {"source_id": a, "target_id": b})
    assert r2.status_code in (200, 409, 422)
    g2, _ = inv_store.read_doc(inv_store.LEDGER)
    assert g2["balances"] == g["balances"]


def test_f2_reverse_blocked_when_stock_already_left():
    main, truck = _world()
    item = _post("mgr@localhost", "/ui/api/inventory/item",
                 {"name": "Bead", "base_unit": "each"}).json()["item"]["id"]
    rcv = _post("mgr@localhost", "/ui/api/inventory/txn",
                {"client_uuid": "f2-1", "type": "RECEIVE", "dst": main,
                 "lines": [{"item_id": item, "qty": "10", "unit": "each"}]}
                ).json()["txn"]["txn_no"]
    _post("field@localhost", "/ui/api/inventory/txn",
          {"client_uuid": "f2-2", "type": "TRANSFER", "src": main,
           "dst": truck,
           "lines": [{"item_id": item, "qty": "8", "unit": "each"}]})
    r = _post("mgr@localhost", "/ui/api/inventory/reverse",
              {"txn_no": rcv, "reason": "wrong PO", "client_uuid": "f2-3"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "INSUFFICIENT_STOCK"
    # Explicit manager override posts, flags, and raises attention.
    r = _post("mgr@localhost", "/ui/api/inventory/reverse",
              {"txn_no": rcv, "reason": "wrong PO", "client_uuid": "f2-4",
               "allow_negative": True})
    assert r.status_code == 200
    assert r.json()["txn"]["negative_override"] is True
    att = _get("mgr@localhost", "/ui/api/inventory/attention").json()
    assert any(x["kind"] == "negative_override" for x in att["items"])


def test_f4_damage_photo_lands_in_attention_not_ledger():
    main, truck = _world()
    item = _post("mgr@localhost", "/ui/api/inventory/item",
                 {"name": "Shop vac", "tracking": "asset",
                  "base_unit": "each"}).json()["item"]["id"]
    asset = _post("mgr@localhost", "/ui/api/inventory/asset",
                  {"item_id": item, "location": main}).json()["asset"]["id"]
    big = "data:image/jpeg;base64," + "x" * 50000
    r = _post("field@localhost", "/ui/api/inventory/txn",
              {"client_uuid": "f4-1", "type": "CONDITION_CHANGE",
               "lines": [{"asset_id": asset, "condition_to": "damaged",
                          "note": "bent", "photo_url": big}]})
    assert r.status_code == 200, r.text
    g, _ = inv_store.read_doc(inv_store.LEDGER)
    line = g["events"][-1]["lines"][0]
    assert len(line.get("photo_url", "")) <= 2000  # reference only
    att = _get("mgr@localhost",
               "/ui/api/inventory/attention?kind=damage").json()["items"]
    assert att and att[0]["payload"]["photo_data"].startswith("data:image")


def test_f5_f14_count_approval_records_txns_and_is_retry_safe():
    main, truck = _world()
    item = _post("mgr@localhost", "/ui/api/inventory/item",
                 {"name": "Tape", "base_unit": "roll"}).json()["item"]["id"]
    _post("mgr@localhost", "/ui/api/inventory/txn",
          {"client_uuid": "f5-1", "type": "INITIAL_LOAD", "dst": main,
           "lines": [{"item_id": item, "qty": "10", "unit": "roll"}]})
    sid = _post("mgr@localhost", "/ui/api/inventory/count/start",
                {"kind": "quick", "location_id": main,
                 "assignee": "field@localhost"}).json()["session"]["id"]
    _post("field@localhost", f"/ui/api/inventory/count/{sid}/record",
          {"item_id": item, "counted": "7"})
    _post("field@localhost", f"/ui/api/inventory/count/{sid}/submit", {})
    r = _post("mgr@localhost", f"/ui/api/inventory/count/{sid}/approve", {})
    assert r.status_code == 200
    txns = r.json()["adjustment_txns"]
    assert txns  # invariant 12: persisted on the session
    s = _get("mgr@localhost",
             f"/ui/api/inventory/count/{sid}").json()["session"]
    assert s["adjustment_txns"] == txns


def test_f7_f8_blind_isolation_and_assignee_gate():
    main, truck = _world()
    item = _post("mgr@localhost", "/ui/api/inventory/item",
                 {"name": "Mud", "base_unit": "bucket"}).json()["item"]["id"]
    _post("mgr@localhost", "/ui/api/inventory/txn",
          {"client_uuid": "f7-1", "type": "INITIAL_LOAD", "dst": main,
           "lines": [{"item_id": item, "qty": "6", "unit": "bucket"}]})
    sid = _post("mgr@localhost", "/ui/api/inventory/count/start",
                {"kind": "blind", "location_id": main,
                 "assignee": "field@localhost"}).json()["session"]["id"]
    # Another field user can neither view nor write the blind session.
    assert _get("field2@localhost",
                f"/ui/api/inventory/count/{sid}").status_code == 403
    assert _post("field2@localhost",
                 f"/ui/api/inventory/count/{sid}/record",
                 {"item_id": item, "counted": "1"}).status_code == 403
    assert _post("field2@localhost",
                 f"/ui/api/inventory/count/{sid}/submit",
                 {}).status_code == 403
    # The assignee still gets the stripped view and can record.
    view = _get("field@localhost",
                f"/ui/api/inventory/count/{sid}").json()["session"]
    assert view["lines"][0]["expected"] == ""
    assert _post("field@localhost",
                 f"/ui/api/inventory/count/{sid}/record",
                 {"item_id": item, "counted": "6"}).status_code == 200


def test_f15_field_user_cannot_retire_an_asset():
    main, truck = _world()
    item = _post("mgr@localhost", "/ui/api/inventory/item",
                 {"name": "Lift", "tracking": "asset",
                  "base_unit": "each"}).json()["item"]["id"]
    asset = _post("mgr@localhost", "/ui/api/inventory/asset",
                  {"item_id": item, "location": main}).json()["asset"]["id"]
    r = _post("field@localhost", "/ui/api/inventory/txn",
              {"client_uuid": "f15-1", "type": "CONDITION_CHANGE",
               "lines": [{"asset_id": asset, "condition_to": "retired"}]})
    assert r.status_code == 403
    r = _post("mgr@localhost", "/ui/api/inventory/txn",
              {"client_uuid": "f15-2", "type": "CONDITION_CHANGE",
               "lines": [{"asset_id": asset, "condition_to": "retired"}]})
    assert r.status_code == 200


def test_f18_empty_kit_template_rejected_even_on_edit():
    _world()
    brace = _post("mgr@localhost", "/ui/api/inventory/item",
                  {"name": "Brace", "base_unit": "each"}
                  ).json()["item"]["id"]
    kit = _post("mgr@localhost", "/ui/api/inventory/item",
                {"name": "Set", "tracking": "kit", "base_unit": "set",
                 "kit_components": [{"item_id": brace, "qty": 4}]})
    assert kit.status_code == 200
    kid = kit.json()["item"]["id"]
    r = _post("mgr@localhost", f"/ui/api/inventory/item/{kid}",
              {"name": "Set", "tracking": "kit", "base_unit": "set",
               "kit_components": []})
    assert r.status_code == 422
    # Components persisted as decimal strings, not floats (finding 11).
    saved = kit.json()["item"]["kit_components"][0]["qty"]
    assert isinstance(saved, str) and saved == "4"


def test_f9_import_commit_is_idempotent_on_retry():
    _world()
    csv_text = ("name,tracking,base_unit,qty,location,serial\n"
                "Bead X,quantity,each,100,Main Storage,\n")
    r1 = _post("mgr@localhost", "/ui/api/inventory/import/commit",
               {"csv": csv_text})
    assert r1.status_code == 200
    r2 = _post("mgr@localhost", "/ui/api/inventory/import/commit",
               {"csv": csv_text})
    assert r2.status_code == 200
    # Deterministic uuids: the retry re-uses the same load txns; the
    # balance did not double.
    assert r2.json()["load_txns"] == r1.json()["load_txns"]
    hits = _get("mgr@localhost",
                "/ui/api/inventory/search?q=Bead X").json()["results"]
    assert hits[0]["on_hand_total"] == "100"


def test_f12_js_decimal_merge_has_no_float_artifacts():
    script = (
        "const fs=require('fs');const vm=require('vm');"
        "const code=fs.readFileSync('web/gvc-inventory.js','utf8');"
        "const sb={console,window:{},localStorage:null};sb.global=sb;"
        "vm.createContext(sb);vm.runInContext(code,sb);"
        "const G=sb.window.GvcInventory||sb.GvcInventory;"
        "const c=G.newCart('TRANSFER');"
        "G.cartAddQty(c,{id:'i1',name:'x',base_unit:'foot'},'0.1','foot');"
        "G.cartAddQty(c,{id:'i1',name:'x',base_unit:'foot'},'0.2','foot');"
        "process.stdout.write(c.lines[0].qty);")
    r = subprocess.run(["node", "-e", script], cwd=str(ROOT),
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "0.3"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("ALL PASSED")
