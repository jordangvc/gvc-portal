# GVC Design Style — class reference

Companion to `gvc-ui.css`. That file is the source of truth; this says what to reach for.
**Do not write new CSS.** If you need something that isn't here, reuse the closest class.

---

## Art direction

Emerald green and antique gold on soft cream. Green carries **chrome and actions**; gold
carries **selection and emphasis**; a muted brown-burgundy is reserved for real alerts.
Flat and solid — no gradients, no washes, no faded text or icons. Everything interactive
is a fully rounded pill. Dense but calm; scannable in a truck.

| Role | Token | Light | Dark |
| --- | --- | --- | --- |
| Action / chrome | `--color-primary` | `#154230` | `#4f9070` |
| Selection | `--color-accent` | `#a6824a` | `#a6824a` |
| Selection text | `--color-accent-ink` | `#6d5326` | `#cfa96a` |
| Selection fill | `--color-accent-soft` | `#eee3cf` | `#2a2317` |
| Alert | `--color-danger` | `#6b4038` | `#a9887f` |
| Ground | `--color-bg` | `#e6e2da` | `#101111` |
| Card | `--color-surface` | `#efece6` | `#171a18` |
| Inset / input | `--color-surface-2` | `#f7f5f1` | `#1d211e` |
| Text | `--color-text` | `#101111` | `#e6e2da` |
| Muted text | `--color-text-muted` | `#4e5551` | `#a0a8a2` |
| Faint text | `--color-text-faint` | `#838a85` | `#767e78` |
| Divider | `--color-divider` | `#d5d0c5` | `#212623` |
| Border | `--color-border` | `#bdb7aa` | `#35443b` |

**Muted vs faint.** `--color-text-muted` is *de-emphasized information* &mdash; labels,
timestamps, subtitles, anything the user still has to read. `--color-text-faint` is
*absence* &mdash; placeholders, "not set" em dashes, junk records. Faint is below AA at
small sizes on purpose; never put readable content in it. Every kicker, stat label and
timestamp in the system uses muted.

Type: **Satoshi / Cabinet Grotesk** for UI, **JetBrains Mono** for figures, labels and
message previews. Numbers the user reads as numbers get `.num` (tabular).

---

## Buttons

```html
<button class="btn btn-primary">Approve to send</button>   <!-- green + gold hairline -->
<button class="btn">Open job</button>                      <!-- outlined -->
<button class="btn btn-ghost">Cancel</button>
<button class="btn btn-danger">Delete</button>
<button class="btn btn-dashed">+ Add floor</button>
<button class="btn btn-icon" aria-label="Refresh">⟳</button>
<button class="btn btn-primary btn-block btn-lg">Confirm this message</button>
```

Sizes: `.btn-sm` 38px · default 44px · `.btn-lg` 48px (phone primary).

**Where green goes.** Two places. Once in the **page flow** — the action that moves the job
forward, last in reading order. And once inside a **card that owns a decision** — a confirm
footer, an `.empty` state's only action, a banner asking you to choose; one per card, so a
queue of decision cards each gets theirs.

Nowhere else. Not in a topbar (Save and Update up there are outlined — a topbar action is
convenience, not the primary). Not on a section-level add, however many there are; a
builder screen with green on every "+ Add" is a field of green and nothing reads as the
next step. Not in a row of peer tools — Email / Copy / PDF / Edit / Reset are one weight,
and promoting one implies the rest are lesser. And never twice for the same action.

**One weight per row.** Don't mix `.btn-ghost` into a row of outlined `.btn`s; the ghost
reads as a text label rather than a button. Ghost is for utilities standing on their own.

Flush action row on a card's bottom edge — job cards use this:

```html
<div class="btn-row">
  <button class="is-primary">Estimate</button>
  <button>Duplicate</button>
  <button>Navigate</button>
  <button class="is-danger">Delete</button>
</div>
```

## Chips — there are no dropdowns

Any field with a finite option set shows every option.

```html
<div class="chipset">
  <span class="chipset__label kicker">Waste</span>
  <div class="chipset__opts">
    <button class="chip" aria-pressed="false">0%</button>
    <button class="chip is-active" aria-pressed="true">5%</button>
    <button class="chip" aria-pressed="false">10%</button>
  </div>
</div>
```

