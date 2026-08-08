# Notes for AI coding agents

This is the **GVC internal portal** (invoicing started it; it now also does estimates, change orders, and paid-by-check, with more apps coming). An admin runs it; office staff use the web UI; Andrea reviews the resulting Gmail draft and clicks send. The big picture is in [README.md](README.md); the structure rationale is in [docs/portal-modularization-2026-06.md](docs/portal-modularization-2026-06.md) — this file is the agent-specific orientation.

## Standing agent workflow (Jordan — do not wait to be told again)

When work is multi-part or the user says "continue" / "what's next" / "fix X and
anything else obvious":

1. **Break it into small independent pieces** with clear acceptance criteria.
2. **Use multiple background/subagents in parallel** for research and
   implementation when that saves wall-clock time or tokens.
3. Coordinate in the foreground; merge, verify, commit, and open the PR yourself.
4. Prefer shipping several small high-value "portal owns this instead of Monday"
   gaps over one oversized redesign.

### Auto-merge (portal owns landing on `master`)

PRs from `cursor/*` → `master` (draft or ready) are squash-merged by
`.github/workflows/auto-merge-cursor-prs.yml` after `compileall` + `pytest`.
Drafts are marked ready automatically — Cursor often opens draft by default.
A 20-minute cron wakes any draft that never got a `ready_for_review` event.
Master then deploys via dispatched `deploy-cloud-run.yml`. Prefer
`draft: false` when opening, but do not babysit the ready toggle. To **pause**
auto-merge: add label `hold` or `do-not-merge` (draft alone does not pause).
Setup notes: `docs/ops/auto-merge-setup.md`.

### Performance (Monday-backed screens)

Static HTML is fast; live paint waits on Monday GraphQL. Before guessing
“need a DB,” run `scripts/measure_monday_paths.py` and read
`docs/ops/perf-audit.md`. Prefer skip unused work → parallelize → share
caches → defer warm. Set `GVC_MONDAY_TRACE=1` to attach `monday_trace` on
hub/billing JSON.

## Repository layout (package structure, 2026-06)

Code is layered. Imports flow **one direction**: `app → orchestrators → subsystems/adapters → shared`.

```
app/            FastAPI routes + auth + request models ONLY. Thin: parse request, delegate.
orchestrators/  one end-to-end flow per operation (invoice_flow, check_flow,
                estimate_flow, change_order_flow). Coordinates adapters + subsystems.
subsystems/     domain logic per area: invoice/ estimate/ change_order/ checks/.
                Validation, enrichment, formatting, rendering. No cross-system orchestration.
adapters/       one module per external system: stripe_invoice, drive, gmail, gcs,
                vision, slack_notify, monday/{client,co,estimate}. ALL outbound I/O.
shared/         paths, money, boards (Monday IDs), errors, access, auth,
                portal_store, activity. Bottom of the graph; imports nothing internal.
```

Each package's `__init__.py` states its contract. Read it before adding to that layer.

### Adding a new module or app (e.g. takeoff, material stocking)

1. New flow → `orchestrators/<app>_flow.py`. 2. Domain logic → `subsystems/<app>/`.
3. New external system → one module under `adapters/`. 4. Routes → thin handlers in
`app/service.py` that delegate to the flow. 5. Reuse `shared/` (don't re-declare board
IDs, paths, money, or the error envelope). Apps that cooperate do so **through an
orchestrator or a shared subsystem function — never by importing each other's internals.**

### Where things moved (vs the old flat layout)

`invoice.py` was split: Stripe → `adapters/stripe_invoice.py`, PDF → `subsystems/invoice/pdf.py`,
validate/enrich/env → `subsystems/invoice/model.py`, the `process_one` pipeline + `_run`/
`_run_correction` → `orchestrators/invoice_flow.py`. `service.py` → `app/service.py`.
Money helpers → `shared/money.py`; board IDs → `shared/boards.py`; error envelope →
`shared/errors.py`. **CLI entry is now `orchestrators/invoice_flow.py` (`main()`); the service
entry is `app.service:app`** (`uvicorn app.service:app`, what the Dockerfile/Cloud Run run).

## Happy path

```bash
./gvc invoice.py --input inputs/<identifier>.json          # live: Stripe + PDF + Drive + Gmail draft
./gvc invoice.py --input inputs/<identifier>.json --dry-run    # no Stripe, placeholder URL
./gvc invoice.py --input inputs/<identifier>.json --preflight  # read-only Stripe lookup, no writes
```

