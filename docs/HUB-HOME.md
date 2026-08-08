# Personal Hub Home

The tool grid is no longer the home screen. `/` is the signed-in person's hub;
tools live in a left rail (desktop) or drawer + bottom bar (phone).

**Files:** `web/hub.html` · hub shell CSS in `web/gvc.css` · `shared/hub_nav.py` ·
`orchestrators/hub_flow.py` · `subsystems/hub/pinned.py` · `GET /ui/api/hub` ·
`PUT /ui/api/hub/pinned`

Contract: hub handoff + `docs/GVC-COMMAND-STYLE.md`. Footer **r99**.

## Fast first paint (r90+)

The hub is still the personal home — not a separate marketing landing — but
**time-to-first-action no longer waits on Monday**:

1. HTML injects `HUB_BOOT_JSON` (grants → rail + greeting + home CTA + quick
   actions). Zero Monday / Cloud Logging.
2. Client paints that shell immediately (`paintInstantShell`).
3. `GET /ui/api/hub` hydrates Needs / Metrics / Queue (Monday parallel slice).
4. `GET /ui/api/hub/activity` fills Activity after paint (Cloud Logging).
5. Hub brief/GM paths skip GFolder attach **and** Open-Meteo weather.
6. Hub billing uses ``billing_hub_payload(for_hub=True)`` — Ready + Accepted
   only (no Projects board walk, no P5 worksheet enrich).
7. Dead Monday auth (`monday_ok` / `ok` false, `MONDAY_AUTH`) is treated as
   unreachable — never a gold "You're clear" over empty queues (r94).

## Shell

| Viewport | Chrome |
|---|---|
| ≥1024px | Sticky 264px rail (brand + search + groups) + main header + scrolling body |
| 768–1023 | Hamburger drawer + bottom dock (rail still hidden) |
| &lt;768px | Hamburger drawer + Home / Search / Alerts / Tools dock |

Grants **dim** unreachable tools (em dash badge); they are not removed.
Active rail item on `/` is **Your hub**.

Loading shows a solid skeleton (no shimmer), then staggered rise-in.
Needs count chip + burgundy inset for Safety/Blocked. Home-tool CTA under the
greeting (quiet outline when needs compete). Local `?demo=1` overlays sample
needs for visual QA (dev bypass).

## Roles (grant → home shape)

Priority: `owner > gm > office > sales > field`.

| Role | Grant trigger | Home tool | Needs focus |
|---|---|---|---|
| Owner | `morning_owner` only (not bare `admin`) | Owner Pulse | Safety, ready-to-invoice, prep alerts |
| GM | `morning_gm` | GM Morning Huddle | Sequence blocks, planning, open ARs |
| Office | `invoice` or `coi` | Billing Hub | Ready to invoice + handoffs |
| Sales | `estimate` / `takeoff` / `jobstart` (no invoice/coi) | Estimate | Handoffs first |
| Field | default | Morning Brief | Route stops, blocked jobs, asks |

Admin presets in `/ui/admin`: Full, Owner Pulse, GM huddle, Sales, Operations,
Crew, Office billing. Grant matrix: grant `morning_owner` to Jordan (and anyone
who should see Owner Pulse); `morning_gm` to the GM; Sales preset for Jake;
Office for Andrea when she should see Billing Hub (not Owner) unless she also
holds `morning_owner`.

- `person.home_tool` (Admin) overrides the role default home tool.
- Superadmins (`GVC_PORTAL_ALLOWED_EMAILS`) still expand to all features, so they
  resolve as **owner** via `morning_owner`.

## Payload

`GET /ui/api/hub` returns one `HubPayload` (user with `homeTool` + `homeToolName`,
greeting, summary, needs ≤4 with `urgent`, exactly 4 metrics, queue rows with `id`,
pinned, activity, badges, nav).

- Metrics: **`0` when live source returned empty**; `—` only when the source failed
  (`unavailable: true` — distinct from zero).
- Invoice / send actions: **"Approve to send"** (never auto-send).
- Needs sorted urgent-first, then oldest date; handoffs expand per accepted bid.
- Activity lines are humanized; hub/sign-in noise filtered.
- **`needs_clear` is honest:** unreachable Monday/billing/pulse/GM view never
  claims "You're clear." The UI shows "Waiting on live data" when the list is
  empty and `needs_clear` is false.
- Field incomplete prep badges Morning even when the route is quiet.

## Pinned

`PUT /ui/api/hub/pinned` `{ items: [{ id, name, sub, href }] }` — max 20, GCS
`portal/hub-pins.json`. Response includes `persisted: false` when the state
bucket is missing (in-session only; toast says so). Queue rows have ★ pin.

## Still open

- Richer Activity shared with Owner Pulse as adapters land
- AR money-at-risk sort when billing exposes due amounts
- Optional: cache-only `/hub/refresh` when L1/L2 Monday snapshots are fresh
- Field Guide leftovers: glossary/sources/index, cabinets (large HTML)
