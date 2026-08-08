# Cursor prompt — GVC forms

Paste everything below into Cursor as a single message, with these files attached:

- `gvc-ui.css`
- `gvc-forms.css`
- `gvc-forms-reference.html`

---

## Why the last few passes drifted

The design files being handed over previously were component files written for a
preview runtime. Cursor cannot execute that runtime, so it read the markup as an
approximation and rebuilt it from intent. Every rebuild lost a little: the topbar
picked up a shadow, the path strip changed height, helper text crept back under the
fields, a second green button appeared. That is why the app "never quite catches up."

`gvc-forms-reference.html` fixes this. It is plain HTML, vanilla JS, no build step,
no framework. Open it in a browser and it renders exactly as designed. There is
nothing to interpret.

---

## The prompt

> You are porting three screens to match a finished design. The design is not a
> suggestion — it is a spec, and it already works.
>
> **Attached:**
> - `gvc-forms-reference.html` — the finished design, plain HTML, runnable as-is
> - `gvc-ui.css` — design tokens (colors, type, spacing, radii). Source of truth.
> - `gvc-forms.css` — every class used by the reference. Source of truth.
>
> **Step 1 — audit before you write anything.**
> Open the reference file. Then list, for each of the three existing screens
> (Estimate Generator, Change Order, Invoice Generator), every place the current
> app diverges from it. Do not fix anything yet. Show me the list.
>
> **Step 2 — install the stylesheets.**
> Copy `gvc-ui.css` and `gvc-forms.css` into the app's stylesheet directory and
> link both, `gvc-ui.css` first. Then find and delete every competing style rule
> that targets these screens — inline styles, module CSS, utility classes,
> component-level overrides. Competing styles are the reason the design keeps
> half-applying. If you are unsure whether a rule competes, show it to me.
>
> **Step 3 — port the chrome first, once, globally.**
> `.gvc-topbar` and `.gvc-path` are one shared component used by every screen in
> the app, not per-screen markup. Build it once. Copy the markup from the
> reference exactly. Specifically:
> - the topbar is 56px tall on `#0d2e21`
> - the path strip is 32px tall on `#154230`, directly beneath, no gap
> - no shadow, no bottom border, no rounded corners on either
> - the generator switcher is pills; the active pill is gold `#a6824a` with dark
>   green text
> - nothing else goes in the topbar — no primary button, no save button
>
> **Step 4 — port the screens, highest traffic first:** Invoice, then Estimate,
> then Change Order. For each one, copy the markup from the matching
> `<section data-stage="…">` block. Keep class names. Keep element order.
>
> **Step 5 — wire the behavior.** Three rules, all violated by the current app:
> 1. **Accept always lands somewhere.** It shows an in-flight label, then a
>    terminal success panel naming what was created. It is never a silent no-op.
> 2. **An incomplete form's primary button routes to the missing field.** It does
>    not disable silently and it does not do nothing. It names the first gap
>    ("Finish a client") and jumps to that stage.
> 3. **Every total is derived from the visible rows.** No stored total, no typed
>    total. Optional add-ons are excluded from the main total and shown separately.
>    Retainage is subtracted, never hidden.
>
> **Constraints that are not negotiable:**
> - No `<select>` anywhere. Every choice is a visible pill, tile, or card.
> - No helper paragraph under a field. Label above, hint in the placeholder.
> - One primary button per screen, and it lives in the sticky action bar.
> - Every interactive element is `border-radius: 9999px`. Panels are 22px. The
>   document preview is 6px because it is paper.
> - Minimum tap target 44px. ~90% of use is a phone in the field.
> - Colors come from `gvc-ui.css` variables only. Green = action, gold =
>   selected, burgundy = alert. No other color enters the app.
>
> **When you are done,** run the app side by side with
> `gvc-forms-reference.html` at 1440px and at 390px and tell me every remaining
> difference. Do not tell me it matches — tell me where it does not.

---

## Job naming

Every job name, everywhere — UI, PDF, Drive folder, monday item:

```
[Street Number Name], [City], [ST] [ZIP] | [Builder] | [Job Title]
```

`9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence`
`300 Tiger Blvd, Lawrenceburg, IN 47025 | Maxwell Construction | First Financial Bank`

Pipes, not dashes. Commercial gets the business name; residential gets
`{Last name} residence`. Job type words stay in fields, never in the title.
If a piece is missing, ask — do not guess.

---

## Checklist for the pull request

- [ ] Both stylesheets linked, `gvc-ui.css` first
- [ ] Competing styles deleted, not overridden
- [ ] Topbar + path strip built once and shared
- [ ] Zero `<select>` elements on these three screens
- [ ] Zero helper paragraphs under fields
- [ ] Exactly one primary button per screen, in the action bar
- [ ] Accept produces a visible terminal state
- [ ] Incomplete primary routes to the missing field
- [ ] Totals recomputed from rows on every change
- [ ] 390px: single column, document preview hidden, action bar intact
