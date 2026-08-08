# Forms redesign audit — 2026-08-08

**Status: conversion shipped in r103** (Invoice → Estimate → Change Order).
This file remains the pre-conversion divergence record.

Source of truth for this pass:

- `Cursor Prompt - Forms.md`
- `gvc-forms-reference.html` (spec, not a mockup)
- `gvc-ui.css` + `gvc-forms.css`

Pack installed under `docs/redesign/`. Served copies:

| File | Served | Linked on Est / CO / Inv? |
|---|---|---|
| `web/gvc-ui.css` | `/ui/gvc-ui.css` | **Yes** (with forms) |
| `web/gvc-forms.css` | `/ui/gvc-forms.css` | **Yes** (Est / CO / Inv) |
| `web/gvc.css` | `/ui/gvc.css` | **No** on Est / CO / Inv |

---

## The two things that keep breaking

### 1. Topbar + path must be ONE shared chrome

**Reference:** `.gvc-topbar` wraps both bars — 56px row on `#0d2e21` (`--color-primary-hover`) + nested 32px `.gvc-path` on `#154230` (`--color-primary`). No gap, no shadow, no gold border. Generator switcher = gold `.gvc-app` pills. Path steps = `.gvc-path-step.is-here`.

**Live (Estimate / CO / Invoice):**

| Violation | Where |
|---|---|
| Still `/ui/gvc.css` only | `estimate.html:23`, `change-order.html:23`, `invoice.html:23` |
| Legacy `.gvc-topbar` + padded `.gvc-topbar-in`; bg `#235339`; **gold bottom border** | `web/gvc.css` ~587 |
| Path is **not nested** under the topbar | Est: `.gvc-flow` inside `<main>`; CO/Inv: `.gvc-flow` between header and main |
| Path is sand card + `›` seps via `GvcFlow.mountLegacy` | `web/gvc-flow.js` when host is `.gvc-flow` |
| No `.gvc-appnav` Estimate / CO / Invoice switcher | page title text instead |
| Topbar also carries health / theme / Sign out | not in the forms chrome |

**Also:** hub + takeoff already use a *third* chrome (`.topbar` / `.path` from `gvc-ui.css`). Forms pack wants `.gvc-topbar` / `.gvc-path`. That global shared-chrome decision has to be made once before screens convert — otherwise we invent a fourth bar.

### 2. Nothing is a dead end

| Risk | Screens |
|---|---|
| Accept starts **`disabled`** until Preview succeeds | All three |
| **Two** visual primaries: Generate (gold) + Accept | All three |
| Success = banner / result HTML, not `data-stage="accepted"` terminal panel | All three |
| Incomplete Accept does not jump to the first missing field | All three |
| Invoice also has Correct/reissue + Start new in the same bar | Invoice |

---

## Screen-by-screen divergences

### Estimate Generator (`web/estimate.html`)

| Area | Reference | Live | Severity |
|---|---|---|---|
| Stylesheets | `gvc-ui.css` then `gvc-forms.css` | `gvc.css` only | blocker |
| Layout | `.gvc-page` → 2-col `.gvc-shell` (form + sticky `.gvc-doc-col`); `.gvc-stages` | Single-col `.gvc-moneyform` cards; no stages | blocker |
| Topbar / path | Nested forms chrome | Legacy topbar + sand `.gvc-flow` | blocker |
| Header | `.gvc-head` + stage rail | `.gvc-page-hd` | high |
| Choices | Pills / tiles / cards — **zero** `<select>` | 3 markup `<select>`s (salesperson never chip-replaced; delivery + project_type replaced at runtime by `GvcChips`) | blocker |
| Helpers | Label + placeholder only | ~17 `.hint` / `.sec-help` | high |
| Lines / totals | `.gvc-line` + derived totals in `.gvc-actionbar` | JS rows + light `.gvc-savebar` | high |
| Primary | One `.gvc-btn-primary` in `.gvc-actionbar` → accepted stage | Generate + disabled Accept | blocker |
| Mobile | ≤1079 hide doc; dark action bar | iframe below; light sticky savebar | high |

