# GVC Portal Design System

**Canonical entry point.** Read this (and `docs/FEATURE_PROCESS.md`) before
writing any UI code. Consolidated 2026-08-09 from `UI-SYSTEM.md`,
`GVC-COMMAND-STYLE.md`, `UX-CHECKLIST.md`, and `AGENTS.md` — those remain as
history/detail; **when they disagree with this file, this file wins.**

## 1. Source of truth

| File | Owns | Enforced by |
|---|---|---|
| `web/gvc-ui.css` | ALL tokens + every shared component. All 19 pages link it | `test_no_portal_html_links_legacy_gvc_css` (no page may link legacy `gvc.css`) |
| `web/gvc-forms.css` | Money-form pack (Estimate / Invoice / Change Order only) | `tests/test_forms_redesign.py` |
| `tests/test_mobile_baseline.py` | Viewport meta + safe-area, 16px phone inputs, `--tap` ≥ 44px | CI, every PR |

Shared JS (served at `/ui/<name>.js`, one `<script>` each):

| File | Provides |
|---|---|
| `gvc-theme.js` | Light/dark/auto toggle (`data-theme` on `<html>`, FOUC-safe boot snippet in `<head>`) |
| `gvc-command.js` | `GvcChips` — chips/segmented controls replacing `<select>` |
| `gvc-flow.js` | `GvcFlow` — the money-spine Path strip (Hub › Takeoff › Estimate › Job Start › Job Check › Billing › Invoice) |
| `gvc-form-chrome.js` | `GvcFormChrome` — money-form topbar (56px) + nested path (32px) |
| `gvc-form-stages.js` | `GvcFormStages` — stage rail + `.gvc-actionbar` Continue/Accept + gap banner |
| `gvc-field-jump.js` | `GvcFieldJump` — clickable validation errors (`[data-jump]` → focus the field) |

**There is no build step.** Pages are static HTML served by FastAPI with
`{{EMAIL}}` / `{{EMAIL_JSON}}` / etc. substituted server-side. Any `{{TOKEN}}`
in a page MUST be substituted by its route (`tests/test_forms_redesign.py`
guards this — an unrendered token in JS position kills the whole inline script).

## 2. Art direction (hard rules)

Emerald green + antique gold on soft cream (light) / charcoal (dark).

- **Green = action. Gold = selected/commitment. Brown-burgundy = real alerts only.** Nothing else carries color meaning.
- **No gradients, no washes, no opacity-faded text** — use `--color-text-muted` / `--color-text-faint`. (Disabled at 45% opacity is the one exception.)
- **No `<select>` for filters or modes** — visible pill chips via `GvcChips` (`aria-pressed` carries state). A genuinely long pick-list in a form may keep a searchable control.
- **Pills everywhere interactive** (`--radius-full`); cards/surfaces use `--radius-lg`/`--radius-xl`.
- **Dense but calm — scannable in a truck.** Long content scrolls inside its box on desktop (`.scroll-rows`, `.tablewrap`); on phones inner scroll caps release so the page is the only scroller.

## 3. Tokens (live values in `web/gvc-ui.css` §1)

- **Type:** fluid clamp scale — `--text-xs/sm/base/lg/xl` + numeric `--text-num-sm/num`. ~7 sizes, no page-local font sizes when a token fits.
- **Space:** 4px base — `--space-1..16`.
- **Radius:** `--radius-sm/md/lg/xl/full`.
- **Fonts:** `--font-display` (Cabinet Grotesk→fallbacks), `--font-body` (Satoshi→system), `--font-mono`. **No remote font CDN** — files ship from `web/fonts/`.
- **Touch:** `--tap: 44px` minimum, `--tap-lg: 48px` for primary actions on phone. Portal rule is the stronger 48px on touch widths.
- **Color:** `--color-*` set per `[data-palette='emerald'][data-theme='light'|'dark']`. Every page opts in via `<html data-palette="emerald">` + theme boot.

## 4. Page shells (two, and only two)

1. **App shell** (hub, takeoff bridge, redesigned pages): `.app` → `.rail` (drawer on phone: `.rail.is-open`) + `main` → `.topbar` → `.page`, plus `.tabbar` phone dock.
2. **Money-form shell** (estimate / invoice / change-order): `data-forms="1"`, `GvcFormChrome` topbar + path, stage rail via `GvcFormStages`, sticky `.gvc-actionbar` with Continue/Accept, `gvc-ui.css` + `gvc-forms.css`.

Rules that apply to both: brand links to `/`; page purpose obvious in the header within 3 seconds; one obvious primary action (green or gold); editable pages keep the commit action visible under scroll (sticky bar); theme boot + `gvc-theme.js` exactly once.

## 5. Component inventory (reuse; do not invent)

| Need | Use |
|---|---|
| Button: primary / commitment / secondary / quiet | `.btn.btn-primary` · gold commit variant · `.btn.secondary` · `.btn-ghost` — never a fourth style for an existing role |
| Filter / mode / status choice | `GvcChips` chips or `.segmented` |
| Surface | `.card` / `.card.card-flush` (+ `.card-head`) |
| Scrolling list | `.scroll-rows` + `.row` |
| Wide table | `.tablewrap` (inner horizontal scroll) |
| Text input | `.input` (44px min) / money forms: `.gvc-input` (48px) |
| Status/empty/error notice | `.gvc-banner` / `.card-note` / `.gvc-empty` |
| Toast | shared `.toast` (`gvc-ui.css`) |
| Validation error → field | `GvcFieldJump` (`data-jump`) |
| Metrics / needs / queue on a home | `.grid-metrics`, `.grid-cards`, `.scroll-rows` |

**A new component requires a PR that adds it to this table** — that is the rule
that keeps this file true.

## 6. Mobile baseline (CI-enforced — `tests/test_mobile_baseline.py`)

1. Every page: `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />` — the CSS uses `env(safe-area-inset-*)`, which is inert without the meta.
2. **Inputs ≥ 16px on phones** (both stylesheets pin this in a `max-width: 768px` block) — under 16px iOS Safari zooms the page on focus.
3. Tap targets ≥ 44px (`--tap`), 48px for primary actions.
4. No horizontal page scroll; wide content scrolls inside `.tablewrap`.
5. One nav pattern: rail drawer + `.tabbar` dock (app shell) or `GvcFormChrome` (money forms). Never strand a user — brand always returns to `/`.
6. Form inputs use the right `type=`/`inputmode` so the correct keyboard appears.

## 7. Flow rules & state completeness (no dead ends)

Every page answers: where am I · what's this for · what next · how back ·
how save · what if data's missing · what if it fails.

- Every blocked state has a recovery path (retry / hub / ask admin / alternate tool).
- Every async action: loading → success (with a **Next:** link when another tool continues the job) or error naming the next step.
- Dirty leave flushes autosave or confirms discard.
- Ungranted/unconfigured = explained (toast/banner), never a silent dead click.
- States to cover on data screens: loading · empty (with guidance) · error (with recovery) · partial failure (name what landed vs failed) · no-permission · degraded/offline (serve cached with an honest "as of" stamp — **stale data must never look live**).

## 8. Validation

```bash
python scripts/ui_consistency_check.py          # anti-pattern lint
python -m pytest tests/test_mobile_baseline.py tests/test_forms_redesign.py -q
```

Plus the checklist in `docs/FEATURE_PROCESS.md` (definition of done) on every PR.
