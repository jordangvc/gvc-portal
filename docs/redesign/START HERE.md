# START HERE

Drop this whole folder into the repo, then paste the block below into Cursor.
That's it — everything else it needs is in the files.

---

## Paste this into Cursor

```
I've dropped a design handoff folder into this repo. Read it before you write any code.

Start with `Cursor Prompt — Forms.md` — it's written for you and contains the full
instructions. Follow it in order.

Quick orientation so you know what you're looking at:

- `gvc-forms-reference.html` is the finished design for the Estimate Generator,
  Change Order, and Invoice Generator screens. It is plain HTML and vanilla JS
  with no build step. Open it and it renders exactly as intended. This is a spec,
  not a mockup — copy its markup and class names rather than rebuilding from
  what you think it looks like. Earlier attempts at these screens drifted because
  the design was re-derived instead of copied. Don't re-derive it.

- `gvc-ui.css` holds the design tokens (color, type, spacing, radii).
- `gvc-forms.css` holds every class the reference uses.
  Link both, `gvc-ui.css` first. These two files are the source of truth for
  styling — if a screen needs a value that isn't in them, tell me instead of
  inventing one.

- `gvc-review-reference.html`, `gvc-takeoffs-reference.html`,
  `gvc-measuring-reference.html`, `gvc-phone-reference.html`, and
  `gvc-takeoff-bridge-reference.html` are the same kind of artifact for the
  other screens. Same rule: copy, don't interpret.

- `GVC Design Style.md` and `UI Checklist.md` describe the system in prose. They
  explain the CSS — they never override it. If prose and CSS disagree, the CSS wins.

Before you change anything: audit the current app against the reference files and
give me a written list of every divergence, screen by screen. Do not start fixing
until I've read that list.

Two things that must be true when you're done, because they're what keeps breaking:

1. The topbar and the path strip are ONE shared component built once and used by
   every screen — not markup repeated per page. 56px bar on #0d2e21, 32px path
   strip on #154230 directly beneath, no gap, no shadow, no border.

2. Nothing is a dead end. Every primary button lands somewhere visible: an
   in-flight state, then a success state or a route to the field that's blocking it.
```

---

## What's in this folder

**The forms redesign (new)**
- `Cursor Prompt — Forms.md` — the full instructions for Cursor
- `gvc-forms-reference.html` — finished design, runnable, no build step
- `gvc-forms.css` — form grammar classes

**The system**
- `gvc-ui.css` — tokens + core components. Link this first, everywhere.
- `GVC Design Style.md` — the written system
- `UI Checklist.md` — pre-merge checks

**Other screens**
- `gvc-review-reference.html`
- `gvc-takeoffs-reference.html`
- `gvc-measuring-reference.html`
- `gvc-phone-reference.html` — 390px behavior
- `gvc-takeoff-bridge-reference.html`
- `gvc-shell-demo.html` — app shell / navigation

**Background**
- `Cursor Prompt.md` — the original port prompt (still valid for non-form screens)
- `README - Cursor handoff.md`
- `GVC Takeoff v2 Fixes.md`, `gvc-v2-patch.css` — v2 cleanup, delete blocks as they're applied
- `GVC Hub Handoff.md`, `GVC Takeoff Handoff.md`, `Status Picker Handoff.md`

---

## If it starts drifting again

It's almost always one of these three:

1. **A competing stylesheet.** Something else still targets these screens and is
   half-winning. The fix is deleting it, not adding `!important`.
2. **The chrome got rebuilt per-screen.** If two pages have topbars that differ by
   a pixel, there are two topbars. There should be one.
3. **Cursor was given prose instead of the HTML.** Prose gets interpreted. Attach
   the reference file.
