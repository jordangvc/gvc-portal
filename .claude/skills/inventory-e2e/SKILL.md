---
name: inventory-e2e
description: Run the seeded end-to-end inventory workflow locally (no GCS)
---

1. In-memory world: the header block of tests/test_inventory_api.py is
   the harness — throwaway session secret, patched grants
   (field@/mgr@/aud@), weasyprint stub, in-memory store. Copy that
   pattern for any new e2e file.
2. Full flow already covered there: setup → INITIAL_LOAD → transfer →
   idempotent retry → stock conflict → reverse → blind count → import
   dry-run/commit → export → attention. Run:
   `python -m pytest tests/test_inventory_api.py -q`
3. Browser-level: `python scripts/screenshot_portal.py --roles crew,ops`
   drives /ui/inventory as real roles (local uvicorn + minted cookies)
   and captures evidence; `--throttle` adds the weak-signal pass.
4. Against the REAL store (careful — writes): seed a scratch bucket by
   exporting GVC_PORTAL_STATE_BUCKET=<scratch>, then
   `python scripts/seed_inventory.py --yes`, then exercise the UI via
   `uvicorn app.service:app` with GVC_UI_DEV_BYPASS=1. Never point this
   at the production bucket.
