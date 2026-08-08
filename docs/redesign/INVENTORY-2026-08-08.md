# Portal redesign inventory — 2026-08-08

Step 1 of `Cursor Prompt.md`. **No page conversion started** — waiting for review.

Handoff pack lives in `docs/redesign/`. Stylesheets also copied to `web/` and served at:

- `/ui/gvc-ui.css`
- `/ui/gvc-v2-patch.css`

Existing live system remains `/ui/gvc.css` (every page still links only this).

## Critical scope note

| System | Where | In this repo? |
|---|---|---|
| **GVC Portal** (hub, estimate, billing, job check, …) | `portal.greenvalleycontractors.com` / this repo | **Yes** |
| **GVC Takeoff v2** (measure → review PWA) | `gvctakeoff.netlify.app/v2.html` | **No** — separate Netlify app; portal only has bridge `web/takeoff.html` + estimate import |

The takeoff screenshot audit in `GVC Takeoff v2 Fixes.md` targets the **external** app. This inventory covers the **portal**.

## Competing stylesheets (do not layer blindly)

| File | Role | Hex count | Gradients |
|---|---|---|---|
| `web/gvc-ui.css` | Redesign SoT (new) | 52 (tokens) | 0 |
| `web/gvc-v2-patch.css` | Temporary corrective layer | 10 | 3 |
| `web/gvc.css` | Current portal system (live) | 180 | 3 |

Class collision: both define `.btn`, `.chip`, `.chipset`. Redesign adds `.app` / `.rail` / `.topbar`. Portal today uses `.gvc-topbar` / `.hub-rail` / `.gvc-shell`.

## Pages with page-local `<style>` (visual props)

| Page | ~Style lines | Hex in page CSS |
|---|---|---|
| fieldguide.html | 522 | 73 |
| morning.html | 169 | 8 |
| jobcheck.html | 136 | 9 |
| activity.html | 69 | 4 |
| admin.html | 65 | 4 |
| billing.html | 66 | 4 |
| training.html | 65 | 10 |
| morning-owner.html | 42 | 2 |
| lien.html | 39 | 8 |
| jobstart.html | 32 | 1 |
| check.html | 22 | 1 |
| morning-gm.html | 15 | 0 |
| timeoff.html | 14 | 1 |
| invoice.html | 7 | 0 |
| takeoff.html | 5 | 0 |
| estimate / change-order / coi / hub | 0 (after recent extracts) | — |

## `<select>` inventory (must become `.chipset`)

| Page | Control | Options ≈ |
|---|---|---|
| activity.html | `#f-result` | 6 |
| activity.html | `#f-range` | 5 |
| estimate.html | `#salesperson-select` | 1 |
| estimate.html | `delivery_method` | 3 |
| estimate.html | `project_type` | 2 |
| invoice.html | `delivery_method` | 3 |
| invoice.html | `invoice_type` | 3 |
| invoice.html | `retainage_scope` | 2 |
| morning.html | `#arCat` (×2 in DOM) | 0–1 |

## Button treatments (counts)

| Pattern | Count |
|---|---|
| Field Guide `doclink` | 324 |
| `<button>` with **no class** | 78 |
| Field Guide `tile` | 64 |
| `gvc-signout` | 18 |
| `btn secondary` (+ small) | 32 |
| bare `btn` / `btn gold` / `btn danger` | 24 |
| `gvc-btn` | 4 |
| hub dock / rail / misc | scattered |

Green primary + gold hairline (redesign rule) is **not** consistently applied; money pages still use green fill `.btn` + separate `.btn.gold` for commit.

## Screen readiness (heuristic)

| Page | Back | Empty | Loading | Error | Notes |
|---|---|---|---|---|---|
| hub.html | Y (home) | Y | Y | Y | Custom rail, not redesign `.app/.rail` |
| takeoff.html | Y | **N** | **N** | **N** | Bridge only; primary is external Open Takeoff |
| estimate.html | Y | Y | Y | Y | 3 `<select>`s |
| invoice.html | Y | Y | Y | Y | 3 `<select>`s |
| billing.html | Y | Y | Y | Y | |
| jobcheck.html | Y | Y | Y | Y | Status picker handoff applies |
| jobstart.html | Y | Y | Y | Y | |
| morning*.html | Y | partial | partial | partial | GM/Owner weak on empty/loading |
| lien.html | Y | **N** | Y | Y | |
| timeoff.html | Y | **N** | **N** | **N** | |
| training.html | Y | Y | Y | **N** | |
| fieldguide.html | Y | Y | Y | Y | Private palette fork; `alert()` ×1 |
| activity.html | Y | Y | Y | weak | 2 `<select>`s |

## Stub / dead ends found

| Page | Issue |
|---|---|
| hub.html | `href="#"` ×2 |
| morning.html | `href="#"` ×1 |
| fieldguide.html | `alert()` ×1 |
| takeoff.html | No empty/loading/error (static bridge — convert to reference handoff card) |

## Linked stylesheets today

- **`takeoff.html`**, **`hub.html`**: `/ui/gvc-ui.css` only
- Remaining portal HTML: `/ui/gvc.css` only (none load `gvc-v2-patch.css` yet)

## Forms pack (2026-08-08, second drop)

New: `Cursor Prompt - Forms.md`, `START HERE.md`, `gvc-forms-reference.html`,
`gvc-forms.css` (served at `/ui/gvc-forms.css`, not linked on pages yet).
**Audit:** `FORMS-AUDIT-2026-08-08.md` — conversion blocked until Jordan signs off.

## Proposed conversion order (portal only)

1. ~~**`takeoff.html` bridge**~~ **Done** — redesign shell + handoff + honest empty queue; `GvcFlow` mounts `.path`
2. ~~**Shell on hub**~~ **Done (r101)** — `.app/.rail/.topbar/.tabbar`; single rail drawer; needs/metrics/queue on redesign cards
3. **Shared forms chrome** — one `.gvc-topbar` + nested `.gvc-path` (56px / 32px) for every generator
4. **Invoice → Estimate → Change Order** — copy `gvc-forms-reference.html` stages; kill selects + helpers + `gvc.css` moneyform
5. **Job Check** status picker — `Status Picker Handoff.md`
6. Remaining money spine → morning → fieldguide last (private palette)

## Blocked / not in this repo

- Full Takeoffs list / Measuring / Review screens on `gvctakeoff.netlify.app` — need the Takeoff deploy repo (or a cloud agent with that workspace).
- Data cleanup (test takeoffs, missing ZIPs) — surface with `.tag-gold` / `.rowcard-junk` when converting that app; list for Jordan.
