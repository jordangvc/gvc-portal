# Inventory — Test Plan

CI gate (compileall + full pytest) runs everything below on every PR;
`python scripts/inventory_verify.py` runs the inventory slice + JS checks
+ build-import as one command.

## Automated

| Layer | File | Covers |
|---|---|---|
| Domain units | tests/test_inventory_domain.py | unit precision/fractional/conversions, org unit overrides, catalog upsert/merge/tracking-lock/barcodes/low-stock, location tree/cycles/tokens/deactivation, employee custody idempotence, search (alias/typo/availability/archived), count sessions (quick + blind + self-approval + reject), attention dedupe/lifecycle |
| Ledger invariants | tests/test_inventory_ledger.py | all 18 invariants incl. CAS-loser concurrency, idempotent replay, reversal exactness + immutability, asset custody, kit conservation, conversion snapshots, negative-stock + override, archived-item block, UTC stamps, projection == replay |
| HTTP + authz | tests/test_inventory_api.py | 401/303 unauthenticated; every role tier against real routes (direct-endpoint attacks 403); transfer/idempotency/stock-gate over the wire; reverse + history integrity; scan resolution; blind count rules over HTTP; import preview-vs-commit; export; unknown item + attention lifecycle; /me caps |
| Field UI | tests/test_inventory_ui.py | page structure vs design system + mobile baseline, inline JS parses post-substitution, outbox behavior in a vm sandbox (persist/reload, UUID stability, 409 → needs_attention, 200 → drain), route registration |
| Office UI | tests/test_inventory_admin_ui.py | structure/sections, JS parses, labels template renders 35 QR labels with page breaks, route registration |
| Cross-portal | existing suite (585+) | role-home invariant with the new pages, admin preset pins, mobile baseline sweep, hub nav |

Evidence run: `scripts/screenshot_portal.py` covers `/ui/inventory` (all
roles holding `inventory*`) and `/ui/inventory/admin` at 4 viewports +
the throttled-4G pass (harness PAGE_FEATURE additions).

## Manual (first live smoke, ~10 minutes)

1. Grant yourself `inventory_manage`; open both pages on a phone.
2. Seed (`scripts/seed_inventory.py --yes`) → hub tile → Drop off 2 items
   + a ladder into Main Storage in ONE cart → receipt shows one txn.
3. Print the Main Storage label; scan it with the phone camera; type the
   code instead (fallback path).
4. Airplane mode → build a transfer → submit → Pending sync → disable
   airplane mode → auto-posts exactly once (check History).
5. Start a blind count on Materials Rack from the admin console on
   desktop; count on the phone; approve; verify the adjustment txn and
   the balance.
6. Reverse the drop-off with a reason; confirm linked pair in History.
7. `/ui/api/inventory/reconcile` → consistent: true.

## Known local-dev quirks

- WeasyPrint isn't installed on the Windows dev box: tests stub it
  (import-time), label PDF returns a clean 503 locally, renders on Cloud
  Run (dep ships in the image).
- `node --check` must be fed FILES, not stdin (Windows charmap trap) —
  the UI tests do this already.