`.chip-next` is the green "suggested next" variant — status picker only.
`.chip-lg` for phone-primary rows. A state word inside a chip uses `.chip__state`:

```html
<button class="chip is-active">Hang crew <span class="chip__state">Confirmed</span></button>
```

## Segmented control

```html
<div class="seg" role="tablist">
  <button class="is-active" aria-selected="true">Internal</button>
  <button aria-selected="false">Customer</button>
</div>
```

## Tags — read-only status

```html
<span class="tag">Draft</span>
<span class="tag tag-gold">Needs you</span>
<span class="tag tag-green">Confirmed</span>
<span class="tag tag-alert">Waiting on GC</span>
<span class="badge">7</span>
```

## Inputs

```html
<label class="field">
  <span class="kicker">Stage completion</span>
  <input type="date" class="input">
</label>

<input class="input-num" inputmode="numeric" value="12">

<div class="stepper">
  <button aria-label="Less">−</button>
  <span class="stepper__val">12</span>
  <button class="is-plus" aria-label="More">+</button>
</div>

<label class="toggle"><input type="checkbox"> Show complete</label>
```

Every input is a pill at 44px. `.field` stacks the kicker over the control. `.toggle` is
`flex: 0 0 auto; white-space: nowrap` so labels never break across lines.

## Cards, rows, previews

```html
<section class="card card-flush">
  <div class="card-head">
    <h2>Crew texts</h2>
    <button class="btn btn-sm btn-dashed">+ Materials list</button>
  </div>

  <a class="row">
    <span class="row__main">
      <b class="row__title">4115 Witler | Drees</b>
      <span class="row__sub">Hang · Mark W · blocked 2 days</span>
    </span>
    <span class="tag tag-alert row__end">Waiting on GC</span>
  </a>

  <pre class="preview">PROJECT START…</pre>

  <div class="card-foot card-foot-ok">
    <span style="flex:1">Confirmed — ready to send</span>
    <button class="btn btn-primary">Mark sent</button>
    <button class="btn">Undo</button>
  </div>
</section>
```

`.card-pad` for a padded card · `.card-flush` when rows run edge to edge ·
`.card-note` adds the gold left edge (instruction cards, needs-you items) ·
`.card-foot-ok` turns the footer green when something is confirmed.

## Shell

```html
<div class="app">
  <aside class="rail">
    <div class="rail__head">
      <span class="kicker">Takeoff</span>
      <div class="rail__job">1246 Meriweather Avenue, Cincinnati, OH 45248 | Willow Creek | Meriweather residence</div>
      <div class="progress"><i style="width:50%"></i></div>
    </div>
    <nav class="rail__nav">
      <div class="rail__group">
        <span class="kicker kicker-sm">Money</span>
        <a class="nav-item is-active"><span class="nav-item__name">Billing Hub</span><span class="badge">7</span></a>
        <a class="nav-item"><span class="nav-item__name">Lien Watch</span></a>
        <a class="nav-item is-locked"><span class="nav-item__name">Admin</span><span class="faint">—</span></a>
      </div>
    </nav>
    <div class="rail__foot"><span class="avatar">JD</span><span class="row__main">…</span></div>
  </aside>

  <main>
    <header class="topbar">
      <div class="topbar__title"><span class="kicker">Step 3 of 14</span><h1>Drywall</h1></div>
      <div class="topbar__actions"><button class="btn btn-primary btn-sm">Save</button></div>
    </header>
    <div class="page">…</div>
    <nav class="tabbar">…</nav>   <!-- phone only; auto-hidden ≥900px -->
  </main>
</div>
```

Nav item states: `.is-active` (green soft fill + inset green rail) · `.is-locked` (grant
not held — dimmed with an em dash, never hidden) · `.nav-item__done` for the gold ✓ ·
`.nav-item__qty` for a mono quantity.

`.phone-only` hides a control above 900px &mdash; the rail's close button uses it, since
on desktop the rail is permanent and a close control would be a dead button.

Layout helpers: `.grid-metrics` (auto-fit 190px) · `.grid-cards` (auto-fill 300px) ·
`.grid-split` (1.35fr / 1fr, collapses on phone) · `.stack` · `.cluster`.

