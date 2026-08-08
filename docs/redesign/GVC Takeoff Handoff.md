# GVC Takeoff — restyle handoff

Prerequisite: `gvc-ui.css` installed, `GVC Design Style.md` read. Every class below comes
from that stylesheet — write no new CSS. Reference design: `GVC Takeoff.dc.html`.

---

## 1. What changes

1. **The 14 step chips become a left rail**, grouped and always visible.
2. **The dark green ground becomes the Emerald light palette** — same tokens as the hub.
3. **Every dropdown becomes a `.chipset`** — waste %, Set all boards, truss spacing,
   taken-by, crew member, OHP.
4. **Every button becomes `.btn`**; green = action, gold = selected, brown-burgundy =
   destructive only.
5. **Crew texts get one standard bilingual format** and a language switch.

---

## 2. Left rail — replaces the step chip strip

```
TAKEOFF   ← kicker
1246 Meriweather Avenue, Cincinnati, OH 45248 | Willow Creek | Meriweather residence
108 boards · autosaved 7:41 AM
[███████░░░░░░░]  7 of 14

JOBS      Takeoffs
SETUP     Project ✓ · Scope ✓
MEASURE   Drywall ✓ 108 bd · Bead ✓ 41 pc · Framing · ACT · Insulation ·
          Paint ✓ 1200 SF · Demo · Patch · Equipment ✓
FINISH    Photos 4 · Estimate ✓ · Review
```

Use `.rail` / `.rail__head` / `.rail__job` / `.progress` / `.rail__nav` / `.rail__group`
/ `.nav-item`. Group headers are `.kicker .kicker-sm`. A completed step gets
`.nav-item__done` (gold ✓); a step with work gets `.nav-item__qty` (mono quantity).

> The `min-width: 0; overflow-x: hidden` on `.rail` and `overflow-wrap: anywhere` on
> `.rail__job` are load-bearing. Without them the long job name gives the sidebar a
> horizontal scrollbar. This is already in the CSS — don't override it.

The horizontal strip couldn't show progress, couldn't group and truncated. This does all
three and is a jump target from any step.

## 3. Top bar

`.topbar`, sticky. `.kicker` (`STEP 3 OF 14` / `PRICING` / `REVIEW`), page title,
`.btn.btn-primary.btn-sm` Save, `.btn-icon` overflow. One sentence of purpose sits under
the bar in the content column, not in the bar.

---

## 4. Screens

### Takeoffs list

`.btn-primary` New takeoff · `.chip` Budget bid / From plans → search `.input` →
`.grid-metrics` pipeline numbers → `.chipset` filters (Active / Final / Auto-saved / All)
→ job cards → "Everything else" `.row` list.

Job card follows the naming standard, decomposed but never reordered:

```html
<article class="card card-flush">
  <div class="card-pad stack" style="gap:var(--space-2)">
    <span class="tag">Draft</span>
    <b class="row__title">1246 Meriweather Avenue</b>
    <span class="row__sub">Cincinnati, OH 45248</span>
    <span class="row__sub">Willow Creek | Meriweather residence</span>
  </div>
  <div class="btn-row">
    <button class="is-primary">Estimate</button>
    <button>Duplicate</button>
    <button>Navigate</button>
    <button class="is-danger">Delete</button>
  </div>
</article>
```

### Project setup

Two-option **cards**, not radios, for "This takeoff is for" and "Drywall supply" — the
selected one carries `.card-note` plus the gold ring (`box-shadow: inset 0 0 0 1px
var(--color-accent)`). All inputs are `.field` + `.input`. Supplier contacts and Scope of
work are `.chipset` rows. `Start measuring →` is the one `.btn-primary` on the page.

### Measuring (Drywall; Framing / ACT / Insulation / Paint / Demo / Patch follow it)

- `.card.card-note` holds the instructions **and** the Waste % `.chipset`.
- Floor `.chipset` with a `.btn.btn-sm.btn-dashed` "+ Floor".
- One `.card` per surface (Ceilings / Exterior walls / Interior walls) with "Set all
  boards" `.chipset` and a `.btn-primary.btn-sm` "+ Add" in the `.card-head`.
- **Row grammar:** `.input-num` L × `.input-num` W · board `.chip` · label `.input` ·
  tag chips (FO / MD / RC1, `.is-active` when on) · "− Deduct" `.btn-sm` · remove
  `.btn-icon`. Wrap the row in `.cluster`.

> **Phone:** seven controls per row is too tight at 390px. Stack two-up under 620px —
> dimensions on line one, board + tags + deduct on line two — before this ships to the
> field.

### Bead count

`.stepper` at 46px with the count in 20px mono between, and `+ Full box (50)` /
`+ Full roll (100 LF)` as `.btn-sm` shortcuts on the right. Grouped: No-coat · Other
bead types · Arch beads.

### Estimate

Rate-card `.card-note` → "Include in estimate" `.chipset` → Internal/Customer `.seg` +
OHP `.chipset` → two rate columns (labor | material) where each row is name,
`.input-num` rate, unit, and the math line with the computed total in
`var(--color-primary)` → totals → **sign-off panel**.

