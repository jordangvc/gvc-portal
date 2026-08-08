# GVC — Cursor handoff pack

Read this file first. It says what to install, in what order, and what "done" looks like.
Everything else depends on step 1.

## Files

| File | What it is | What you do with it |
| --- | --- | --- |
| `gvc-ui.css` | **The stylesheet.** Tokens + every component class. | Copy in, link it once, delete competing CSS. |
| `GVC Design Style.md` | Class reference + rules. | Read before writing markup. |
| `GVC Hub Handoff.md` | Hub shell + personal home screen. | Build after the CSS is in. |
| `GVC Takeoff Handoff.md` | The takeoff app, all screens. | Build after the CSS is in. |
| `Status Picker Handoff.md` | Job Check status picker. | Build last; depends on chip classes. |
| `gvc-review-reference.html` | **A finished screen, built only from the classes.** | Open it, then copy its markup. This is the answer to "what should it look like". |
| `gvc-takeoffs-reference.html` | **The Takeoffs list, finished.** | Copy its markup. |
| `gvc-measuring-reference.html` | **Drywall measuring, finished** — plus the empty, loading, error and offline states. | Copy its markup. |
| `gvc-phone-reference.html` | Every screen at a real 390px viewport, side by side. | Open it. This is the real target. |
| `gvc-shell-demo.html` | The app shell (rail + topbar + tabbar) alone. | Copy for any new screen. |
| `gvc-v2-patch.css` | Corrective layer for the live v2 app. | Load last; delete blocks as you convert screens. |
| `UI Checklist.md` | **The definition of done for a screen.** | Run it on every page before shipping. |
| `Cursor Prompt.md` | The message to paste into Cursor. | Paste it. |
| `GVC Takeoff v2 Fixes.md` | Screenshot audit of the live app. | Work the priority list at the bottom. |
| `gvc-shell-demo.html` | A working app shell built only from the classes. | Open it in a browser. Resize to 390px. This is the target. |

## The prompt

`Cursor Prompt.md` in this pack is the message to paste into Cursor. Use it as written.
It tells Cursor to **convert**, not to design — asking Cursor to build a design system
when one already exists produces a second system beside the first, which is how the app
grew orange, purple, blue and red alongside the green and gold.

## Start here

Open `gvc-review-reference.html` in a browser. That is a real, finished screen built from
nothing but `gvc-ui.css` classes and one 6-line script. **Copy its markup patterns
directly** — it is faster and more accurate than reading any description in this pack.
`gvc-shell-demo.html` does the same for the app shell.

## Order

1. **Install `gvc-ui.css`.** `<link rel="stylesheet" href="/styles/gvc-ui.css">` in the head,
   before any app CSS. Set `<html data-palette="emerald" data-theme="light">`.
2. **Delete or neutralize the old styles.** Any existing rule setting a color, a
   border-radius or a button background will fight this file and win on specificity.
   Two live stylesheets is the most common reason a port comes out wrong.
3. **Convert markup to the classes.** Do not write new CSS. If something seems to need a
   style that isn't here, reuse the closest class instead of adding one.
4. **Open `gvc-shell-demo.html`** beside your build and resize the window from 1400px to
   390px. Every rule in this pack is visible in that one file — rail, top bar, cards,
   chips, the drawer, the bottom bar, the released scroll caps. If your screen doesn't
   behave the same way at the same widths, something in step 2 or 3 was missed.
5. Then the three app docs, in any order.

## Why previous ports came out wrong

Rules, not preferences:

1. **Buttons are `border-radius: 9999px`.** Not 8px, not 12px. Use `.btn`; never restyle
   a bare `<button>`.
2. **The green primary button carries a gold hairline** —
   `box-shadow: inset 0 0 0 1px var(--color-accent)`. One line, and it is what makes the
   button look like GVC. It gets dropped in every port. Don't drop it.
3. **Selected = gold, all three parts:** `--color-accent-soft` fill, `--color-accent`
   border, `--color-accent-ink` text, plus the inset gold ring. Partial application
   (gold text only, gold border only) reads as a bug.
4. **Inputs are pills too** — text, search, number and date. Square inputs beside round
   buttons is the number-one tell of an unfinished port.
5. **Field labels are the mono uppercase kicker** (`.kicker`), never sentence-case body
   text. Half a card in kickers and half in body labels looks broken.
5b. **Muted is for information, faint is for absence.** `--color-text-muted` on labels,
   timestamps and subtitles; `--color-text-faint` only on placeholders and "not set"
   marks. Faint is deliberately below AA at 10&ndash;13px &mdash; it must never carry
   something the user has to read in a truck in daylight.
