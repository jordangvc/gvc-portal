# GVC Takeoff v2 — what's wrong on the live app, and the exact fix

Screenshot audit of `gvctakeoff.netlify.app/v2.html`, August 7. Read this with
`gvc-ui.css` and `gvc-v2-patch.css` in hand.

The port didn't miss details — the app grew a **second color language** on top of the
first. Orange, purple, blue and red are doing work that green and gold should be doing,
and gold is doing work that green should be doing. Fix the color roles and 80% of the
"doesn't look right" goes away on its own.

---

## The one rule that fixes most of it

```
GREEN  = action           (Send to office, Update, Save, Calculate, + New takeoff)
GOLD   = selection        (active chip, active tab, confirmed state, the ✓)
BURGUNDY = alert          (Delete, blockers, overdue)
EVERYTHING ELSE = neutral (section headings, secondary buttons, Monday exports)
```

Right now the app has it close to inverted: gold is on the primary buttons, green is on
a secondary ("Copy All Data"), and orange, purple, blue and red are carrying meaning
that has no place in the system.

---

## Screen by screen

### 1 · Takeoffs list

| What's there | What it should be |
| --- | --- |
| `+ New takeoff` — full-width solid gold slab | `.btn.btn-primary.btn-block` — green with the gold hairline |
| `Budget bid` / `From plans` with ⚡ and 📐 emoji | `.btn` outlined pills, no emoji (Lucide `zap` / `ruler` if you want a mark) |
| `▶ Continue 9999 keep testing …` | `.btn` outlined; drop the ▶ |
| Filter tabs `Active 49 / Final 0 / Auto-saved 56 / All` — cream fill on the active one | `.chipset` with `.chip.is-active` — gold fill, gold border, gold ink |
| `Manage ▾` | `.btn.btn-sm`; the caret is a Lucide `chevron-down`, not a text glyph |
| Pipeline row | Already right in structure — wrap in `.grid-metrics`, figures get `.num`, labels `.kicker`, and drop the ▣ ♡ $ 🖥 emoji |
| Job card action row: Estimate on a gold-tinted cell, `$` and 🧭 and 📁 emoji, red Delete | `.btn-row` — flush, neutral, `.is-primary` on Estimate, `.is-danger` on Delete, no emoji, no tinted cell |

**Job card content.** Right now it reads `1246 Merriweather Ave / Cincinnati , OH /
Willow Creek · Crash Dummy`. Two problems: a stray space before the comma, and the
builder and job title are joined with a middle dot instead of the pipe. It should be:

```html
<b class="row__title">1246 Merriweather Avenue</b>
<span class="row__sub">Cincinnati, OH 45248</span>
<span class="row__sub">Willow Creek | Meriweather residence</span>
```

The ZIP is missing on nearly every card. Per the standard: if a piece is missing, **ask,
don't guess** — surface it as a `.tag` reading "ZIP missing" so the office can fill it,
rather than silently rendering a partial name.

Also: "Crash Dummy", "Test 7-5-26 751am", "PLAY WITH ME!", "Testing for confirm to
delete" — 214 hidden duplicates and a list of test records is the real reason this
screen feels bad. No amount of styling fixes it. Purge or archive them.

### 1b · Takeoffs list — the five things making it look bad

See `gvc-takeoffs-reference.html` for the finished version. The five:

1. **Plan uploads are rendered at low opacity** &mdash; the filenames are literally
   unreadable in the screenshot. Whatever `opacity` or grey is on that list, remove it.
   If those rows are queued and not yet actionable, say so with a `.tag` reading QUEUED;
   don't fade the text. **Nothing in this system is dimmed with opacity.**
2. **Every list row is a 9999px pill.** The full radius belongs on *controls* &mdash;
   buttons, chips, inputs. A 1000px-wide capsule reads as a button the user can't press,
   and the round ends waste ~40px at each edge. List rows are `.rowcard`
   (`--radius-md`). This is the single biggest visual fix on the screen.
3. **The pipeline is five floating circles**, four of them zero, taking a full band of
   vertical space to say nothing. Use `.pipe` &mdash; one joined strip, zeros muted,
   the live stage tinted green. Same information, a third of the height.
4. **Cards are ~60% empty.** Draft tag, address, city, builder, estimator, age &mdash;
   six short lines with `var(--space-5)` between each. Tighten to `var(--space-4)`
   padding and 1px between the three name lines; they are one block, not six.
5. **`+ New takeoff` is a full-width outlined pill** while `Continue 9999 keep
   testing…` &mdash; a resume link to a *test record* &mdash; sits in the header looking
   equally important. New takeoff is `.btn-primary`, normal width, in the card header
   next to Budget bid and From plans.

### 1c · The data is the other half of the problem

From the screenshots, actual live records:

