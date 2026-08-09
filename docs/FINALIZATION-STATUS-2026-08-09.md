# GVC Company Portal — Finalization Status Report

**Date:** August 9, 2026
**App:** portal.greenvalleycontractors.com (Cloud Run service `gvc-invoice`, project `gvc-invoice-system`)
**Live revision at time of writing:** `gvc-invoice-00225-sxg` (r107), deployed via CI, all health probes green
**Test suite:** 582 passing, run in CI on every PR before merge

This report responds to the "Company Portal Finalization" brief, phase by phase. The headline: **the audit was run in full, and the portal is materially further along than the brief assumes.** Several phases the brief budgets significant work for were already complete before this run started; the run's value was three real defects the audit surfaced, all now fixed, tested, and deployed.

---

## What this application actually is (Phase 0 — Orient)

The brief says "do not assume — read the code." Read in full. The stack is not what a generic web-app checklist expects:

| Layer | Reality |
|---|---|
| Backend | **Python / FastAPI** (`app/service.py`), uvicorn, on Google Cloud Run |
| Frontend | **Server-rendered static HTML** (19 pages in `web/`), vanilla JS. No React, no framework, no bundler, no npm dependency tree, no build step |
| Styling | One design system: `web/gvc-ui.css` (tokens as CSS custom properties) + `web/gvc-forms.css` for the three money forms |
| Data | **Monday.com is the source of truth** for jobs/bids/invoicing state. GCS for portal state (grants, drafts, pins), Google Drive for documents, Stripe for invoices, Gmail for outbound drafts, Cloud Logging for the audit trail |
| Auth | Google OAuth restricted to the company domain → signed session cookie. Per-feature grants stored in GCS, managed in an in-app admin screen |
| CI/CD | GitHub Actions: every PR runs compileall + the full pytest suite; merges to master auto-deploy to Cloud Run |

Architecture is layered and enforced: `app → orchestrators → subsystems/adapters → shared`, one flow module per business operation (estimate, invoice, change order, paid-by-check, COI, job start, job check, lien watch, morning brief, field guide).

**Deliverable:** `docs/APP_MAP.md` — stack, all 21 page routes mapped to their server-side guard, the 5 roles, the 7 admin presets, and each role's daily task path. Committed to the repo.

### Open loops (the brief expected many)

The entire codebase contains **exactly one TODO** (`orchestrators/invoice_flow.py:600`, a deliberately deferred Monday-automation hook). Zero FIXMEs, zero dead routes, zero commented-out feature corpses, zero placeholder text in production paths. The reason: this repo has already absorbed several finalization-style passes, documented in-repo (`docs/UI-FLOW-AUDIT.md`, `docs/UI-AUDIT.md`, `docs/UI-SYSTEM.md`, `docs/UX-CHECKLIST.md`) — a flow/dead-end audit dated Aug 8 scores every page on "can the user continue, go back, save, recover," with 14 tracked dead-ends closed.

## Phase 1 — Close open loops

Nothing material to close (see above). The one TODO is a documented future hook, not an unfinished flow.

## Phase 2 — Role & permission integrity

**Audited every route in the app against its server-side guard.** Result:

