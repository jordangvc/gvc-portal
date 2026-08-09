# Inventory — Release Evidence (2026-08-09)

Branch `cursor/inventory-core`; ships through the standard gate
(compileall + FULL pytest on Linux/CI with real WeasyPrint) → squash-merge
→ auto-deploy to Cloud Run. The PR run is the authoritative full-suite
record; numbers below are the local evidence trail.

## Commands and results

| Check | Command | Result |
|---|---|---|
| Inventory suite | `pytest tests/test_inventory_{domain,ledger,api,ui,admin_ui,review_fixes}.py` | **59 passed** |
| Release check | `python scripts/inventory_verify.py` | **ALL INVENTORY CHECKS PASSED** (compile + 59 inventory + admin-roles/role-home/mobile-baseline adjacents + node --check on module JS and both pages' inline JS) |
| Screenshot matrix | `python scripts/screenshot_portal.py` | **328 screenshots** — 7 role presets × every reachable page × 4 viewports, `/ui/inventory` + `/ui/inventory/admin` included — **0 findings, 0 console errors, 0 auth bounces** → `docs/screenshots/` |
| Throttled 4G | `… --throttle` (150ms RTT / 1.6Mbps) | **28 shots**, every role home first-content ≤1.5s, no blanks/hangs |
| Domain bench | 500 items / 30 locations / 2k lines | post 14ms · search 12ms · overview 8ms (PERFORMANCE.md) |
| Local full suite | `pytest -q` | 1 known local-only collection error (WeasyPrint native libs absent on the Windows dev box — TEST_PLAN.md); CI gate runs the full suite with WeasyPrint installed |

## Adversarial review (spec §25)

Fresh-context reviewer (not the author) audited ledger correctness, authz,
offline/idempotency, field usability, deployment, and docs-vs-reality:
**18 findings — 4 high, 10 medium, 4 low. All material findings fixed**,
each locked by a named regression test in
`tests/test_inventory_review_fixes.py`:

- F1 merge-with-stock stranded balances → transfers now post BEFORE the
  catalog merge, deterministic uuids, retry-safe.
- F2 reversals could silently drive stock negative → guarded, manager
  `allow_negative` flags the event + raises attention.
- F3 full localStorage silently discarded queued work → quota failures
  are loud; the cart is never dropped unless the queue write persisted.
- F4 damage photos ballooned the ledger doc → photos ride the attention
  side-channel; ledger lines cap `photo_url` at a 2KB reference.
- F5/F14 count approval now posts adjustments first (idempotent) and
  records their txn ids on the session.
- F6 401/408/429 stay retryable in the outbox (session lapse ≠ rejection).
- F7/F8 blind sessions locked to assignee+managers for view AND writes.
- F9 import: one catalog write, ≤150-line chunked INITIAL_LOADs,
  content-hash uuids — full-retry idempotent (tested).
- F10 /health probe TTL-cached (`GVC_INVENTORY_PROBE_TTL`, default 300s).
- F11/F12 float paths removed (kit components server-side; cart merge
  client-side — `0.1+0.2 → "0.3"` locked by a node test).
- F13 count-record + auto custody-location creation now audit-logged.
- F15 retiring an asset requires `inventory_manage`.
- F17 online 5xx submits queue to the outbox instead of a dead error.
- F18 empty kit templates rejected on create AND edit.
- F16 + blind-audit information limits: accepted and documented
  (SECURITY.md "Known limitations").

## Deployment state

No new env vars, secrets, or Dockerfile changes; the feature rides the
existing image (packages COPYed whole; deps qrcode/jinja2/weasyprint were
already shipped). `/health` gains `inventory_store_ok` (real read,
TTL-cached) + `inventory_events`. Rollout gate: the tiles are invisible
until an admin grants `inventory*` features — deploying is safe before
onboarding starts (spec §22.6).

Post-merge live smoke (5 min, from `.claude/skills/inventory-release`):
`/health` shows `inventory_store_ok: true` → seed
(`python scripts/seed_inventory.py --yes`) → grant → post one transaction
on a phone → reverse it with a reason → `/ui/api/inventory/reconcile`
returns `consistent: true`.

## External blockers

None. No credentials, no new infrastructure, no third-party accounts.