### Change Order (`web/change-order.html`)

| Area | Reference | Live | Severity |
|---|---|---|---|
| Stylesheets | `gvc-ui` + `gvc-forms` | `gvc.css` only | blocker |
| Layout | Staged `.gvc-stack` panels + doc col | Flat `.card` stack | blocker |
| Topbar / path | Shared nested chrome; CO pill on | Legacy topbar; `.gvc-flow` between header/main | blocker |
| `<select>` | None | **None** (best of three) | — |
| Helpers | None under fields | ~9 `.hint` / `.sec-help` | high |
| Primary / success | One Accept → terminal panel | Generate + disabled Accept + Start new | blocker |
| Mobile | Dark `.gvc-actionbar` | Light `.gvc-savebar` | high |

### Invoice Generator (`web/invoice.html`)

| Area | Reference | Live | Severity |
|---|---|---|---|
| Stylesheets | `gvc-ui` + `gvc-forms` | `gvc.css` + ~8-line page `<style>` | blocker |
| Layout | Staged panels + live `.gvc-doc` | Long single form (commercial / AIA / retainage / CO pickers inline) | blocker |
| Topbar / path | Shared chrome; Invoice pill on | Same legacy chrome as CO | blocker |
| Choices | `.gvc-chips` / `.gvc-toggle` | 3 markup `<select>`s (runtime chip-replaced) | blocker |
| Helpers | None | ~15 `.hint`s | high |
| Retainage | Subtracted, always visible | “Before retainage” + amount field | high |
| Primary | One Accept in action bar | Generate + Accept + Start new + Correct/reissue | blocker |

---

## `<select>` still in markup

| Screen | Selects |
|---|---|
| Estimate | `#salesperson-select`, `delivery_method`, `project_type` |
| Change Order | *(none)* |
| Invoice | `delivery_method`, `invoice_type`, `retainage_scope` |

Checklist “zero `<select>`” fails until markup is chips/tiles — not select→replace.

---

## Competing CSS (delete, don’t override)

| Competitor | Why it fights |
|---|---|
| `gvc.css` `.gvc-topbar` | **Same class name** as pack; wrong green + gold border |
| `gvc.css` `.gvc-shell` | Pack = 2-col grid; live = padded column |
| `gvc.css` `.gvc-flow` | Alternate path chrome |
| `gvc.css` `.gvc-savebar` | Light bar + gold rule vs dark `.gvc-actionbar` |
| `gvc.css` `.gvc-moneyform` (~350 lines) | Legacy card/btn/select/hint dialect |
| Hub `.topbar` / `.path` | Third chrome vocabulary |
| Invoice page `<style>` | Small page-local leftover |
| `GvcChips` → `.chipset` / `.segmented` | Not `.gvc-chip` from forms pack |

**Class-name collision:** stacking `gvc-forms.css` with `gvc.css` without deleting competitors will half-apply the design — the exact failure mode the Forms prompt describes.

---

## Shared-component gap

| Layer | Today |
|---|---|
| Topbar markup | Copy-pasted per money page |
| Path | Partial shared JS (`GvcFlow`) with two render modes; host markup still per-page |
| Forms-pack chrome | **Missing** — no shared `.gvc-topbar` + nested `.gvc-path` component |

---

## Recommended order AFTER you approve this list

1. Link `gvc-ui.css` then `gvc-forms.css` on money pages; **stop loading** `gvc.css` on those pages.
2. Build **one** shared topbar+path chrome from the reference (extend or replace `GvcFlow` to emit `.gvc-path-step`).
3. Port screens by traffic: **Invoice → Estimate → Change Order** — copy `data-stage` markup, keep class names.
4. Wire behavior: Accept in-flight → terminal success; incomplete primary routes to first gap; totals derived from rows.
5. Side-by-side vs `gvc-forms-reference.html` at 1440px and 390px — list remaining diffs, don’t claim “matches.”

**Waiting on your read of this list before Step 2+.**