## Path track

The money path on every portal page. It replaces both a breadcrumb and the numbered
prose list that used to describe the same sequence.

```html
<nav class="path" aria-label="Money path">
  <button class="path__step is-done">Hub</button>
  <button class="path__step is-here">Takeoff</button>
  <button class="path__step">Estimate</button>
</nav>
```

`.is-here` is green &mdash; the path is chrome, not a selection, so it doesn't take gold.
`.is-done` dims and gains a gold check. It scrolls sideways on a phone rather than wrapping.

## Handoff cards

For a page whose whole job is sending you somewhere else. Two destinations at most, as
full choices rather than a row of same-size buttons where the real action hides among its
neighbours.

```html
<section class="handoff">
  <a class="handoff__card is-primary">
    <span class="handoff__kicker">Measure</span>
    <b class="handoff__title">Open the Takeoff app &rarr;</b>
    <span class="handoff__body">Boards, bead and scope. Opens in a new tab.</span>
  </a>
  <a class="handoff__card">…</a>
</section>
```

`.is-primary` is the button grammar at card scale &mdash; green ground, gold hairline. It
counts as the screen's one green action; don't add another primary below it.

## Scrolling

`.scroll-rows` caps at 26rem, `.scroll-tall` at 62vh, both with
`overscroll-behavior: contain`. A sticky "show more" uses `.scroll-rows__more`. Tables
use `.tablewrap` (sticky header included). All of them release on phone so the page is
the only scroller — that's already in the CSS, don't re-add caps in components.

## Empty, loading and error

Every list and screen has four states, not one. They are in the stylesheet &mdash; don't
invent them per screen.

```html
<div class="empty empty-inline">
  <b class="empty__title">No exterior walls measured yet</b>
  <p class="empty__body">Add a run for each wall, or copy the ceiling dimensions.</p>
  <div class="empty__actions">
    <button class="btn btn-primary btn-sm">+ Add a run</button>
    <button class="btn btn-sm">Copy from ceilings</button>
  </div>
</div>
```

An empty state **names what goes here and offers the action that fills it**. Never
"No data", never a shrug. `.empty-clear` is the good empty &mdash; a queue you cleared,
tinted green, because that is news worth reporting.

Loading uses skeletons in the shape of the thing (`.skel`, `.skel-title`, `.skel-sub`,
`.skel-row`), not a spinner &mdash; the shape *is* the loading state. A button mid-action
takes `.is-working`: the label stays put and a small spinner appends. Never swap a
button's label for a spinner; the user loses their place. `.loading-bar` is the 2px
sticky bar for a background refresh.

Errors say what failed, what it means, and what to do:

```html
<div class="errorbox">
  <b class="errorbox__title">Couldn't reach the plan file</b>
  <p class="errorbox__body">The PDF is on Drive and your phone is offline. Your
     measurements are safe on this device.</p>
  <div class="errorbox__actions">
    <button class="btn btn-sm">Try again</button>
    <button class="btn btn-sm btn-ghost">Keep measuring without it</button>
  </div>
</div>
```

`.field__error` + `.is-invalid` handle field-level errors. `.stale` is the offline
caveat bar &mdash; warm, not red, because working offline in a basement is normal here.

## Measuring row

The densest row in the product: `.mrow` with `.mrow__dims` (two `.input-num` around
`.mrow__x`), a board `.chip`, `.mrow__label`, `.mrow__tags` (`.tchip` &mdash; the 2-3
letter FO / MD / RC1 marks), and `.mrow__end`. Under 620px it reorders itself two-up:
dimensions and board on line one, label, tags and actions on line two. `.mtotal` closes
a group with the running figure in green.

## Interaction

Hover tints, the gold `:focus-visible` ring, `::selection` and 45% disabled are all in
the stylesheet. Don't restyle them per screen.

## Phone

The CSS handles the breakpoints. What you owe it in markup:

- Put the rail's open state on `.rail.is-open` (full-screen drawer under 900px).
- Render `.tabbar` in every app shell — it hides itself above 900px.
- Primary actions get `.btn-lg .btn-block` on phone-first screens.
- Don't nest your own `max-height` scrollers inside `.page`.
