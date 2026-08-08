# Status Picker — handoff

Prerequisite: `gvc-ui.css` installed. Reference design: `Job Check Status Picker.dc.html`.
This replaces the wall of chips on Job Check with a one-value-per-field summary that opens
into a searchable, grouped picker.

---

## 1. The pattern

**Closed (default).** Each status column is one row: label, the current value as a gold
chip, a muted hint, and a `Change` button. Three rows total — no scrolling.

**Open.** Tapping `Change` reveals, in this order:

1. **Search** — type-to-find across all values of that column.
2. **Next up** — the 2–3 values that follow the current one in the real workflow, as
   `.chip-next` (green). This is the 90% case: one tap, done.
3. **Phase groups** — everything else, collapsed. Headers show a count; the group holding
   the current value auto-expands and carries a gold dot.

Selecting a value closes the field and clears the search. Tapping the active value clears it.

**Color:** gold = currently selected · green = suggested next · neutral = everything else.
Never color a chip by its monday color — that is what makes the wall unreadable.

---

## 2. Data — group your columns once

The only real work. One config, keyed by column, listing phase groups in workflow order.
Order matters twice: it drives group order *and* the "Next up" suggestions.

```js
export const STATUS_GROUPS = {
  stage: [
    ['Pipeline',        ['Upcoming', 'Estimating', 'Draft Estimate', 'Sent for Takeoff', 'Site Takeoff Needed', 'First Priority', 'Second Priority']],
    ['Materials',       ['Stocking', 'GVC Material D.O.', 'Send Material Order', 'Ready to Hang']],
    ['Framing',         ['Framing', 'Framing Track Layout', 'Framing Studs', 'Framing Blocking', 'Framing Punch out', 'Insulation Install']],
    ['Hang',            ['Hanging', 'Pre-Rock', 'Hold for Heat']],
    ['Finish',          ['Scrapping', 'Finishing', 'Fire Tape', 'Touch up - Sand']],
    ['Ceilings & doors',['ACT', 'Installing doors']],
    ['Service',         ['Touch Up/Service', 'Punchout List', 'Stuck']],
    ['Closeout',        ['Ready to Invoice', 'Complete']],
    ['Not a job stage', ['Meeting', 'Admin', 'Personal Item', 'OOO-Vacation']],
  ],
  detail: [
    ['Estimating', ['Ops Team', 'Site Take-off Needed', 'Onsite - Takeoff', 'Estimate Needed']],
    ['Materials',  ['Order Supplies', 'Delivery Scheduled']],
    ['Framing',    ['Layout/RC1', 'Track Layout', 'Studs', 'Blocking', 'Framing Punch Out']],
    ['Hang',       ['Hang Scheduled', 'Hang Started', 'Hang < 50%', 'Hang > 50%', 'Hang Complete']],
    ['Scrap',      ['Scrap Scheduled', 'Scrap Started', 'Scrap Complete', 'Ready for finisher']],
    ['Finish',     ['Tape', 'Bed/2nd Coat', '3rd/Skim Coat', 'Skim/Skimskim', 'Sand', 'Clean Out/Final Check']],
    ['Paint',      ['Paint Scheduled', 'Prime Coat', '1st Coat', '2nd Coat (Paint)', 'Paint Complete']],
    ['ACT & tile', ['ACT Scheduled', 'Grid Layout', 'Grid Install', 'Tile Install']],
    ['Service',    ['Touch Up Scheduled', 'Touch Up Started', 'Service Scheduled']],
    ['Billing',    ['Invoice Approved to Send']],
    ['Other',      ['Personal']],
  ],
  blocked: [
    ['Status', ['Clear', 'Blocked', 'Hold for Heat', 'Waiting on GC', 'Waiting on Materials', 'Jordan']],
  ],
};

export const QUICK = { blocked: ['Blocked', 'Waiting on GC', 'Waiting on Materials'] };

const flat = (key) => STATUS_GROUPS[key].flatMap(([, items]) => items);

export function suggestionsFor(key, current) {
  if (QUICK[key]) return QUICK[key].filter((v) => v !== current);
  const list = flat(key);
  const i = list.indexOf(current);
  return i < 0 ? list.slice(0, 4) : list.slice(i + 1, i + 4);
}
```

Validate against monday on boot so a new column value can't silently vanish:

```js
const known = new Set(flat('stage'));
const missing = mondayStageValues.filter((v) => !known.has(v));
if (missing.length) console.warn('Ungrouped stage values:', missing);
```

Push anything ungrouped into a trailing `['Other', missing]` group so it stays pickable.

---

## 3. Markup

```html
<section class="card card-flush sp" data-field="stage">

  <div class="row" style="border-bottom:0">
    <div class="row__main">
      <span class="kicker">Stage</span>
      <span class="cluster">
        <button class="chip is-active" data-clear>Hanging</button>
        <em class="faint" style="font-size:var(--text-xs)">set 3 days ago</em>
      </span>
    </div>
    <button class="btn btn-sm row__end" data-toggle>Change</button>
  </div>

  <div class="sp__panel card-foot stack" hidden>
    <input class="input" type="search" placeholder="Type to find a status">

    <div class="stack" style="gap:var(--space-2)" data-suggestions>
      <span class="kicker kicker-sm">Next up</span>
      <div class="cluster">
        <button class="chip chip-next" data-value="Pre-Rock">Pre-Rock</button>
      </div>
    </div>

    <div class="sp__groups">
      <div class="sp__group" data-open="true">
        <button class="sp__ghead" data-group>
          <span class="sp__gname">Hang</span>
          <i class="sp__gdot" aria-hidden="true"></i>
          <span class="mono faint" style="font-size:var(--text-xs)">3</span>
          <span class="faint sp__gcaret">–</span>
        </button>
        <div class="cluster sp__opts">
          <button class="chip is-active" data-value="Hanging">Hanging</button>
          <button class="chip" data-value="Pre-Rock">Pre-Rock</button>
          <button class="chip" data-value="Hold for Heat">Hold for Heat</button>
        </div>
      </div>
    </div>
  </div>
</section>
```