The sign-off panel is the one dark-green block in the app: total in `.num.num-lg`,
`✓ Numbers are right` as a gold chip, Queue to portal / Sent to customer as outlined
buttons, and the "numbers changed since Jordan confirmed" warning underneath.

### Review

Blocker/warning counts as `.chipset` → ready-check `.row`s with BLOCKER / WARN / NOTE
`.tag`s → pricing notes → stocking email `.preview` → crew texts.

---

## 5. Crew texts — the part that needed the most work

**Before:** four stacked boxes, each with its own Edit/Email/PDF/Copy row and its own
green Confirm bar. Nothing scannable, no state, enormous scroll.

**After:** one card. Pick the message, see one message.

```
Crew texts                                    [+ Materials list]
In job order — hang, scrap, finish. One message at a time.

[ Hang crew CONFIRMED ][ Scrap crew DRAFT ][ Finish crew DRAFT ][ Materials list SENT ]

LANGUAGE  [ Both ][ Espanol ][ English ]

[ Text crew ][ Email ][ Edit ][ PDF ][ Copy ][ Reset ]

┌ .preview — mono, max 22rem, scrolls ┐

[ Confirmed — ready to send · by Jordan Jul 25 ]  [ Mark sent ][ Undo ]
  — or —
[ Not confirmed yet ]                             [ Confirm this message ]
```

Tabs are `.chip.chip-lg` with `.chip__state` carrying CONFIRMED / DRAFT / SENT, so you
see at a glance what's left. Footer is `.card-foot.card-foot-ok` when confirmed, plain
`.card-foot` when not.

### Message format — the standard

Spanish block, divider, English block. Same five bullets in both:

```
INICIO DE PROYECTO
• Direccion: 9999 Keep Testing St, Brookville, IN 47012
• Mapa:
https://www.google.com/maps/search/?api=1&query=9999+Keep+Testing+St+Brookville+IN+47012
• Alcance: Listo para colgar — 108 tablas
• Notas: Garaje 36 · 2do piso 36 · sotano 36. 2 andamios, 2 ventiladores, 1 generador.
• PM: Robert — 812-655-3845

Avisame si tienes preguntas.

———

PROJECT START
• Address: 9999 Keep Testing St, Brookville, IN 47012
• Maps:
https://www.google.com/maps/search/?api=1&query=9999+Keep+Testing+St+Brookville+IN+47012
• Scope: Ready to hang — 108 boards
• Notes: Garage 36 · 2nd floor 36 · basement 36. 2 scaffolds, 2 fans, 1 generator.
• PM: Robert — 812-655-3845

Let me know if any questions.
```

Builder:

```js
const PM = 'Robert — 812-655-3845';
const mapsUrl = (addr) =>
  'https://www.google.com/maps/search/?api=1&query=' +
  encodeURIComponent(addr).replace(/%20/g, '+');

const LABELS = {
  es: { addr:'Direccion', map:'Mapa', scope:'Alcance', notes:'Notas',
        close:'Avisame si tienes preguntas.' },
  en: { addr:'Address',   map:'Maps', scope:'Scope',   notes:'Notes',
        close:'Let me know if any questions.' },
};

function crewText(lang, { head, addr, scope, notes, pm = PM }) {
  const L = LABELS[lang];
  return [
    head,
    \`• \${L.addr}: \${addr}\`,
    \`• \${L.map}:\\n\${mapsUrl(addr)}\`,
    \`• \${L.scope}: \${scope}\`,
    \`• \${L.notes}: \${notes}\`,
    \`• PM: \${pm}\`,
    '',
    L.close,
  ].join('\\n');
}

const both = (es, en) => crewText('es', es) + '\\n\\n———\\n\\n' + crewText('en', en);
```

Headings per message:

| Message | Spanish | English |
| --- | --- | --- |
| Hang | INICIO DE PROYECTO | PROJECT START |
| Scrap | LISTO PARA RASPAR | READY TO SCRAP |
| Finish | LISTO PARA ACABADO | READY TO FINISH |
| Materials | MATERIAL EN SITIO | MATERIALS ON SITE |

Rules:

- **The address line is the address only** — street, city, ST, ZIP. No builder, no job
  title. The crew needs a maps link, not the contract. (The stocking email and all app
  chrome use the full `address | builder | job title` standard.)
- The maps URL is generated from the address; never hand-typed.
- Copy / Email / PDF export whatever the Language switch is showing.
- Plain text only — no markdown, no emoji. It's going into a text message.

---

## 6. Checklist

1. No dropdowns left. Waste %, board size, truss spacing, taken-by, crew member and OHP
   are all `.chipset` groups.
2. No dark-green page grounds except the estimate sign-off panel.
3. Every target ≥44px; the bead `.stepper` at 46px.
4. `.rail` keeps `min-width: 0; overflow-x: hidden`; `.rail__job` keeps
   `overflow-wrap: anywhere`.
5. Measuring rows stack two-up under 620px.
6. Job names follow `[Street], [City], [ST] [ZIP] | [Builder] | [Job Title]`.
7. Nothing sends itself — Confirm, then Mark sent, both by a person.