```
Cincinnsti, OH          ← misspelled
Cincinnati , OH         ← space before the comma
HCC!?                   ← builder field
Recovered 1:16:22 PM    ← × many
Untitled
Testing 5-8-2026 V106
5672 Whetsel Ave (extra in basement)   ← scope note in the name field
Weiland / Wieland       ← same builder, two spellings
```

Plus 218 hidden duplicates. No stylesheet fixes that. Two changes:

- **A cleanup banner** at the top of the list &mdash; `.card-note` &mdash; reading
  "218 duplicate copies and 31 test records are hiding your real jobs" with a
  **Review and purge** action. Shown in the reference build.
- **Group the nameless records** under their own `No job attached` heading with
  `.rowcard-junk` (dashed, muted). A recovered autosave with no address should never
  sort into the same visual class as a real job.

**Validate on save, not on render.** If a takeoff has no ZIP, no builder or no job title,
show the `ZIP missing` `.tag-gold` and change the card's primary action from
`Estimate` to **Finish the name** &mdash; per the standard, ask, don't guess. Right now
those cards render `Cincinnati, OH` with no ZIP and no builder and look complete.

### 2 · Everything-else list

Rows are fine structurally. Convert to `.row` + `.row__main` + `.row__title` +
`.row__sub`, put the DRAFT badge in a `.tag`, and the timestamp in `.mono.faint`.
The list needs `.scroll-tall` so it stops running to 200+ rows down the page.

### 3 · Budget bid dialog

| What's there | What it should be |
| --- | --- |
| `GVC supplies · $1.87/SF` solid green vs `Builder supplies` outlined | Both are `.chip.chip-lg`; the selected one takes `.is-active` (gold). This is a **choice**, not an action — green is wrong here. |
| `9' ceilings` native `<select>` × 2 | `.chipset` — 8' / 9' / 10' / 12' visible |
| `Cathedral +0.3` checkbox | `.toggle` |
| Red `−` remove buttons | `.btn-icon.btn-danger` |
| `+ Add level` gold outline | `.btn.btn-sm.btn-dashed` |
| `Calculate` green, `Save` solid gold | `Calculate` = `.btn-primary`. `Save` = `.btn`. Two primaries in one row means neither reads as primary — pick one. |
| `💾` and `⟲` emoji | Lucide `save` / `rotate-ccw`, or nothing |
| Square inputs | `.input` (pills) |
| Field labels sentence-case | `.kicker` — mono, uppercase |
| The bare `9 / 1 / 16 / 9` garage inputs | Each needs a `.kicker` label. Right now nobody can tell what those four numbers are. |

### 4 · Job shell (rail + dark main)

The rail is light cream, the main column is near-black. **Pick one ground.** If the field
wants dark, set `data-theme="dark"` on `<html>` and let both sides follow it; the tokens
already handle it. A light rail against a dark body is the single most jarring thing in
these screenshots.

Rail itself is close to right. Convert to `.rail` / `.rail__head` / `.rail__job` /
`.progress` / `.nav-item`, and the gold ✓ marks become `.nav-item__done`.

The top bar's `UPDATE` is solid gold → `.btn.btn-primary`. The `⋯` is `.btn-icon`.

### 5 · Review — action buttons

```
SEND TO OFFICE     orange, full width     → .btn.btn-primary.btn-block
Copy All Data      green                  → .btn  (secondary — it copies, it doesn't send)
Monday — Project   purple                 → .btn
Monday — Payroll   purple                 → .btn
COLLAPSE ALL       neutral outline        → .btn.btn-sm.btn-ghost   ✓ already close
EXPAND ALL         green                  → .btn.btn-sm.btn-ghost
RE-SAVE TO DRIVE   green + 📁             → .btn
```

One green button per view. `SEND TO OFFICE` is it — and per the standard it should read
**"Approve and send to office"**, because a person presses send.

### 6 · Review — accordions

`TAKEOFF SUMMARY` teal · `PRINT / SAVE PDF` grey · `SEND TO OFFICE` green ·
`DRYWALL OUTPUTS` blue · `SALES` orange · `PAYROLL` blue · `MONDAY.COM EXPORT` red,
each with an emoji. Seven colors and seven emoji on one screen.

All seven become the same object: `.card` with a `.card-head`, the title in plain
`--color-text`, the subtitle in `.muted`, and a Lucide `chevron-down` on the right.
If one of them genuinely needs to stand out, give that one — and only one —
`.card-note` (the gold left edge).

Inside `TAKEOFF SUMMARY`, the three figure boxes are right: `.grid-metrics` with
`.kicker` labels and `.num.num-lg` figures. The board-type rows become `.row`, and
`Grand Total` gets `.card-foot`.

### 7 · Review — Board order / Bead order / Ready check

The three cards are good. Two fixes:

- **The Waste `<select>` is the last dropdown in the app.** Replace it:

