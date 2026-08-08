# Forms conversion — remaining diffs vs reference (r103)

Side-by-side against `gvc-forms-reference.html` at 1440px / 390px after port.
Do **not** read this as “matches.”

## Matches now

- Stylesheets: `gvc-ui.css` then `gvc-forms.css` (no `gvc.css`)
- Shared chrome: one `GvcFormChrome` → `.gvc-topbar` 56px + nested `.gvc-path` 32px
- Generator gold pills; path steps via `mountFormsPath`
- Stage rail + Continue/Back; primary lives in `.gvc-actionbar`
- Zero `<select>` in markup; delivery / type / retainage are `.gvc-chip`
- Salesperson is `.gvc-people` cards (Estimate)
- Helper `.hint` / `.sec-help` paragraphs removed
- Doc column hides ≤1079px; action bar stays

## Still different (code / product, not ignored)

| Diff | Notes |
|---|---|
| Find / Who / Scope content is ported live fields, not the reference’s demo copy | Reference uses Maxwell demo; live keeps Monday lookup + real fields |
| Scope tiles (priced `.gvc-tile`) not yet replacing Estimate’s scope checkbox admin UI | Existing `#scope-groups` still drives lines |
| Line rows still use legacy line-item JS DOM, not full `.gvc-line` markup | Totals still derived from live row math |
| Email recipients are still a textarea, not `.gvc-chipfield` | Needs a chipfield widget + parse/serialize |
| “Customer has no email” is still a checkbox, not `.gvc-toggle` | Behavior preserved |
| Invoice correction modal uses a small page `<style>` bridge | No modal in `gvc-forms.css` yet — candidate to promote |
| Accept still confirms via `window.confirm` before live run | Spec wants in-flight label on the button (partially: `Accepting…`) |
| Autosave / health / theme sit in the topbar | Reference only shows saved + avatar — theme kept for portal parity |
| Job Start / Billing / Check / Job Check still on `gvc.css` chrome | Forms chrome is shared across the three generators; spine-wide chrome next |

## Data (not this PR)

- Job names missing ZIP / builder / title should surface `.tag-gold` “Finish the name” when those screens own naming — not invented here.
