# Inventory — Codebase Assessment (2026-08-09)

Findings from repository inspection before any inventory code was written.
This assessment drives every stack decision in `DECISIONS.md`.

## Stack (verified, not assumed)

| Concern | What this repo actually uses |
|---|---|
| Language / framework | Python 3.12, FastAPI (`app/service.py`, ~90 routes), uvicorn |
| Frontend | Static HTML pages in `web/`, vanilla JS, no build step. Design system `web/gvc-ui.css` (+ `gvc-forms.css` on money forms), shared JS components (`GvcChips`, `GvcFlow`, `GvcFormChrome/Stages`, `GvcFieldJump`) |
| Layering (enforced) | `app → orchestrators → subsystems/adapters → shared`, one-way imports |
| Database | **None.** Monday.com is business source of truth; portal state lives in GCS JSON objects (`gs://gvc-portal-state`, versioning ON, no lifecycle rules) with **generation-guarded optimistic concurrency** (`ifGenerationMatch`) — see `shared/portal_store.py`, `subsystems/morning/store.py` (`read_doc` / `write_doc` / `mutate`), `subsystems/estimate/drafts.py` |
| Auth | Google OAuth (Workspace-internal) → signed session cookie (`shared/auth.py`); per-feature grants in `shared/access.py` (GCS-backed store, `require_feature` / `require_admin` server-side on every route) |
| Multi-tenancy | Single organization. No org scoping exists anywhere |
| Files/images | Google Drive via service account (`adapters/drive.py`); GCS for previews/state |
| PDF | WeasyPrint + Jinja2 (`templates/*.j2`) — already produces invoices/estimates; suitable for QR label sheets |
| Notifications | Slack bot by channel ID (`adapters/slack_notify.py`), graceful-skip pattern; in-app attention = hub "Needs you" cards |
| Audit log | `shared/activity.py` → structured JSON to Cloud Logging (60-day retention + monthly Drive export) |
| Tests | pytest (585+ passing in CI); self-running test files; fakes over mocks; JS checked via `node --check` |
| CI/CD | GitHub Actions: `cursor/*` PR → compileall + full pytest gate → squash-merge → auto-deploy to Cloud Run (`gvc-invoice`, us-central1). Deploys reversible via revision traffic |
| E2E / evidence | `scripts/screenshot_portal.py` (Playwright, per-role × 4 viewports, throttled-4G mode) — dev-only deps in `requirements-dev.txt` |
| Mobile | Mobile-first is the house rule; CI-enforced baseline in `tests/test_mobile_baseline.py` (viewport meta + safe-area, ≥16px phone inputs, ≥44px taps) |
| Offline precedent | Field Manual: offline monolith + localStorage checklists w/ shared-GCS last-writer-wins; estimate drafts: localStorage working copy + shared GCS store. **No service worker exists anywhere** |
| Barcode/QR | None yet. No native app; camera use must be in-browser |

## Reusable capabilities (mapped to inventory needs)

- **Ledger storage**: the `mutate()` generation-guard pattern gives atomic
  read-modify-write per GCS object — events + balance projection can update in
  ONE compare-and-swap, satisfying invariants 6–7/17 at GVC's write volume
  (single org, tens of movements/day, a handful of concurrent users).
- **Idempotency**: client-UUID dedup inside the same guarded object (the
  Stripe-side precedent: `gvc_inv_v3_*` idempotency keys).
- **Roles**: extend `shared/access.py` FEATURES — no new auth system.
- **People/trucks/jobs as locations**: portal already models employees
  (grants store `people`), Monday Projects (job sites), and jobs via
  `adapters/monday/*`. Trucks exist only in the Takeoff app's Apps Script —
  reference by name/config, don't integrate in v1.
- **Search**: catalog scale is hundreds of items — in-memory token/alias
  scoring like `subsystems/fieldguide/search.py` beats adding Postgres.
- **Labels**: WeasyPrint template → printable QR sheet PDF (`qrcode[pil]` is
  ALREADY in requirements.txt — used for invoice pay-link QRs).
- **Notifications/attention**: hub needs-cards + Slack notify pattern.
- **Docs/screenshots**: extend `screenshot_portal.py` PAGE_FEATURE map.

## Constraints that shaped the design

1. **No relational DB and no credentials for one.** Adding Cloud SQL is new
   infrastructure + cost + secrets = the only true external blocker in the
   spec. GCS-ledger avoids it entirely and is the smallest change consistent
   with repo patterns (spec §1.2). Consequence: per-object write serialization
   (~1 write/s/object) — orders of magnitude above observed portal volume.
2. **Root `CLAUDE.md` is the repo's 2,500-line working memory** — a locked
   convention. The spec's "<200-line CLAUDE.md" yields to the repo rule;
   inventory rules go in `.claude/rules/inventory.md` + `docs/inventory/`.
3. **No build step** — the UI is hand-written HTML/JS on gvc-ui.css. A React
   PWA would be a second frontend stack; rejected.
4. **Concurrent writer**: an uncommitted "nonneg" feature (r109) is in flight
   in the main checkout touching `app/service.py` / `shared/hub_nav.py`.
   Inventory is built in a separate worktree; service.py/hub_nav edits are
   append-only regions to minimize merge conflict surface.
5. **Locked architecture list** (AGENTS.md) — none of it is affected; no
   Stripe/Gmail/Monday writes exist in inventory v1.

## Integration points inventory will touch

- `shared/access.py` — new features: `inventory`, `inventory_manage`,
  `inventory_view` (auditor) + role-preset updates.
- `shared/hub_nav.py` — nav tile (append-only edit; conflict-aware).
- `app/service.py` — new route block (append-only region at end of file).
- `shared/boards.py` — no Monday writes v1; job-site locations reference
  Monday project ids read-only (optional enrich later).
- `tests/` — new test files only.
- `scripts/screenshot_portal.py` — PAGE_FEATURE additions land with the UI.
