# The prompt to paste into Cursor

Paste everything below the line. It assumes the handoff pack is already in the repo.

Do **not** ask Cursor to design a system, create tokens, or standardize components — that
work is done and it is in `gvc-ui.css`. Asking again produces a *second* system beside
the first, which is how the app ended up with orange, purple, blue and red in the first
place. The job now is conversion and enforcement, not design.

---

You are converting the GVC app to an existing design system. **Do not design anything.**
The system is finished and lives in these files, already in the repo:

- `gvc-ui.css` — the stylesheet. Tokens and every component class. This is the single
  source of truth for color, spacing, type, radius, shadow and every component.
- `GVC Design Style.md` — class reference.
- `UI Checklist.md` — the definition of done for any screen.
- `gvc-review-reference.html`, `gvc-takeoffs-reference.html`,
  `gvc-measuring-reference.html`, `gvc-shell-demo.html` — four finished screens built
  only from those classes. **Copy their markup patterns.**
- `gvc-phone-reference.html` — all four at a real 390px viewport.
- `gvc-v2-patch.css` — a temporary corrective layer for the live app.
- `README - Cursor handoff.md` — install order and the rules that keep getting lost.

## Your constraints

1. **Write no new CSS.** If a style seems missing, the class exists and you missed it.
   Search `gvc-ui.css` first. Only if a pattern genuinely repeats across two or more
   screens do you add it — to `gvc-ui.css`, with a name, using only `var(--*)` tokens,
   never inline on a page.
2. **No hex codes in components.** Grep your work for `#`; it should come back empty.
3. **Delete, don't layer.** The existing `app.css` fights `gvc-ui.css` and wins on
   specificity. Two live stylesheets is why the port looked wrong before. Remove the old
   rules for each screen as you convert it.
4. **Copy the reference builds.** When a screen resembles one of the four, lift its
   markup rather than interpreting prose. That is what they are for.

## Do this in order

### 1. Install and inventory

Link `gvc-ui.css` first in the head, set `<html data-palette="emerald" data-theme="light">`,
and add `gvc-v2-patch.css` last. Then report, before changing anything:

- every file that sets a color, radius, shadow or font
- every `<select>` in the codebase
- every distinct button treatment, with a count
- every screen with no back navigation
- every screen with zero or more than one primary action
- every list with no empty state
- every async action with no loading or error state
- every button whose handler is missing, stubbed, or points at a backend route that
  does not exist

Give me that as a table. Do not start converting until I have seen it.

### 2. Convert the shell

Replace the app's chrome with `.app` / `.rail` / `.topbar` / `.page` / `.tabbar` from
`gvc-shell-demo.html`. Every screen inherits it. Do this once, correctly, before touching
any individual page.

### 3. Convert screen by screen

Highest traffic first: Takeoffs list → Drywall measuring → Review → the remaining
measuring steps → Estimate → dialogs.

For each screen: convert the markup to the classes, delete that screen's old CSS, delete
the now-dead blocks from `gvc-v2-patch.css`, then run `UI Checklist.md` against it and
paste me the completed checklist. Do not move on with items unchecked.

### 4. Report what you cannot fix

Some of what is wrong is data, not code:

- test records and recovered autosaves mixed in with real jobs
- job names missing ZIPs, builders or titles
- builder names spelled two ways

Do not silently render these as if they were complete. Surface them: a `.tag-gold`
saying what is missing, a primary action of **Finish the name**, and `.rowcard-junk` for
records with no address. Then give me the list so I can fix the source.

## Definition of done

`gvc-v2-patch.css` is empty and deleted, `app.css` is gone, no `<select>` remains,
grep for `#` in components returns nothing, and every converted screen has a completed
`UI Checklist.md` run pasted in the PR.

## How to report

Every time you finish a screen:

1. What you converted
2. Which old CSS you deleted
3. Which patch blocks you removed
4. The completed checklist
5. Anything you could not fix, and whether it is code, backend or data