The gold dot renders only on the group holding the current value.

## 4. The only new CSS

Everything else is `gvc-ui.css`. These five rules are picker-specific:

```css
.sp__panel[hidden] { display: none; }
.sp__panel { flex-direction: column; align-items: stretch; }
.sp__group { border-top: 1px solid var(--color-divider); }
.sp__ghead {
  width: 100%; display: flex; align-items: center; gap: var(--space-2);
  min-height: 44px; padding: 8px 2px;
  background: none; border: 0; font: inherit; text-align: left; cursor: pointer;
}
.sp__gname { flex: 1; font-size: var(--text-sm); font-weight: 600; color: var(--color-text); }
.sp__ghead:hover .sp__gname { color: var(--color-primary); }
.sp__gdot { width: 7px; height: 7px; border-radius: var(--radius-full); background: var(--color-accent); }
.sp__gcaret { width: 12px; text-align: center; font-size: 11px; }
.sp__group[data-open='false'] .sp__opts { display: none; }
.sp__group .sp__opts { padding: 2px 0 var(--space-3); }
```

---

## 5. Behavior

```js
function wireStatusPicker(root, { getValue, setValue }) {
  root.addEventListener('click', (e) => {
    const sec = e.target.closest('.sp');
    if (!sec) return;
    const field = sec.dataset.field;

    if (e.target.closest('[data-toggle]')) { togglePanel(sec); return; }

    const ghead = e.target.closest('[data-group]');
    if (ghead) {
      const g = ghead.closest('.sp__group');
      g.dataset.open = g.dataset.open === 'true' ? 'false' : 'true';
      ghead.querySelector('.sp__gcaret').textContent = g.dataset.open === 'true' ? '–' : '+';
      return;
    }

    const opt = e.target.closest('[data-value], [data-clear]');
    if (opt) {
      const next = opt.dataset.clear ? '' : opt.dataset.value;
      setValue(field, next === getValue(field) ? '' : next);   // tap active to clear
      closePanel(sec);
    }
  });

  root.addEventListener('input', (e) => {
    const input = e.target.closest('.sp__panel .input');
    if (!input) return;
    const sec = input.closest('.sp');
    const q = input.value.trim().toLowerCase();
    sec.querySelector('[data-suggestions]').hidden = !!q;
    for (const g of sec.querySelectorAll('.sp__group')) {
      let shown = 0;
      for (const chip of g.querySelectorAll('.chip')) {
        const hit = !q || chip.textContent.toLowerCase().includes(q);
        chip.hidden = !hit;
        if (hit) shown++;
      }
      g.hidden = shown === 0;
      if (q) g.dataset.open = 'true';
      g.querySelector('.mono').textContent = shown;
    }
  });
}

function togglePanel(sec) {
  const panel = sec.querySelector('.sp__panel');
  panel.hidden = !panel.hidden;
  sec.querySelector('[data-toggle]').textContent = panel.hidden ? 'Change' : 'Done';
  if (!panel.hidden) {
    const active = panel.querySelector('.chip.is-active');
    active?.closest('.sp__group')?.setAttribute('data-open', 'true');
    panel.querySelector('.input').focus();
  }
}
```

Write-back: call the monday mutation inside `setValue`, update local state optimistically,
roll back on failure — the chip is the only thing that has to change.

Accessibility: chips are `<button aria-pressed>`, group headers `aria-expanded`, the panel
`aria-hidden` when closed.

---

## 6. If the rows don't line up

Symptom: label, value chip and `Change` run together on one line and `Change` drops
underneath.

Cause: the row isn't a flex row, or `.row__main` isn't a flex **column**.

Three rules keep every row identical:

1. **`.row__main` is `flex: 1 1 auto` with `min-width: 0`** — it takes the leftover width,
   so `Change` always lands hard right at the same x-position no matter how long the label
   or value is. That is what makes the column of buttons line up.
2. **The Change button is `flex: 0 0 auto`** (`.row__end`) — never shrinks, never wraps.
3. **Labels use `.kicker`**, same as `STAGE COMPLETION` further down the form. Mixing
   kickers and sentence-case body labels is why the top half of the card reads differently
   from the bottom half.

All three are already in `gvc-ui.css` — you get them by using `.row`, `.row__main`,
`.row__end` and `.kicker` instead of hand-rolling the row.

### The date fields belong to the same rhythm

`STAGE COMPLETION` / `FULL COMPLETION` / `START DATE` use the same `.field` + `.kicker`
+ `.input` grammar. Square-cornered date inputs beside fully rounded chips is the other
thing making the card look unfinished — `.input` rounds them for you.

---

## 7. Fix the data too

The UI hides the problem; the column still has it. In monday's Stage column:

- **Duplicates** — `Upcoming`, `Estimating`, `Finishing`, `Touch Up/Service` each appear twice.
- **Wrong column** — `Framing Studs`, `Framing Blocking`, `Pre-Rock`, `Fire Tape`,
  `Touch up - Sand` are Stage *Detail* values.
- **Not job stages** — `Meeting`, `Admin`, `Personal Item`, `OOO-Vacation` belong on a
  separate calendar/type column.

Retiring those cuts the Stage list by roughly a third and makes "Next up" much more accurate.