6. **No gradients. No opacity-faded text or icons.** Every color is one solid token; dim
   with `--color-text-muted` / `--color-text-faint`, never `opacity`.
7. **No `<select>` anywhere.** Finite option sets render as `.chip` groups with every
   option visible. This is a product decision, not a style — the field crew picks faster
   from visible options than from a dropdown.
8. **44px minimum on every target**, 48px for primary actions on phone.
8b. **The full radius is for controls only** — buttons, chips, inputs, avatars. List
   rows, cards and panels take `--radius-md` / `--radius-lg`. A 1000px-wide pill reads
   as a button nobody can press.
9. **Rails need `min-width: 0; overflow-x: hidden`**, job names need
   `overflow-wrap: anywhere`. The naming standard is long; without these the sidebar
   grows a horizontal scrollbar.
10. **Nothing auto-sends.** Anything that puts a document in front of a customer reads
    **"Approve to send"**, and a person presses send.

## Job naming standard

Every job name, everywhere — UI, exports, monday, Drive:

```
[Street Number Name], [City], [ST] [ZIP] | [Builder] | [Job Title]
```

- Residential: `9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence`
- Commercial: `300 Tiger Blvd, Lawrenceburg, IN 47025 | Maxwell Construction | First Financial Bank`

The pipe stays — never a dash or underscore. Left side always carries street + city +
state + ZIP; if a piece is missing, **ask, don't guess**. Middle is the builder / GC.
Right is the business name (commercial) or `{Last name} residence` (residential).
Job-type words ("New House", "Remodel", "Punch") live in fields, never the title.
Prefixes (`CO_`, `STU-`) stay on the front.

Split across lines on a card it is still the same three parts in the same order — never
reordered, never abbreviated:

```
1246 Meriweather Avenue
Cincinnati, OH 45248
Willow Creek | Meriweather residence
```

One exception: **crew text messages carry the address only** (street, city, ST, ZIP), so
the maps link works and the crew isn't reading contract details.

## The repetition rule

If a toolbar, a confirm bar or a card header appears more than twice on one screen with
only its content changing, it is **one component with a picker**, not N copies. The
takeoff Review screen had seven copies of the same four-button toolbar; it is now one
Outputs card with a chip row. Apply the same test everywhere.

## Four states, always

Every list and screen ships **full, empty, loading and error** — all four are in
`gvc-ui.css` and shown in `gvc-measuring-reference.html`. An empty state names what goes
there and offers the action that fills it; it is never "No data". Loading is a skeleton in
the shape of the content, never a spinner. An error says what failed, what it means, and
what to do next. Offline is `.stale` — warm, not red; a phone with no signal in a
basement is a Tuesday, not a failure.

## Flow rules

Layout is not just what things look like — it is what order they happen in. Four rules
the reference builds now follow:

1. **The send action is the last thing on the page.** The Review screen originally put
   "Send to office" *above* the outputs you have to confirm first. Confirm, then detail,
   then send. If a screen has one irreversible action, it goes at the bottom and it is
   the only green button.
2. **One number, one source.** A count that appears in two places must come from one
   query. Review said "1 confirmed" in the banner and "2 of 7" in the Outputs card;
   the Takeoffs list said "104 in field" in the pipeline and "Active 48" in the filters.
   Both were real bugs waiting to happen.
3. **A control sits with what it controls.** The stage filter chips floated above the
   Exceptions card looking like a page filter; they belong inside its header. Collapse
   All / Expand All sat in the send block; they belong above the sections they collapse.
4. **Every drawer needs a way out.** The phone rail opens as `position: fixed; inset: 0`,
   which covers the hamburger that opened it. Without an explicit close control the user
   is trapped unless they happen to tap a nav item.

## Definition of done

- [ ] No `<select>` elements remain.
- [ ] Every button uses `.btn`; no bare styled `<button>`.
- [ ] Every green primary button has the gold inset hairline.
- [ ] Every input is fully rounded and ≥44px tall.
- [ ] Every field label uses `.kicker`.
- [ ] No `linear-gradient`, no `opacity:` on text or icons.
- [ ] Grep your components for `#` — hex codes should return nothing; colors come from
      `var(--color-*)`.
- [ ] At 390px: nothing clips, nothing double-scrolls, all targets ≥44px.
- [ ] At 1440px: the rail has no horizontal scrollbar, the top bar doesn't clip.
- [ ] Job names follow the standard.
