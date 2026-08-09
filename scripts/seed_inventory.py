"""Seed the inventory store with GVC's starter world — DEV/TEST ONLY.

Everything goes through the same flow layer the app uses (INITIAL_LOAD
transactions, never direct balance writes), so the seed is itself a ledger
exercise. Idempotent: re-runs skip items/locations that already exist by
name and skip the load when the seed's client_uuid already posted.

Usage (from the repo root, with the state-bucket env + SA json available):

    python scripts/seed_inventory.py --dry-run     # show the plan
    python scripts/seed_inventory.py --yes         # write to the store

Refuses to run without --yes; prints the target bucket first. Never
creates credentials or users — grants stay in /ui/admin.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LOCATIONS = [
    {"name": "Main Storage", "kind": "storage"},
    {"name": "Scaffold Zone", "kind": "zone", "parent": "Main Storage"},
    {"name": "Materials Rack", "kind": "zone", "parent": "Main Storage"},
    {"name": "Secondary Storage", "kind": "storage"},
    {"name": "Truck 12", "kind": "truck"},
    {"name": "Sample Job Site", "kind": "job_site"},
    {"name": "Repair Area", "kind": "repair"},
]

ITEMS = [
    {"name": "Corner bead", "base_unit": "each", "category": "Materials",
     "aliases": ["bead"], "min_qty": {"Main Storage": 50}},
    {"name": "Drywall nails", "base_unit": "box", "category": "Materials",
     "aliases": ["nails"]},
    {"name": "Drywall screws", "base_unit": "box", "category": "Materials",
     "aliases": ["screws", "skrews"], "conversions": {"case": 12}},
    {"name": "Joint compound", "base_unit": "bucket",
     "category": "Materials", "aliases": ["mud"]},
    {"name": "Paper tape", "base_unit": "roll", "category": "Materials",
     "aliases": ["tape"]},
    {"name": "Scaffold brace", "base_unit": "each",
     "category": "Scaffold"},
    {"name": "Walk board", "base_unit": "each", "category": "Scaffold",
     "aliases": ["walkboard", "plank"]},
    {"name": "8-foot ladder", "tracking": "asset", "base_unit": "each",
     "category": "Equipment"},
    {"name": "12-foot ladder", "tracking": "asset", "base_unit": "each",
     "category": "Equipment"},
    {"name": "Shop vac", "tracking": "asset", "base_unit": "each",
     "category": "Equipment", "aliases": ["vacuum"]},
    {"name": "Laser level", "tracking": "asset", "base_unit": "each",
     "category": "Equipment"},
]

KIT = {"name": "Standard scaffold set", "tracking": "kit",
       "base_unit": "set", "category": "Scaffold",
       "components": [("Scaffold brace", 4), ("Walk board", 2)]}

LOADS = [  # (item, qty, unit, location)
    ("Corner bead", "200", "each", "Materials Rack"),
    ("Drywall nails", "12", "box", "Materials Rack"),
    ("Drywall screws", "30", "box", "Materials Rack"),
    ("Joint compound", "18", "bucket", "Materials Rack"),
    ("Paper tape", "40", "roll", "Materials Rack"),
    ("Scaffold brace", "24", "each", "Scaffold Zone"),
    ("Walk board", "12", "each", "Scaffold Zone"),
]

ASSETS = [  # (item, serial, location)
    ("8-foot ladder", "WERNER-8-001", "Main Storage"),
    ("12-foot ladder", "WERNER-12-001", "Main Storage"),
    ("Shop vac", "RIDGID-SV-01", "Truck 12"),
    ("Laser level", "DEWALT-LL-01", "Main Storage"),
]

ACTOR = "seed@greenvalleycontractors.com"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bucket = os.environ.get("GVC_PORTAL_STATE_BUCKET") \
        or os.environ.get("GVC_GCS_PREVIEW_BUCKET") or "(unset)"
    print(f"Target state bucket: {bucket}")
    if args.dry_run or not args.yes:
        print(f"DRY RUN — would create {len(LOCATIONS)} locations, "
              f"{len(ITEMS) + 1} items, {len(ASSETS)} assets, "
              f"{len(LOADS)} opening balances, 1 kit assembly.")
        if not args.yes:
            print("Pass --yes to write.")
        return 0

    from orchestrators import inventory_flow as flow
    from subsystems.inventory import store
    from subsystems.inventory.model import InventoryError

    cdoc, _ = store.read_doc(store.CATALOG)
    ldoc, _ = store.read_doc(store.LOCATIONS)
    have_items = {i["name"].lower() for i in
                  (cdoc.get("items") or {}).values()}
    have_locs = {x["name"].lower(): x["id"] for x in
                 (ldoc.get("locations") or {}).values()}

    loc_ids: dict[str, str] = dict(have_locs)
    for spec in LOCATIONS:
        if spec["name"].lower() in have_locs:
            print(f"  skip location (exists): {spec['name']}")
            continue
        payload = dict(spec)
        if payload.get("parent"):
            payload["parent"] = loc_ids[payload["parent"].lower()]
        loc = flow.save_location(payload, actor=ACTOR)["location"]
        loc_ids[spec["name"].lower()] = loc["id"]
        print(f"  location: {spec['name']} -> {loc['id']}")

    item_ids: dict[str, str] = {}
    for spec in ITEMS:
        if spec["name"].lower() in have_items:
            print(f"  skip item (exists): {spec['name']}")
            cdoc, _ = store.read_doc(store.CATALOG)
            item_ids[spec["name"]] = next(
                i["id"] for i in cdoc["items"].values()
                if i["name"].lower() == spec["name"].lower())
            continue
        payload = {k: v for k, v in spec.items()
                   if k not in ("min_qty",)}
        if spec.get("min_qty"):
            payload["min_qty"] = {loc_ids[k.lower()]: v for k, v in
                                  spec["min_qty"].items()}
        item = flow.save_item(payload, actor=ACTOR)["item"]
        item_ids[spec["name"]] = item["id"]
        print(f"  item: {spec['name']} -> {item['id']}")

    if KIT["name"].lower() not in have_items:
        payload = {k: v for k, v in KIT.items() if k != "components"}
        payload["kit_components"] = [
            {"item_id": item_ids[n], "qty": q} for n, q in
            KIT["components"]]
        item = flow.save_item(payload, actor=ACTOR)["item"]
        item_ids[KIT["name"]] = item["id"]
        print(f"  kit template: {KIT['name']} -> {item['id']}")

    loads_by_loc: dict[str, list] = {}
    for name, qty, unit, loc in LOADS:
        loads_by_loc.setdefault(loc, []).append(
            {"item_id": item_ids[name], "qty": qty, "unit": unit})
    for loc, lines in loads_by_loc.items():
        try:
            res = flow.post_transaction(
                {"client_uuid": f"seed-load-{loc.lower().replace(' ', '-')}",
                 "type": "INITIAL_LOAD", "dst": loc_ids[loc.lower()],
                 "note": "seed", "lines": lines},
                actor=ACTOR, can_manage=True)
            state = "duplicate" if res.get("already") else "posted"
            print(f"  load {loc}: {state} ({res['txn']['txn_no']})")
        except InventoryError as e:
            print(f"  load {loc}: SKIPPED ({e.code}: {e.detail})")

    g, _ = store.read_doc(store.LEDGER)
    have_serials = {a.get("serial") for a in (g.get("assets") or {}).values()}
    for item_name, serial, loc in ASSETS:
        if serial in have_serials:
            print(f"  skip asset (exists): {serial}")
            continue
        a = flow.create_asset({"item_id": item_ids[item_name],
                               "location": loc_ids[loc.lower()],
                               "serial": serial}, actor=ACTOR)["asset"]
        print(f"  asset: {serial} -> {a['id']}")

    print("Seed complete. Next: grant `inventory` features in /ui/admin, "
          "print labels from /ui/inventory/admin, run a first count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