The `./gvc` wrapper is mandatory — it sets `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` so WeasyPrint can load Pango/Cairo, and points at the venv at `~/.venv/` (NOT `.venv/` inside the repo).

If you must invoke Python directly (e.g. one-off heredoc), use:

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib ~/.venv/bin/python ...
```

And note: `load_dotenv()` with no args fails on Python 3.14 from heredocs (frame-walk assertion). Pass the path explicitly: `load_dotenv("/path/to/gvc_invoice/.env")`.

## Locked architecture — do not change without explicit ask

These rules are deliberate, not accidental:

0. **Morning Brief never posts to Slack `#operations`.** Field brief, huddle
   summary, route sheet, prep status — always a private side chat / DM, never a
   channel broadcast. Jordan 2026-08-05. Do not "temporarily" post there for
   review. See `docs/MORNING_BRIEF_BUILD_SPEC.md` §Slack.
1. **Never call `stripe.Invoice.send_invoice()`**. Stripe never emails the customer. The Gmail draft is the only customer-facing send, and Andrea triggers it manually.
2. **Never auto-send Gmail**. `gmail.py` creates drafts only. The locked architecture lives or dies by Andrea's review step.
3. **Never modify the `gvc_inv_v3_` idempotency prefix** without bumping it (`v3_` → `v4_`) and documenting the reason in [docs/payment-terms.md](docs/payment-terms.md). The bump exists to make corrected invoices not collide with voided ones.
4. **Drive uploads, Gmail drafts, Monday writeback, and AIA export are each graceful — they print a skip message and continue if their integration isn't configured.** Don't make any of them hard-required.
5. **Retainage NEVER goes to Stripe as its own line item.** Stripe sees only the net due. Per-line allocation lives in `create_stripe_invoice()`; honor the `retainage.scope` field (`"base"` = first non-CO line eats it; `"all"` = proportional across all lines). The GVC PDF is the only place retainage is visible as a separate row.
6. **The CO Template PDF is NEVER an invoice.** The disclaimer banner on page 1 ("Informational — Not an Invoice") and the cross-reference to the parent invoice identifier are load-bearing. Don't remove them. The CO Template carries no Pay Now button, no hosted URL, no Stripe ID.
7. **Stripe invoice `description` follows the convention** `"{job} — Progress Bill #N"` / `"… — Final Bill"` / `"… — {identifier}"`, with a trailing `" plusTM"` when any line item is `kind: "co"` / `"tm"`. Customers see this in the Stripe portal — keep it human-readable.

## Naming conventions (set 2026-05-19)

- **G703 tab in the AIA workbook**: `"{N} - G703 - Schedule of Values"` where N is the pay-app number. First period = `"1 - G703 - Schedule of Values"`. If the JSON declares `pay_app_number`, the pipeline derives `g703_sheet_name` automatically — only set it explicitly to override.
- **G702 tab**: no convention yet; varies per workbook. Declare `g702_sheet_name` explicitly in the JSON.
- **CO tracking number**: `{invoice-id}-CO-{NN}`. Example: `GVC-2026-C-005-CO-01`. Scoped per invoice. Appears on the CO Template PDF, in the invoice's CO line description (`line_items[].co_number`), and is the cross-reference customers / Hunt / Chamber use to confirm they're looking at the right doc.

## Input JSON conventions

