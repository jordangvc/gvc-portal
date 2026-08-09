# App Map — routes, roles, and who sees what

**Generated 2026-08-09** from `app/service.py`, `shared/access.py`, `shared/hub_nav.py`.
Regenerate whenever a route, feature, or preset changes — `docs/FEATURE_PROCESS.md`
makes updating this file part of definition-of-done.

## Stack (read from the code, not assumed)

| Layer | What it actually is |
|---|---|
| Backend | **FastAPI** (`app/service.py`), served by uvicorn |
| Frontend | **Server-rendered static HTML** in `web/`, vanilla JS, no framework, no bundler, no build step |
| Styling | **`web/gvc-ui.css`** (design system) + `web/gvc-forms.css` (money forms). CSS custom properties as tokens. No Tailwind |
| State | None client-side beyond `localStorage`; the server composes one payload per screen |
| Data | **Monday.com is the source of truth.** GCS for portal state (grants, drafts, pins), Drive for documents, Stripe for invoices, Cloud Logging for the audit trail |
| Auth | Google OAuth (in-app, `hd`-restricted) → signed session cookie. Grants in `portal/grants.json` (GCS) |
| Deploy | Cloud Run `gvc-invoice`; GitHub Actions auto-deploys `master` |
| Tests | pytest, 580+, gate runs on every PR |

There is **no JS build, no bundle, and no npm dependency tree** — a fact that
makes several standard "web app finalization" steps inapplicable here.

## Roles

Five roles, resolved server-side in `hub_nav.resolve_role()` from the user's
effective grants — first match wins:

| Role | Resolved by | Home | Queue heading |
|---|---|---|---|
| `owner` | `morning_owner` | `/ui/morning-owner` | Exceptions |
| `gm` | `morning_gm` | `/ui/morning-gm` | Huddle queue |
| `office` | `invoice` or `coi` | `/ui/billing` | Billing queue |
| `sales` | `estimate` or `takeoff` | `/ui/estimate` | Bids & handoffs |
| `field` | *(default)* | `/ui/morning` | Your route today |

`admin` alone does **not** make someone an owner — deliberate, so admins without
Owner Pulse don't land on the exceptions shell.

## Admin presets → what each person actually gets

`/ui/admin` grants by preset (`access.ROLE_PRESETS`). BASELINE
(`timeoff`, `fieldguide`, `morning`, `training`) is added to everyone.

| Preset | Who | Role | Home | Pages |
|---|---|---|---|---|
| `full` (`*`) | Jordan, Andrea | owner | `/ui/morning-owner` | 19 (all) |
| `owner` | ownership | owner | `/ui/morning-owner` | 14 |
| `gm` | GM | gm | `/ui/morning-gm` | 11 |
| `sales` | Jake | sales | `/ui/estimate` | 9 |
| `ops` | Mark, Robert (field PMs) | **field** | `/ui/morning` | 7 |
| `crew` | Ethan, crew | field | `/ui/morning` | 6 |
| `office` | office billing | office | `/ui/billing` | 11 |

> **Fixed 2026-08-09:** `ops` previously resolved to `sales` (because it holds
> `jobstart`) and was sent home to `/ui/estimate` — a page ops has no grant for,
> so the home screen redirected to sign-in. Sales now requires `estimate` or
> `takeoff`. Locked by `tests/test_role_home_reachable.py`, which asserts every
> preset's home is a page that preset can open.

## Screens → required grant

Every page enforces its grant **server-side** in `app/service.py`. Hiding a tile
is presentation only; the route is the gate.

| Route | Grant | Purpose |
|---|---|---|
| `/` | signed-in | Hub — role-shaped home |
| `/ui/morning` | `morning` | Daily brief / route (baseline: everyone) |
| `/ui/morning-gm` · `/ui/morning/gm` | `morning_gm` | GM huddle |
| `/ui/morning-owner` · `/ui/morning/owner` | `morning_owner` | Owner Pulse |
| `/ui/billing` | `invoice` | Billing Hub — Ready-to-Invoice queue |
| `/ui/invoice` | `invoice` | Invoice generator |
| `/ui/estimate` | `estimate` | Estimate generator |
| `/ui/change-order` | `change_order` | Change orders (implied by `estimate`) |
| `/ui/check` | `check` | Paid by Check (implied by `invoice`) |
| `/ui/coi` | `coi` | Certificates of insurance |
| `/ui/jobstart` | `jobstart` | Sales → Ops handoff packet |
| `/ui/jobcheck` | `jobcheck` | Field job status → Monday |
| `/ui/inventory` | `inventory_view` | Field inventory: drop off / pick up / transfer / count / scan (moves need `inventory`) |
| `/ui/inventory/admin` | `inventory_manage` | Inventory admin: items, assets, kits, locations, counts, imports, labels, attention |
| `/ui/takeoff` | `takeoff` | Takeoff app bridge |
| `/ui/lien` | `lien` | Lien deadline tracker (read-only) |
| `/ui/fieldguide` | `fieldguide` | Field Manual (baseline) |
| `/ui/training` | `training` | Training (baseline) |
| `/ui/timeoff` | `timeoff` | Time off (baseline) |
| `/ui/activity` | `activity` | Audit log (implied by `admin`) |
| `/ui/admin` | `admin` | Grants + templates |

## Permission posture (audited 2026-08-09)

- **21 page routes** — every one behind a server-side feature check except `/`
  (hub, correctly signed-in-only) and `/ui/fonts/{name}`.
- **96 `/ui/api/*` routes** — 92 behind a feature check. The 4 exceptions are
  all hub endpoints (`/ui/api/hub`, `/refresh`, `/activity`, `/pinned`), correct
  by design: the hub is everyone's home and its contents are derived from the
  caller's own grants server-side.
- **12 `/v1/*` routes** — machine surface, `X-API-Key`.
- No route relies on button-hiding for enforcement.

## Daily task per role (top actions, taps from home)

| Role | Their day | Taps |
|---|---|---|
| Owner | Exceptions → decide → Billing | 1–2 |
| GM | Huddle queue → Job Check → Job Start accepts | 1–2 |
| Office | Ready to Invoice → Invoice → Paid by Check | 1–2 |
| Sales | Bid → Estimate → Job Start handoff | 1–2 |
| Field | Route today → Job Check → Field Manual | 1–2 |

## Open loops

The codebase carries **one** TODO (`orchestrators/invoice_flow.py:600`, a
Monday-automation hook, correctly deferred). No FIXMEs, no dead routes, no
commented-out blocks of substance. Prior cleanup passes are documented in
`docs/UI-FLOW-AUDIT.md`, `docs/UI-AUDIT.md`, and `docs/UI-SYSTEM.md`.
