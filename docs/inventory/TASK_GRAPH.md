# Inventory — Task Graph and Ownership

Execution order, file ownership, acceptance criteria. Lead = this session.
Parallel implementation agents work ONLY in their owned files, in this
worktree, against the contracts committed in S1/S2.

| ID | Task | Owner | Depends | Owns (files) | Accept when | Verify |
|----|------|-------|---------|--------------|-------------|--------|
| S1 | Domain core: types, units, catalog, locations, ledger, invariants, idempotency, concurrency, counts, kits | lead | — | `subsystems/inventory/{__init__,model,units,store,ledger,catalog,locations,search,counts,kits}.py` | all 18 invariants unit-tested incl. CAS-conflict retry + duplicate-UUID + asset-single-location + kit no-double-count | `pytest tests/test_inventory_domain.py tests/test_inventory_ledger.py` |
| S2 | Flows + API + grants: orchestrator, routes, validation, authz, idempotent posting, QR resolve, attention | lead | S1 | `orchestrators/inventory_flow.py`, route block in `app/service.py` (append-only region), `shared/access.py` additions, `tests/test_inventory_api.py` | every mutation server-authorized; field user 403 on manager mutations; posting idempotent over HTTP; structured error envelope | `pytest tests/test_inventory_api.py` |
| S3 | Field UI: mobile home, drop-off/pick-up/transfer carts, scan, count, unknown item, damage, outbox | field agent (worktree, owned files only) | S2 | `web/inventory.html`, `web/gvc-inventory.js` | carts post one atomic transaction; location persists; outbox survives reload; every scan has manual fallback; 375px usable; node --check clean | `pytest tests/test_inventory_ui.py` + harness |
| S4 | Office UI: search/browse, item/asset/kit/location admin, imports, labels, reports, attention | office agent (worktree, owned files only) | S2 | `web/inventory-admin.html`, `templates/inventory_labels.html.j2` | CSV dry-run→commit as INITIAL_LOAD; merge preserves history; labels PDF renders; reports reconcile to ledger | `pytest tests/test_inventory_admin_ui.py` |
| S5 | Integration: nav tile, APP_MAP, screenshot-harness pages, seed script, health probe | lead | S3,S4 | `shared/hub_nav.py` (append), `docs/APP_MAP.md`, `scripts/screenshot_portal.py` (PAGE_FEATURE), `scripts/seed_inventory.py`, footer bump | all roles see correct tiles; harness green incl. inventory pages; seed idempotent | full local slice + harness run |
| S6 | Verification & release: fresh-context adversarial review, a11y pass, docs, evidence | verify agent (fresh context, read-only) + lead fixes | S5 | `docs/inventory/*` remaining docs | no unresolved high/medium finding; release gate green in CI | PR gate (compileall + full pytest) |

Shared-file rule: `app/service.py`, `shared/access.py`, `shared/hub_nav.py`,
`docs/APP_MAP.md` are edited by the LEAD ONLY. Agents never touch them.

Status log (updated as slices land — a slice is DONE only with test evidence)
- S1: DONE — 21 tests green (domain + ledger invariants)
- S2: DONE — 9 HTTP tests green (authz tiers, idempotency, counts, import, attention)
- S3: DONE — 8 tests green; outbox vm-tested (uuid stable, 409 parks, 200 drains)
- S4: DONE — 12 tests green; labels render 35 QRs across 2 sheets
- S5: DONE — nav, APP_MAP, health probe, seed, verify cmd, shared-JS route, r110
- S6: in progress (fresh-context review)
