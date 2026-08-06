# Personal Hub Home

The tool grid is no longer the home screen. `/` is the signed-in person's hub;
tools live in a left rail (desktop) or drawer + bottom bar (phone).

**Files:** `web/hub.html` · hub shell CSS in `web/gvc.css` · `shared/hub_nav.py` ·
`orchestrators/hub_flow.py` · `GET /ui/api/hub`

Contract: hub handoff + `docs/GVC-COMMAND-STYLE.md`. Footer **r44**.

## Shell

| Viewport | Chrome |
|---|---|
| ≥1024px | Sticky 264px rail + scrolling main |
| &lt;768px | Hamburger drawer + Home / Search / Alerts / Tools dock |

Grants **dim** unreachable tools (em dash badge); they are not removed.

## Payload

`GET /ui/api/hub` returns one `HubPayload` (user, greeting, summary, needs ≤4,
exactly 4 metrics, queue, pinned, activity, badges, nav). Role =
`owner > gm > office > field` from grants. Live Monday/billing/morning enrichment
is best-effort — failures never blank the page.

## Still open (handoff steps 5–6)

- Pinned write path (per-user store)
- Admin: per-user `home_tool` override (person field reserved)
- Deeper needs queries (approvals, AR aging) as adapters land
