---
name: inventory-release
description: Verify and ship an inventory change end-to-end (tests → PR → gate → deploy → live smoke)
---

1. `python scripts/inventory_verify.py` — must end ALL INVENTORY CHECKS
   PASSED.
2. UI touched? `python scripts/screenshot_portal.py --roles crew,ops` and
   eyeball the inventory pages in docs/screenshots/.
3. Footer: bump hub `rN` + the pinned assertions (tests/test_hub_home.py,
   tests/test_hub_stash.py) in the same commit; dated note in CLAUDE.md.
4. Branch `cursor/<topic>` → push → PR. The auto-merge gate runs
   compileall + FULL pytest; master auto-deploys.
5. Post-deploy: `/health` → `inventory_store_ok: true` and
   `inventory_events` sane; open /ui/inventory on a phone; post one
   test transaction and reverse it with reason "release smoke".
6. `GET /ui/api/inventory/reconcile` → `consistent: true`.
Rollback: Cloud Run → Revisions → previous revision 100% (state in GCS is
unaffected; ledger is append-only so old code reads new docs).