- File lives at `inputs/<identifier>-<slug>.json`. Identifier follows `GVC-{year}-{series}-{NNN}` where series is `C` (commercial) or `MV` (residential / "make visit"). Use `./gvc scripts/next-invoice-number.py --series C` to get the next free number in a series.
- Schema documented in [README.md](README.md#input-json-shape) and shown in [example_input.json](example_input.json).
- For Net 30, **omit `due_date`** — it auto-computes to `issue_date + 30 days`. Only set `due_date` for non-30-day terms.
- Combine multiple change orders into a single line item per the customer's billing instructions — see [inputs/GVC-2026-C-002-300-high-st-gcc.json](inputs/GVC-2026-C-002-300-high-st-gcc.json) for the pattern (description "Change Orders CO#1 + CO#2", `detail` field carrying both breakdowns).
- Optional `job.drive_invoice_folder_id` (or its alias `drive_source_folder_id`) — the ID of the job's `Invoice/` folder in the new directory structure (`Projects/<year>/<Residential|Commercial>/<customer>/<[Number Street], [City], [ST] [ZIP] | [Builder/Client]>/Invoice/`). When set on a live run, the service creates (or reuses) a dated subfolder `Completed Invoices <YYYY-MM-DD>/` inside that folder and uploads the invoice PDF + G702/G703 PDFs + CO Template PDFs + `{identifier} - made and sent.txt` sentinel into the dated subfolder. Source materials at the Invoice/ root (billing instructions, AIA xlsx, approval photos) stay untouched. Multiple progress bills on the same job land in their own dated subfolders so the audit trail stays visually distinct. UTC date is used so the folder name is unambiguous across timezones. When `drive_invoice_folder_id` is unset, drive.py falls back to the legacy `Invoices/<year>/<customer>/` tree for the invoice PDF only (no AIA/CO writeback, no dated subfolder).
- Optional `invoice.email_context` — surfaces a line of context (e.g. "Final draw minus touch-up; pad install Tuesday") into the top of the Gmail draft body so Andrea doesn't have to context-switch back to the billing doc.

## Don't touch

- The Stripe idempotency prefix (see locked-architecture #3).
- The existing input files in `inputs/` — real customer data, no PII edits.
- `output/` and `logs/` — gitignored runtime artifacts.
- `templates/invoice.html.j2` and `templates/change_order.html.j2` for cosmetic tweaks — the customer-approved layouts are stable. Change only with explicit ask.

## Drive permissions footgun

Folders that live in the former GM's personal Drive are NOT visible to the service account by default — only the GVC Shared Drive is. When a job's `drive_invoice_folder_id` (or its older alias `drive_source_folder_id`) points to a personal-Drive folder, the sentinel + invoice PDF + AIA + CO writebacks will 404 (gracefully — they're non-fatal) until that folder is shared with `gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com` as Editor. Folders inside the GVC Shared Drive don't need additional sharing. The core invoice flow (Stripe, GVC PDF generation, Gmail draft) succeeds regardless of Drive writeback status. If you see the 404 in stderr, tell an admin so they can share the folder.

## When the user asks for "a new invoice"

1. Read the source: a Drive folder link, a Monday item ID, or a billing-instructions document.
2. Cross-check every field against an existing `inputs/<identifier>-*.json` if one already exists for the job — verify customer name, email, amounts, change-order math against the billing doc before doing anything Stripe-side.
3. If anything contradicts (e.g. typo in an email address), ask before running live. Stripe operations are idempotent on identifier but not free to undo at the customer-experience level — the Gmail draft and Drive copy both carry whatever you ship.
4. Run live (`./gvc invoice.py --input ...`) and verify all four side effects (PDF, Stripe, Drive, Gmail draft) end-to-end before reporting done.

### Commercial progress billing (C-series)

When the source folder contains an AIA workbook (.xlsx) or pre-made G702/G703 PDFs:

1. **Read the billing instructions doc first.** It usually lives in the same Drive folder and has the canonical numbers (contract value, retainage %, T&M total, recipient email, billing rules). Cross-check every number against the Excel before authoring the JSON.
2. **Confirm retainage scope.** Some jobs apply retainage only to base contract (`scope: "base"`), some to all progress billings inclusive of T&M (`scope: "all"`). The billing rules will say. If they're ambiguous, ask before running.
3. **Author the input JSON by hand for now.** Place per-job source assets (xlsx, TM approval images) under `inputs/<identifier>/` and reference them from the JSON. The JSON itself lives at `inputs/<identifier>-<slug>.json` per the existing convention.
4. **For T&M on the AIA:** if the billing instructions say "this will be billed as an additional line on the AIA," the customer expects the AIA itself to include the T&M row. Have an admin add the row to the G703 tab (`"{N} - G703 - Schedule of Values"`) before exporting — don't ship a CO that's only on our invoice but missing from the G702/G703.
5. **Always dry-run before live.** The CO Template + invoice PDF both render in dry-run; eyeball them and surface to Jordan for sign-off before preflight/live.

## Cursor Cloud specific instructions

This section is for Cloud Agents. The VM boots from a snapshot with WeasyPrint's
system libraries (Pango/Cairo/GDK-Pixbuf/fonts, per the `Dockerfile`) already
installed, and the startup update script refreshes Python deps into a repo-local
venv at `/workspace/.venv`. Python is **3.12** (matches the Dockerfile; ignore the
macOS 3.14 quirks noted above — those are for the local Mac dev box, not this VM).

- **Use the venv.** Dependencies live in `/workspace/.venv`, NOT the macOS `~/.venv`
  the `./gvc` wrapper hardcodes. The `./gvc` wrapper is macOS/Homebrew-only — do not
  use it here. Run tools with `. .venv/bin/activate` first, or call `.venv/bin/python`
  / `.venv/bin/pytest` / `.venv/bin/uvicorn` directly. System `pip` is
  externally-managed (PEP 668) and will refuse installs — always go through the venv.
- **Run the app (the only long-running service):**
  `GVC_UI_DEV_BYPASS=1 GVC_PORTAL_ALLOWED_EMAILS=dev-bypass@localhost uvicorn app.service:app --port 8080`
  (start it from the repo root so `app.service` imports; `PYTHONPATH` = repo root).
  There are no databases, brokers, or worker processes — the FastAPI app is
  everything. Scheduled jobs are just external Cloud Scheduler POSTs to `/v1/tasks/*`.
- **`GVC_UI_DEV_BYPASS=1` is mandatory for local `/ui/*` access** — without it every
  browser page redirects to Google sign-in. The bypass user is `dev-bypass@localhost`.
- **Non-obvious gotcha:** dev-bypass alone still 303-redirects the tool pages
  (`/ui/estimate`, `/ui/invoice`, …) back to the hub, because they are feature-gated.
  Add `GVC_PORTAL_ALLOWED_EMAILS=dev-bypass@localhost` to make that user a break-glass
  superadmin (grants all features); only then do the tool pages return 200.
- **Everything external degrades gracefully.** Stripe, Monday, Drive, Gmail, GCS,
  Vision, and Slack are all unconfigured on this VM; the app boots, `/health` returns
  `ok: true`, and startup only prints "missing config" warnings. No secrets are needed
  to run, load the UI, or render PDFs.
- **Exercise core functionality without any external service** via the dry-run PDF
  path — it renders through WeasyPrint with no Stripe/Monday. Set
  `GVC_SERVICE_API_KEY=<key>` and POST to `/v1/estimate/from-json` (or
  `/v1/invoice/from-json`) with `{"data": {...}, "mode": "dry-run"}`; the PDF lands in
  `/workspace/output/`. Leave `estimate.identifier` blank so it auto-assigns
  (`YYYY-MMDD-NNN`); a legacy id like `EST-...` is rejected as INVALID_INPUT.
- **Tests + compile:** `.venv/bin/pytest -q` (the `tests/` suite; also runnable as
  standalone scripts, e.g. `python tests/test_lien_watch.py`) and
  `python -m compileall app orchestrators subsystems adapters shared scripts`. The
  Dockerfile's build-time smoke test is `python -c "import app.service"`.

- **Monday durable snapshots (speed):** List data for Job Start / Morning Brief /
  Job Check is cached in-process (L1) **and** as JSON objects in GCS under
  `monday-cache/` (L2, same bucket as portal state / preview). Cold Cloud Run
  instances hydrate from GCS instead of waiting on Monday. `POST /v1/tasks/warm-monday`
  (API key) force-refreshes Monday → L1 + L2 — wire Cloud Scheduler every **2–5
  minutes** in production or cold opens will eventually miss after
  `GVC_MONDAY_SNAPSHOT_MAX_AGE` (default 2h). Hub also fires
  `POST /ui/api/monday/warm` on load.
- **Morning Brief Slack:** never post Morning Brief / field brief / huddle
  content to `#operations`. Side chat / DM only (Jordan 2026-08-05).
- **Portal UI system:** `docs/UI-SYSTEM.md` + `docs/UX-CHECKLIST.md` are the
  product-system contract (shells, actions, dead-end rules). Visual tokens stay
  in `docs/GVC-COMMAND-STYLE.md` / `web/gvc.css`. Dark mode / contrast rules:
  `docs/UI-DARK-MODE.md` (semantic `--color-*-ink`, `--color-input-bg`,
  `.gvc-msg--*`; no light-only hex in page CSS/JS). Before shipping UI changes,
  run `python scripts/ui_consistency_check.py` and complete the UX checklist.
  Prefer system fixes in `gvc.css` over new page-local dialects. Toggle theme
  via topbar (`web/gvc-theme.js` → `data-theme`); verify light **and** dark.
- **Hub first paint:** HTML injects `HUB_BOOT_JSON` (rail + greeting + quick
  actions — no Monday). Live Needs/Metrics wait on `GET /ui/api/hub`; Activity
  is deferred to `GET /ui/api/hub/activity`. See `docs/HUB-HOME.md`.

