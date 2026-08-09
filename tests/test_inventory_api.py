"""Inventory API tests — real HTTP through the FastAPI app.

Server-side authorization is the point (spec §4): hidden buttons are not
security. Every role tier is exercised over the wire with real signed
session cookies (throwaway secret) against an IN-MEMORY inventory store —
no GCS, no network.

Runs under pytest OR directly: ``python tests/test_inventory_api.py``.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# --- environment BEFORE app import -----------------------------------------
os.environ["GVC_SESSION_SECRET"] = "t" * 64
os.environ["GVC_GRANTS_BACKEND"] = "env"
os.environ.pop("GVC_UI_DEV_BYPASS", None)
os.environ.pop("GVC_PORTAL_ALLOWED_EMAILS", None)

# WeasyPrint stub (native libs absent on dev boxes; PDF paths untested here).
_wp = types.ModuleType("weasyprint")
_wp.HTML = object
_wp.CSS = object
sys.modules.setdefault("weasyprint", _wp)

from shared import access  # noqa: E402
from shared import auth as portal_auth  # noqa: E402

_GRANTS = {
    "field@localhost": {"inventory", "inventory_view"},
    "mgr@localhost": {"inventory_manage", "inventory", "inventory_view"},
    "aud@localhost": {"inventory_view"},
    "none@localhost": {"morning", "fieldguide"},
}
access.is_provisioned = lambda email: (email or "").lower() in _GRANTS
access.effective_features = lambda email: set(
    _GRANTS.get((email or "").lower(), set()))

# --- in-memory inventory store ----------------------------------------------
from subsystems.inventory import store as inv_store  # noqa: E402

_MEM: dict[str, dict] = {}


def _read(name):
    import copy
    return copy.deepcopy(_MEM.get(name, {})), 1


def _write(name, doc, *, generation):
    import copy
    _MEM[name] = copy.deepcopy(doc)
    return generation + 1


def _mutate(name, fn):
    doc, gen = _read(name)
    new_doc, result = fn(doc)
    if new_doc is not doc:
        _write(name, new_doc, generation=gen)
    return result


inv_store.read_doc = _read
inv_store.write_doc = _write
inv_store.mutate = _mutate

from fastapi.testclient import TestClient  # noqa: E402

from app.service import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)


def _as(email: str) -> dict:
    return {"gvc_session": portal_auth.make_session_cookie(email)}


def _get(email, path, **kw):
    return client.get(path, cookies=_as(email), **kw)


def _post(email, path, body, **kw):
    return client.post(path, json=body, cookies=_as(email), **kw)


# ------------------------------------------------------------------- authz

def test_unauthenticated_page_redirects_and_api_401():
    assert client.get("/ui/inventory", follow_redirects=False
                      ).status_code == 303
    assert client.get("/ui/api/inventory/overview").status_code == 401


def test_role_tiers_enforced_server_side():
    _MEM.clear()
    # No inventory grant at all → 403 even on reads.
    assert _get("none@localhost",
                "/ui/api/inventory/overview").status_code == 403
    # Auditor reads but cannot move or manage.
    assert _get("aud@localhost",
                "/ui/api/inventory/overview").status_code == 200
    assert _post("aud@localhost", "/ui/api/inventory/txn",
                 {}).status_code == 403
    assert _post("aud@localhost", "/ui/api/inventory/item",
                 {"name": "X"}).status_code == 403
    # Field user moves but cannot manage — calling the endpoint directly
    # (spec e2e #10) is refused, not just hidden.
    for path, body in (
            ("/ui/api/inventory/item", {"name": "X"}),
            ("/ui/api/inventory/reverse", {"txn_no": "INV-000001",
                                           "reason": "x",
                                           "client_uuid": "u1"}),
            ("/ui/api/inventory/import/commit", {"csv": "name\nX"}),
            ("/ui/api/inventory/count/start", {"location_id": "x"}),
            ("/ui/api/inventory/attention/att-1", {"status": "resolved"})):
        assert _post("field@localhost", path, body).status_code == 403, path
    assert _get("field@localhost",
                "/ui/api/inventory/attention").status_code == 403
    # Admin-only reconcile.
    assert _get("mgr@localhost",
                "/ui/api/inventory/reconcile").status_code == 403


# ---------------------------------------------------------- end-to-end flow

def _setup_world():
    _MEM.clear()
    r = _post("mgr@localhost", "/ui/api/inventory/location",
              {"name": "Main Storage", "kind": "storage"})
    assert r.status_code == 200, r.text
    main = r.json()["location"]["id"]
    r = _post("mgr@localhost", "/ui/api/inventory/location",
              {"name": "Truck 12", "kind": "truck"})
    truck = r.json()["location"]["id"]
    r = _post("mgr@localhost", "/ui/api/inventory/item",
              {"name": "Drywall screws", "base_unit": "box",
               "aliases": ["screws"], "conversions": {"case": 12}})
    screws = r.json()["item"]["id"]
    r = _post("mgr@localhost", "/ui/api/inventory/txn",
              {"client_uuid": "seed-1", "type": "INITIAL_LOAD",
               "dst": main,
               "lines": [{"item_id": screws, "qty": "10", "unit": "box"}]})
    assert r.status_code == 200, r.text
    return main, truck, screws


def test_field_transfer_idempotency_and_stock_gate_over_http():
    main, truck, screws = _setup_world()
    body = {"client_uuid": "http-1", "type": "TRANSFER", "src": main,
            "dst": truck,
            "lines": [{"item_id": screws, "qty": "4", "unit": "box"}]}
    r = _post("field@localhost", "/ui/api/inventory/txn", body)
    assert r.status_code == 200 and r.json()["already"] is False
    # Same client_uuid retried (offline replay) → no double movement.
    r2 = _post("field@localhost", "/ui/api/inventory/txn", dict(body))
    assert r2.status_code == 200 and r2.json()["already"] is True
    assert r2.json()["txn"]["txn_no"] == r.json()["txn"]["txn_no"]
    # Over-pick refused with the envelope.
    r3 = _post("field@localhost", "/ui/api/inventory/txn",
               {"client_uuid": "http-2", "type": "ISSUE", "src": main,
                "dst": truck,
                "lines": [{"item_id": screws, "qty": "99", "unit": "box"}]})
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "INSUFFICIENT_STOCK"
    assert "advice" in r3.json()["detail"]
    # Field user may NOT self-override negative stock.
    r4 = _post("field@localhost", "/ui/api/inventory/txn",
               {"client_uuid": "http-3", "type": "ISSUE", "src": main,
                "dst": truck, "allow_negative": True,
                "reason": "trust me",
                "lines": [{"item_id": screws, "qty": "99", "unit": "box"}]})
    assert r4.status_code == 403
    # Balances visible to the auditor.
    r5 = _get("aud@localhost", f"/ui/api/inventory/location/{truck}")
    assert r5.json()["items"][0]["qty"] == "4"


def test_manager_reverse_and_history_intact():
    main, truck, screws = _setup_world()
    r = _post("field@localhost", "/ui/api/inventory/txn",
              {"client_uuid": "rv-a", "type": "TRANSFER", "src": main,
               "dst": truck,
               "lines": [{"item_id": screws, "qty": "2", "unit": "box"}]})
    txn_no = r.json()["txn"]["txn_no"]
    r2 = _post("mgr@localhost", "/ui/api/inventory/reverse",
               {"txn_no": txn_no, "reason": "wrong truck",
                "client_uuid": "rv-b"})
    assert r2.status_code == 200
    hist = _get("aud@localhost",
                "/ui/api/inventory/history?limit=10").json()["events"]
    types_seen = [e["type"] for e in hist]
    assert "REVERSAL" in types_seen and "TRANSFER" in types_seen
    orig = next(e for e in hist if e["txn_no"] == txn_no)
    assert orig["status"] == "reversed" and orig["reversed_by"]
    r3 = _get("aud@localhost", f"/ui/api/inventory/location/{main}")
    assert r3.json()["items"][0]["qty"] == "10"


def test_scan_resolution_and_unknown_code():
    main, truck, screws = _setup_world()
    loc = _get("aud@localhost",
               f"/ui/api/inventory/location/{main}").json()["location"]
    r = _get("field@localhost",
             f"/ui/api/inventory/scan?code={loc['scan_token']}")
    assert r.json()["kind"] == "location"
    item = _get("aud@localhost",
                f"/ui/api/inventory/item/{screws}").json()["item"]
    r = _get("field@localhost",
             f"/ui/api/inventory/scan?code={item['scan_token']}")
    assert r.json()["kind"] == "item"
    r = _get("field@localhost", "/ui/api/inventory/scan?code=zzz-nope")
    assert r.json()["kind"] == "unknown"


def test_count_flow_over_http_blind_rules():
    main, truck, screws = _setup_world()
    r = _post("mgr@localhost", "/ui/api/inventory/count/start",
              {"kind": "blind", "location_id": main,
               "assignee": "field@localhost"})
    sid = r.json()["session"]["id"]
    # Assignee's view hides expected quantities.
    view = _get("field@localhost",
                f"/ui/api/inventory/count/{sid}").json()["session"]
    assert view["lines"][0]["expected"] == ""
    # Manager's view keeps them.
    view2 = _get("mgr@localhost",
                 f"/ui/api/inventory/count/{sid}").json()["session"]
    assert view2["lines"][0]["expected"] == "10"
    r = _post("field@localhost", f"/ui/api/inventory/count/{sid}/record",
              {"item_id": screws, "counted": "8"})
    assert r.status_code == 200
    r = _post("field@localhost", f"/ui/api/inventory/count/{sid}/submit",
              {})
    assert r.status_code == 200
    assert r.json()["variances"][0]["variance"] == "-2"
    # Field user cannot approve; manager approval posts the adjustment.
    r = _post("field@localhost", f"/ui/api/inventory/count/{sid}/approve",
              {})
    assert r.status_code == 403
    r = _post("mgr@localhost", f"/ui/api/inventory/count/{sid}/approve",
              {})
    assert r.status_code == 200 and r.json()["adjustment_txns"]
    r = _get("aud@localhost", f"/ui/api/inventory/location/{main}")
    assert r.json()["items"][0]["qty"] == "8"


def test_import_preview_commit_and_export():
    _MEM.clear()
    _post("mgr@localhost", "/ui/api/inventory/location",
          {"name": "Main Storage", "kind": "storage"})
    csv_text = ("name,tracking,base_unit,qty,location,serial\n"
                "Corner bead,quantity,each,200,Main Storage,\n"
                "Paper tape,quantity,roll,24,Main Storage,\n"
                "8-foot ladder,asset,each,,Main Storage,W-123\n")
    r = _post("mgr@localhost", "/ui/api/inventory/import/preview",
              {"csv": csv_text})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True and r.json()["valid"] == 3
    # Preview writes nothing.
    assert _get("aud@localhost", "/ui/api/inventory/search?q=bead"
                ).json()["results"] == []
    r = _post("mgr@localhost", "/ui/api/inventory/import/commit",
              {"csv": csv_text})
    assert r.status_code == 200
    out = r.json()
    assert out["items_created"] == 3 and out["assets_created"] == 1
    assert out["load_txns"]
    hits = _get("field@localhost",
                "/ui/api/inventory/search?q=bead").json()["results"]
    assert hits and hits[0]["on_hand_total"] == "200"
    # Bad rows: nothing commits.
    bad = "name,qty,location\nThing,5,Nowhere Storage\n"
    r = _post("mgr@localhost", "/ui/api/inventory/import/commit",
              {"csv": bad})
    assert r.status_code == 422
    # Export runs for the read-only auditor.
    r = _get("aud@localhost", "/ui/api/inventory/export.csv?kind=balances")
    assert r.status_code == 200 and "Corner bead" in r.text


def test_unknown_item_and_attention_lifecycle():
    main, truck, screws = _setup_world()
    r = _post("field@localhost", "/ui/api/inventory/unknown-item",
              {"name": "green corner thing", "qty": "3", "unit": "each",
               "location_id": main})
    assert r.status_code == 200
    aid = r.json()["attention"]["id"]
    lst = _get("mgr@localhost", "/ui/api/inventory/attention").json()
    assert any(x["id"] == aid for x in lst["items"])
    r = _post("mgr@localhost", f"/ui/api/inventory/attention/{aid}",
              {"status": "resolved", "note": "matched to corner bead"})
    assert r.status_code == 200
    lst = _get("mgr@localhost", "/ui/api/inventory/attention").json()
    assert not any(x["id"] == aid for x in lst["items"])


def test_me_endpoint_reports_caps_and_custody():
    _MEM.clear()
    me = _get("field@localhost", "/ui/api/inventory/me").json()
    assert me["can_move"] is True and me["can_manage"] is False
    assert me["custody"]["kind"] == "employee"
    aud = _get("aud@localhost", "/ui/api/inventory/me").json()
    assert aud["can_move"] is False and "custody" not in aud


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("ALL PASSED")
