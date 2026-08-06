# GVC Command — Design Style Spec

**Contract for portal UI.** Token-driven: no component may hard-code a hex, font,
or px value that a token already carries.

Implementation lives in `web/gvc.css` (Command section). Activate per page with:

```html
<html lang="en" data-palette="emerald" data-theme="light">
```

Without `data-palette`, legacy `--gvc-*` pages are unchanged. Hub opts in first.

**Portal tap floor:** ≥48px on touch widths (Command asks ≥44; keep the stronger
Jordan rule). **No remote font CDN** — Cabinet Grotesk / Satoshi fall through to
embedded Montserrat / Lato.

---

## 1. Art direction

Emerald green and antique gold on soft cream or charcoal. Green carries **chrome and
actions**; gold carries **selection and emphasis**; burgundy-brown is reserved for real
alerts only. Flat and solid — no gradients, no washes, no opacity-faded text or icons.
Rounded: buttons, chips and segmented controls are full-radius pills. Dense but calm,
scannable in a truck.

Hard rules:

- **No gradients or fades anywhere.** Every fill is one solid token. No `opacity:` used
  to dim text or icons — use `--color-text-muted` / `--color-text-faint` instead.
- **No dropdowns / `<select>` for filters.** All options render as visible pill buttons
  (chips or segmented) that highlight when active. (Form fields that truly need a
  long pick-list may keep a select; filters and modes may not.)
- **Red is rationed.** `--color-danger` is a warm brown-burgundy and appears only for
  overdue / blocked / failure states. Never decorative.
- **Boxes with lots of content scroll inside themselves**, not down the page
  (desktop). On ≤620px, release inner scroll caps so the page is the only scroller.
- **Every interactive target is ≥44px tall on touch widths** (portal: 48px).

---

## 2. Tokens

`[data-palette]` on `<html>` picks the palette, `[data-theme]` picks light/dark.
Default for new work: `data-palette="emerald" data-theme="light"`.

See `web/gvc.css` for the live values (type scale, spacing, radius, emerald light/dark,
and the `--gvc-*` bridge so existing `.gvc-btn` / `.gvc-card` pick up Command when
opted in).

Emerald brand anchors: Emerald Green `#154230` · Deep Burgundy `#5D1E21` ·
Charcoal `#101111` · Antique Gold `#A6824A` · Soft Cream `#E6E2DA`.

Optional palettes (`brand` / `field` / `night`) — only add when a switcher is wanted.

---

## 3. Components

Use Command class names on new UI:

| Class | Role |
|---|---|
| `.btn` / `.btn--primary` / `.btn--ghost` | Pills; primary = green + gold hairline |
| `.chipset` / `.chip` / `.chip.is-active` | Filter replacement (gold when active) |
| `.segmented` / `button.is-active` | Mode toggle |
| `.card` / `.card--flush` | Surfaces |
| `.rows` / `.tablewrap` | Contained scrolling |

Legacy `.gvc-*` stays valid. Under emerald, `.gvc-btn--primary` also gets the gold
hairline and full-radius pills.

Chips markup (buttons, not `<select>`; `aria-pressed` carries state):

```html
<div class="chipset">
  <span class="chipset__label">Stage</span>
  <div class="chipset__opts">
    <button type="button" class="chip is-active" data-filter="stage" data-value="all" aria-pressed="true">All stages</button>
    <button type="button" class="chip" data-filter="stage" data-value="Hang" aria-pressed="false">Hang</button>
  </div>
</div>
```

---

## 4. Interaction states

- Hover: `--color-primary-hover` on filled, `--color-accent` border on outlined.
- Active/selected: gold trio — `--color-accent-soft` fill, `--color-accent` inset ring,
  `--color-accent-ink` text.
- Focus: `outline: 2px solid var(--color-accent); outline-offset: 2px;` on
  `:focus-visible`.
- Selection: `::selection` uses `--color-primary-soft`.
- Disabled: 45% opacity (the one legitimate opacity use).

---

## 5. Contained scrolling

Dense boxes scroll inside themselves; the page never grows unbounded on desktop.
Use `.rows`, `.tablewrap`, or existing Billing `.billing-scroll` patterns.
On ≤620px, inner max-heights are released (no nested scroll traps on a phone).

---

## 6. Mobile (≤620px)

- Filter groups: one swipeable row each (`flex-wrap: nowrap; overflow-x: auto`).
- Targets ≥48px; chip `padding-inline: 16px`.
- Safe area: `env(safe-area-inset-*)` on top bar / footers (already on hub).

---

## 7. Checklist for any new screen

1. Colors come from tokens only — no hexes in components.
2. No gradient, no faded text, no dimmed icons.
3. Filters and modes are visible pill buttons, not dropdowns.
4. Selected state is gold; primary action is green; alerts are the brown-burgundy.
5. Anything that can get long has an internal scroll cap (desktop) and sticky chrome.
6. Works at 375px with 48px targets, and at 1440px without clipping the top bar.
7. Opt the page in with `data-palette="emerald"` only when ready — do not surprise
   unmigrated tools.

---

## Rollout

| Page | Status |
|---|---|
| Hub (`web/hub.html`) | Opted in (emerald) · **r41** |
| Estimate / Invoice | Emerald + chip project type, delivery, invoice type, retainage scope |
| Job Start / Job Check | Emerald + status columns as gold-active chips |
| Activity | Emerald + Outcome / Range chip filters |
| Morning | Emerald + Action Request category chips; origin segmented |
| Billing / CO / Check / COI / Admin / Lien / Timeoff / Field Manual | Emerald chrome opted in |
| Salesperson roster / Monday search lists | Stay as searchable controls (not chips) |

Customer-facing PDF templates (`templates/*.j2`) are **out of scope** — separate brand
vector decision with Jordan.