- **21 page routes:** every one enforces a feature grant server-side (`require_feature`) except `/` (the hub — correctly signed-in-only, contents shaped per-user server-side) and the font file route. Hiding a tile is presentation; the route is the gate.
- **96 `/ui/api/*` JSON routes:** 92 feature-gated; the 4 exceptions are hub endpoints whose payloads are derived from the *caller's own* grants server-side — correct by design.
- **12 `/v1/*` machine routes:** X-API-Key gated.
- Roles: 5 (`owner`, `gm`, `office`, `sales`, `field`), resolved server-side from grants. Each lands on a role-specific home with their queue first (Owner → Exceptions, GM → Huddle queue, Office → Billing queue, Sales → Bids & handoffs, Field → today's route). Top daily actions are 1–2 taps from home for every role.

**One real defect found and fixed:** the `ops` admin preset (field project managers — holds `morning_ops` + `jobcheck` + `jobstart`) resolved to the *sales* role because the role matcher accepted `jobstart` as a sales signal. Result: ops users were sent home to `/ui/estimate`, a page their grants cannot open, which redirected them straight back to sign-in. The matcher now requires an actual bidding grant (`estimate` or `takeoff`) for sales; ops correctly lands on the field home. **A new invariant test (`tests/test_role_home_reachable.py`) asserts that every preset's home screen is a page that preset can actually open** — locking out the whole class of bug, not just this instance.

## Phase 3 — Consistency

Already done before this run, and verifiably so: the r97–r104 wave (all dated Aug 8, all in git history) converted every page onto the single `gvc-ui.css` design system — tokens, one type scale, shared components (topbar, rail, cards, chips, action bars), zero `<select>` elements (replaced by a shared chip component), shared toast, theme-aware light/dark. A CI test (`test_no_portal_html_links_legacy_gvc_css`) fails the build if any page links the legacy stylesheet. Empty/loading/error states were the subject of the flow audit above; error envelopes are standardized (`{code, detail, advice}`) with the advice line written for the person reading it.

## Phase 4 — Mobile (primary use case)

The app was designed field-first (48px tap targets predate this run; `--tap: 44px` is a token). The audit still found **two real mobile defects**, both now fixed and deployed:

1. **iOS input auto-zoom — every form, every phone.** Inputs rendered at 14–15px. iOS Safari zooms the whole page whenever a focused input is under 16px, so every tap into a field on a job site yanked the layout sideways. Inputs are now pinned to 16px inside a phone-only media query in both stylesheets; desktop rendering unchanged. This was the brief's "fonts break on phones" report, root-caused.
2. **Safe-area insets silently off on the money forms.** The CSS uses `env(safe-area-inset-*)`, but four pages (estimate, invoice, change-order, takeoff) lacked `viewport-fit=cover` in their viewport meta — without which those insets do nothing. On notched phones the sticky action bars clipped on exactly the forms where money gets committed. All 19 pages now carry the correct viewport meta.

**A new baseline test (`tests/test_mobile_baseline.py`) enforces this permanently:** every page must have the safe-area viewport meta; input font-size must be 16px inside a phone media query; the tap-target token must be ≥ 44px. A regression now fails CI before it can deploy.

Screenshot evidence across the four viewports per role (`docs/screenshots/`) is **not done** — it requires authenticated sessions per role and a Playwright harness this repo doesn't yet carry. Listed under "remaining" below rather than claimed.

## Phase 5 — Stability & release readiness

- **Build/deploy:** no build step to fail; deploy is containerized and CI-driven. Every PR must pass compileall + 582 tests before it can merge; master auto-deploys. Deployments are one-click reversible via Cloud Run revisions.
- **Tests:** 582 passing, including auth/access suites, per-role hub payload shaping, permission invariants, and the two new suites from this run.
- **Security hygiene:** secrets live in GCP Secret Manager (never in the repo — `.gcloudignore` excludes local credential files); sessions are signed cookies with TTL; all mutations authorized server-side; `/health` live-probes every integration (Gmail, Slack, Monday, Stripe, Drive, grants store) rather than checking config presence — a lesson this codebase learned the hard way and now enforces.
- **Performance:** hub first paint is server-injected with zero API calls; heavy data hydrates after paint; as of r106 the hub also replays the last known numbers (display data only, never cached grants) with an explicit "as of «time» · refreshing…" stamp, so a slow Monday walk no longer leaves the user staring at an empty screen. Monday calls are cached (in-process + GCS snapshot, stale-while-revalidate) and instrumented (`GVC_MONday_TRACE=1` reports call count/latency per request).

## Phase 6 — Feature installation process

Partially in place already: `AGENTS.md` (how to add a module, layering rules), `docs/UI-SYSTEM.md` (design-system rules), `docs/GVC-COMMAND-STYLE.md` (component conventions), and a repo-root `CLAUDE.md` working memory that every session reads. The consolidated `docs/DESIGN_SYSTEM.md` + `docs/FEATURE_PROCESS.md` pair the brief asks for has **not** been written yet — the raw material exists across the docs above; consolidation is a half-day writing task, listed below.

## Phase 7 — Remaining work (honest list)

1. **Per-role viewport screenshots** (Playwright harness + role test accounts) — the evidence artifact for Phase 4. Effort: M.
2. **`docs/DESIGN_SYSTEM.md` + `docs/FEATURE_PROCESS.md`** consolidation from the existing scattered docs. Effort: S.
3. **Per-role live walkthrough** of each core flow end-to-end (the audit verified guards and payload shaping in code and tests; a human-in-the-loop pass per role is still worth an afternoon). Effort: S–M.
4. **Simulated slow-connection pass** (the app's states exist; verifying behavior on throttled 4G per screen hasn't been evidenced). Effort: S.

## Feature ideas surfaced during the audit (for the future roadmap)

- **AI scope-review reader** — extract the estimator's scope-review document into the Job Start packet (the current regex parser works but is brittle against format drift); groundwork and evaluation baseline already exist. (M)
- **Missing-scope detection at estimate time** — compare scope review + scope catalog vs. estimate line items; flag uncovered trades before the bid goes out. Blocked on three trade-scope definitions (FRP, Doors & Hardware, Tectum) that are still title-only stubs in the catalog. (M)
- **Grounded field-guide Q&A with citations** over the 60-procedure structured catalog that already ships in the repo. (M)
- **Fireflies transcript → Action Request extraction** — the config socket already exists in the morning subsystem, currently unwired. (M)
- Push notifications, offline-first checklists (decisions already locked in-repo), timesheet/clock-in, document library — all consistent with the existing architecture. (M–L each)

## App Store readiness (not built, as instructed)

Current state is a responsive web app behind Google OAuth. An iOS route would need: a wrapper decision (PWA install vs. Capacitor), push notification infrastructure, offline behavior for the field tools (the field-guide checklist design already specifies offline-first), an Apple developer account, and a billing decision (App Store subscription vs. web billing — material at the intended ~$300/month price point, given Apple's cut). None of this blocks the web product.

---

## Bottom line

The brief assumed an app full of open loops, inconsistent styling, and unenforced permissions. The audit found the opposite: **permissions are enforced server-side on every route, the design system is unified and CI-enforced, and the codebase carries one TODO.** What the audit *did* find — a role landing on a page it couldn't open, iOS zoom on every form field, and dead safe-area insets on the money forms — is exactly the kind of defect that only shows up by reading the code against real devices, and all three are fixed, regression-tested, merged through CI, and live in production as of r107.
