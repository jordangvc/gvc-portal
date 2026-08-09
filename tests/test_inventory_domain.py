"""Inventory domain tests: units, catalog, locations, search, counts,
attention. Pure — no GCS, no network. Runs under pytest OR directly."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from subsystems.inventory import attention as att  # noqa: E402
from subsystems.inventory import catalog as cat  # noqa: E402
from subsystems.inventory import counts as cnt  # noqa: E402
from subsystems.inventory import locations as locs  # noqa: E402
from subsystems.inventory import search as srch  # noqa: E402
from subsystems.inventory import units as un  # noqa: E402
from subsystems.inventory.model import InventoryError  # noqa: E402


# -------------------------------------------------------------------- units

def test_units_precision_and_fractional_rules():
    units = un.units_registry({})
    # 'each' is integral — 2.5 each refused, no silent rounding.
    with pytest.raises(InventoryError) as e:
        un.validate_precision(Decimal("2.5"), "each", units)
    assert e.value.code == "QTY_PRECISION"
    un.validate_precision(Decimal("2"), "each", units)
    # box allows one decimal (half a box) but not two.
    un.validate_precision(Decimal("0.5"), "box", units)
    with pytest.raises(InventoryError):
        un.validate_precision(Decimal("0.55"), "box", units)
    # zero and negatives refused at parse.
    for bad in ("0", "-3", "nan", "abc", None):
        with pytest.raises(InventoryError):
            un.parse_qty(bad)


def test_unit_conversion_and_missing_conversion():
    item = {"id": "i", "name": "Screws", "base_unit": "box",
            "conversions": {"case": 12}}
    units = un.units_registry({})
    snap = un.normalize_line_qty(item, "0.5", "case", units)
    assert snap["base_qty"] == "6"
    assert snap["factor"] == "12"
    with pytest.raises(InventoryError) as e:
        un.normalize_line_qty(item, "1", "roll", units)
    assert e.value.code == "NO_CONVERSION"


def test_org_defined_unit_overrides_default():
    catalog_doc = {"units": {"box": {"label": "Box", "precision": 0,
                                     "fractional": False}}}
    units = un.units_registry(catalog_doc)
    with pytest.raises(InventoryError):
        un.validate_precision(Decimal("0.5"), "box", units)


# ------------------------------------------------------------------ catalog

def test_catalog_upsert_merge_and_tracking_lock():
    doc: dict = {}
    doc, a = cat.upsert_item(doc, {"name": "Drywall nails",
                                   "base_unit": "box",
                                   "aliases": ["nails"]}, actor="t")
    doc, b = cat.upsert_item(doc, {"name": "Drywall nails 1-3/8",
                                   "base_unit": "box"}, actor="t")
    # tracking is frozen after creation.
    with pytest.raises(InventoryError) as e:
        cat.upsert_item(doc, {"name": "Drywall nails",
                              "tracking": "asset", "base_unit": "box"},
                        actor="t", item_id=a["id"])
    assert e.value.code == "TRACKING_LOCKED"

    doc, merged = cat.merge_items(doc, b["id"], a["id"], actor="mgr")
    assert doc["items"][b["id"]]["merged_into"] == a["id"]
    assert doc["items"][b["id"]]["archived"] is True
    assert "Drywall nails 1-3/8" in merged["aliases"]
    # Old id resolves through the merge pointer.
    assert cat.get_item(doc, b["id"])["id"] == a["id"]
    with pytest.raises(InventoryError):
        cat.merge_items(doc, a["id"], a["id"], actor="mgr")


def test_catalog_barcode_and_low_stock():
    doc: dict = {}
    doc, item = cat.upsert_item(doc, {
        "name": "Paper tape", "base_unit": "roll",
        "barcodes": ["012345678905"],
        "min_qty": {"loc-1": 10}}, actor="t")
    assert cat.resolve_barcode(doc, "012345678905")["id"] == item["id"]
    assert cat.resolve_barcode(doc, item["scan_token"])["id"] == item["id"]
    assert cat.resolve_barcode(doc, "nope") is None
    hits = cat.low_stock_hits(doc, {item["id"]: {"loc-1": "4"}})
    assert hits and hits[0]["on_hand"] == 4.0
    assert not cat.low_stock_hits(doc, {item["id"]: {"loc-1": "12"}})


# ---------------------------------------------------------------- locations

def test_location_tree_tokens_and_deactivation_guard():
    doc: dict = {}
    doc, main = locs.upsert_location(doc, {"name": "Main Storage",
                                           "kind": "storage"}, actor="t")
    doc, zone = locs.upsert_location(doc, {"name": "Scaffold Zone",
                                           "kind": "zone",
                                           "parent": main["id"]}, actor="t")
    assert locs.path_name(doc, zone["id"]) == "Main Storage › Scaffold Zone"
    # Trucks are leaf-only.
    doc, truck = locs.upsert_location(doc, {"name": "Truck 12",
                                            "kind": "truck"}, actor="t")
    with pytest.raises(InventoryError):
        locs.upsert_location(doc, {"name": "Bin", "kind": "shelf",
                                   "parent": truck["id"]}, actor="t")
    # Cycle guard.
    with pytest.raises(InventoryError):
        locs.upsert_location(doc, {"name": "Main Storage",
                                   "kind": "storage",
                                   "parent": zone["id"]},
                             actor="t", loc_id=main["id"])
    # Token resolve + rotation revokes the old label.
    tok = zone["scan_token"]
    assert locs.resolve_token(doc, tok)["id"] == zone["id"]
    doc, zone2 = locs.rotate_token(doc, zone["id"], actor="t")
    assert locs.resolve_token(doc, tok) is None
    assert locs.resolve_token(doc, zone2["scan_token"])["id"] == zone["id"]
    # Deactivation needs empty holdings and no active children.
    with pytest.raises(InventoryError):
        locs.set_active(doc, main["id"], False, actor="t",
                        holdings_empty=False)
    with pytest.raises(InventoryError):
        locs.set_active(doc, main["id"], False, actor="t",
                        holdings_empty=True)  # zone still active under it
    doc, _ = locs.set_active(doc, zone["id"], False, actor="t",
                             holdings_empty=True)
    doc, _ = locs.set_active(doc, main["id"], False, actor="t",
                             holdings_empty=True)
    with pytest.raises(InventoryError):
        locs.get_location(doc, main["id"])


def test_employee_location_idempotent():
    doc: dict = {}
    doc, me = locs.ensure_employee_location(doc, "Mark@GVC.com", "Mark W")
    doc2, me2 = locs.ensure_employee_location(doc, "mark@gvc.com")
    assert doc2 is doc and me2["id"] == me["id"]


# ------------------------------------------------------------------- search

def _search_world():
    c: dict = {}
    c, screws = cat.upsert_item(c, {"name": "Drywall screws",
                                    "base_unit": "box",
                                    "aliases": ["screws", "skrews"],
                                    "category": "Materials"}, actor="t")
    c, nails = cat.upsert_item(c, {"name": "Drywall nails",
                                   "base_unit": "box"}, actor="t")
    c, mud = cat.upsert_item(c, {"name": "Joint compound",
                                 "base_unit": "bucket",
                                 "aliases": ["mud"]}, actor="t")
    ledger = {"balances": {screws["id"]: {"loc-main": "4"},
                           nails["id"]: {"loc-truck": "2"}}}
    return c, ledger, screws, nails, mud


def test_search_alias_typo_and_availability_boost():
    c, ledger, screws, nails, mud = _search_world()
    # Alias exact.
    hits = srch.search_items(c, ledger, "mud")
    assert hits[0]["item"]["id"] == mud["id"]
    # Typo tolerance: 'scews' still finds screws.
    hits = srch.search_items(c, ledger, "scews")
    assert any(h["item"]["id"] == screws["id"] for h in hits)
    # Availability boost: at loc-main, screws outrank nails for "drywall".
    hits = srch.search_items(c, ledger, "drywall",
                             location_id="loc-main")
    assert hits[0]["item"]["id"] == screws["id"]
    assert hits[0]["on_hand_here"] == "4"
    # Archived hidden by default.
    c2, _ = cat.set_archived(c, mud["id"], True, actor="t")
    assert not srch.search_items(c2, ledger, "mud")
    assert srch.search_items(c2, ledger, "mud", include_archived=True)


# ------------------------------------------------------------------- counts

def test_count_session_quick_flow_variances():
    doc: dict = {}
    doc, s = cnt.create_session(
        doc, kind="quick", location_id="loc-main",
        ledger_balances_at_loc={"itm-1": "10", "itm-2": "5"},
        item_names={"itm-1": "Screws", "itm-2": "Bead"},
        assignee="mark@gvc", actor="mgr@gvc")
    sid = s["id"]
    doc, _ = cnt.record_line(doc, sid, "itm-1", counted="8")
    # Submit blocked while a line is unanswered.
    with pytest.raises(InventoryError) as e:
        cnt.submit(doc, sid, actor="mark@gvc")
    assert e.value.code == "COUNT_INCOMPLETE"
    # Zero must be explicit; skip needs a reason.
    with pytest.raises(InventoryError):
        cnt.record_line(doc, sid, "itm-2", skipped=True)
    doc, _ = cnt.record_line(doc, sid, "itm-2", counted="0")
    doc, s = cnt.submit(doc, sid, actor="mark@gvc")
    assert s["status"] == "submitted"

    doc, s, txns = cnt.approve(doc, sid, actor="mgr@gvc")
    assert s["status"] == "approved"
    assert len(txns) == 1 and txns[0]["type"] == "COUNT_ADJUSTMENT"
    lines = {ln["item_id"]: ln for ln in txns[0]["lines"]}
    assert lines["itm-1"]["sign"] == -1 and lines["itm-1"]["qty"] == "2"
    assert lines["itm-2"]["sign"] == -1 and lines["itm-2"]["qty"] == "5"


def test_blind_audit_hides_expected_and_blocks_self_approval():
    doc: dict = {}
    doc, s = cnt.create_session(
        doc, kind="blind", location_id="loc-main",
        ledger_balances_at_loc={"itm-1": "10"},
        item_names={"itm-1": "Screws"},
        assignee="mark@gvc", actor="mgr@gvc")
    blind = cnt.strip_expected(s)
    assert blind["lines"][0]["expected"] == ""
    assert s["lines"][0]["expected"] == "10"  # stored intact
    doc, _ = cnt.record_line(doc, s["id"], "itm-1", counted="9")
    doc, _ = cnt.submit(doc, s["id"], actor="mark@gvc")
    with pytest.raises(InventoryError) as e:
        cnt.approve(doc, s["id"], actor="mark@gvc")
    assert e.value.code == "SELF_APPROVAL"
    doc, s2, txns = cnt.approve(doc, s["id"], actor="mgr@gvc")
    assert txns and txns[0]["lines"][0]["qty"] == "1"
    # Rejection posts nothing.
    doc3: dict = {}
    doc3, s3 = cnt.create_session(
        doc3, kind="blind", location_id="loc-main",
        ledger_balances_at_loc={"itm-1": "10"},
        item_names={}, assignee="a@gvc", actor="mgr@gvc")
    doc3, _ = cnt.record_line(doc3, s3["id"], "itm-1", counted="3")
    doc3, _ = cnt.submit(doc3, s3["id"], actor="a@gvc")
    doc3, s3, txns3 = cnt.approve(doc3, s3["id"], actor="mgr@gvc",
                                  reject=True)
    assert s3["status"] == "rejected" and txns3 == []


# ---------------------------------------------------------------- attention

def test_attention_raise_dedupe_and_resolve():
    doc: dict = {}
    doc, rec = att.raise_item(doc, kind="low_stock", title="Screws low",
                              dedupe_key="low:itm-1:loc-main",
                              actor="system")
    doc2, rec2 = att.raise_item(doc, kind="low_stock", title="Screws low",
                                dedupe_key="low:itm-1:loc-main",
                                actor="system")
    assert doc2 is doc and rec2["id"] == rec["id"]  # deduped, no write
    assert len(att.open_items(doc)) == 1
    with pytest.raises(InventoryError):
        att.update_status(doc, rec["id"], status="resolved", actor="m",
                          note="  ")
    doc, rec = att.update_status(doc, rec["id"], status="resolved",
                                 actor="mgr@gvc", note="restocked 20")
    assert not att.open_items(doc)
    # Once resolved, the same dedupe key may raise a fresh record.
    doc, rec3 = att.raise_item(doc, kind="low_stock", title="Screws low",
                               dedupe_key="low:itm-1:loc-main",
                               actor="system")
    assert rec3["id"] != rec["id"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("ALL PASSED")
