# Personal Hub Home

The tool grid is no longer the home screen. `/` is the signed-in person's hub;
tools live in a left rail (desktop) or drawer + bottom bar (phone).

**Files:** `web/hub.html` · hub shell CSS in `web/gvc.css` · `shared/hub_nav.py` ·
`orchestrators/hub_flow.py` · `subsystems/hub/pinned.py` · `GET /ui/api/hub` ·
`PUT /ui/api/hub/pinned`

Contract: hub handoff + `docs/GVC-COMMAND-STYLE.md`. Footer **r47**.

## Shell

| Viewport | Chrome |
|---|---|
| ≥1024px | Sticky 264px rail (brand + search + groups) + main header + scrolling body |
| &lt;768px | Hamburger drawer + Home / Search / Alerts / Tools dock |

Grants **dim** unreachable tools (em dash badge); they are not removed.
Active rail item on `/` is **Your hub**.

Loading shows a solid skeleton (no shimmer), then staggered rise-in.
Needs count chip + burgundy inset for Safety/Blocked. Home-tool CTA under the greeting.
Local `?demo=1` overlays sample needs for visual QA (dev bypass).

## Payload

`GET /ui/api/hub` returns one `HubPayload` (user with `homeTool` + `homeToolName`,
greeting, summary, needs ≤4 with `urgent`, exactly 4 metrics, queue rows with `id`,
pinned, activity, badges, nav).

- Metrics: **`0` when live source returned empty**; `—` only when the source failed.
- Invoice / send actions: **"Approve to send"** (never auto-send).
- Role = `owner > gm > office > field` from grants.
- `person.home_tool` (Admin) overrides the role default home tool.

## Pinned

`PUT /ui/api/hub/pinned` `{ items: [{ id, name, sub, href }] }` — max 20, GCS
`portal/hub-pins.json`, soft-fails without a state bucket. Queue rows have ★ pin.

## Still open

- Deeper needs queries (approvals aging, AR money-at-risk sort)
- Richer Activity feed shared with Owner Pulse as adapters land