```html
<div class="chipset">
  <span class="chipset__label kicker">Waste</span>
  <div class="chipset__opts">
    <button class="chip" aria-pressed="false">0%</button>
    <button class="chip is-active" aria-pressed="true">5%</button>
    <button class="chip" aria-pressed="false">10%</button>
    <button class="chip" aria-pressed="false">15%</button>
  </div>
</div>
```

- **Ready check** NOTE / WARN chips become `.tag` and `.tag-alert`.

### 8 · Review — Shadow check

Layout bug, not a style bug: the prose wraps into 8-character columns because the grid
gives the label track all the width. The patch file fixes it; the real fix is

```css
display: grid;
grid-template-columns: minmax(90px, auto) minmax(56px, auto) minmax(0, 1fr);
gap: var(--space-3);
align-items: baseline;
```

with `min-width: 0` on the prose cell.

Also: "vs 422 all 422 jobs on record jobs" is a broken string template — it reads
"422 all 422 … jobs jobs". Should be **"vs 422 jobs on record"**.

---

## 9 · The outputs section — the biggest structural problem

Screenshots 9&ndash;12 show it clearly. **The same toolbar repeats seven times.** Estimate
Sheet, Stocking Email, Subject, Body Only, Hang Crew, Scrap Crew and Finish Crew each get:

- their own `Edit / Email / PDF / Copy` row (4 buttons &times; 7 = 28 buttons)
- their own full-width green `&#10003; Confirm — ready to send` bar
- their own preview block

That is roughly 2,000px of vertical scroll of near-identical chrome. It is also where the
inconsistencies live: on Scrap Crew the Email button renders **blue**, on Finish Crew it
renders **blank white with no label**. Nobody notices a broken button in a wall of
identical buttons.

### Replace all seven with one Outputs card

One card. Chips pick the output; the toolbar, the preview and the confirm bar are single
instances that swap content. See `gvc-review-reference.html` &mdash; the pattern is:

```
Outputs                                              [2 of 7 confirmed]
Everything this takeoff produces. Pick one — one toolbar, one preview, one confirm.

OFFICE    [ Stocking email CONFIRMED ][ Office email DRAFT ][ Estimate sheet CONFIRMED ]
CREW      [ Hang DRAFT ][ Scrap DRAFT ][ Finish DRAFT ][ Materials SENT ][ + Add ]
LANGUAGE  [ Both ][ Espanol ][ English ]

[ Email ][ Copy ][ PDF ][ Edit ][ Reset ]              [MODIFIED] [CONFIRMED]

┌ .preview ┐

[ Confirmed — not sent · by Jordan Jul 25 ]        [ Mark sent ][ Undo ]
```

Wins: 28 buttons become 5. Seven confirm bars become one. Every output's state is
visible at a glance in the chip row instead of requiring a 2,000px scroll. And a broken
button can't hide.

**Keep:** the MODIFIED / CONFIRMED chips (they're good &mdash; just `.tag-gold` /
`.tag-green`), the "Email/Copy/PDF use your edited version" note, and the honest
`Mark sent` wording. Drop "(after the email actually leaves)" from the button and put it
in the subtitle &mdash; a button label shouldn't be a sentence.

### While you're in there

- **`CONFIRMED — NOT SENT` is a solid gold banner.** Gold is selection, not a fill. Use
  `.card-foot` with the state as text, or `.card-foot-ok` when it is actually sent.
- **Previews are set in the body serif.** They're tab-separated data &mdash; the Estimate
  Sheet columns don't line up at all. They must use `.preview` (JetBrains Mono). Align
  the columns with padding, not tabs.
- **Emoji:** the toolbars carry pencil, envelope, page, outbox, printer and warning
  glyphs. Lucide or nothing.
- **The address in every output** reads `9999 keep testing st / Brookville , IN` &mdash;
  lowercase, stray space before the comma, no ZIP, and `098` floating on its own line.
  Every generated document should render the standard:
  `9999 Keep Testing St, Brookville, IN 47012`, with Lot / Job `098` as a labelled field.

---

## Install

```html
<link rel="stylesheet" href="/styles/gvc-ui.css">
<link rel="stylesheet" href="/styles/app.css">        <!-- existing, on its way out -->
<link rel="stylesheet" href="/styles/gvc-v2-patch.css">
```

`gvc-v2-patch.css` is a bridge. It force-corrects the palette, the radii, the touch
targets and the two layout bugs so the app stops looking wrong today. As you convert each
screen to the `gvc-ui.css` classes, **delete the matching block from the patch.** When
the patch file is empty, the port is finished and you can drop `app.css` too.

---

## Priority order

1. **Delete the test records.** 214 hidden duplicates and "Crash Dummy" do more visual
   damage than any CSS.
2. Palette: orange → green, purple → neutral, gold → green on actions.
3. Section headers: seven colors and emoji → one neutral treatment.
4. The Waste `<select>` → chips.
5. One ground — light or dark, not both.
6. Job names to the standard, with ZIPs.
7. Convert screen by screen to `gvc-ui.css`, deleting patch blocks as you go.
