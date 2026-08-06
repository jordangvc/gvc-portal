# Handoff — Field Manual track, 30 July 2026

Companion to `docs/HANDOFF-2026-07-30.md`, which covers the portal/design-system
track and explicitly leaves this one alone ("`web/fieldguide.html` — belongs to
the other session, not mine. Leave it"). This document is that gap.

Read the other handoff first for deploy mechanics, the multi-writer rule, and
the Monday/Slack/Drive gotchas. Nothing here repeats them.

---

## 1. Where this track stands

| | |
|---|---|
| Live revision | `gvc-invoice-00077-psq` |
| URL | `portal.greenvalleycontractors.com/ui/fieldguide` |
| Live and working | 21 procedures, Plain/Full toggle, Component Index, Glossary, **resumable per-job checklists** |
| Committed, **NOT deployed** | `c26be8a` — embedded Montserrat/Lato, reference palette, first three diagrams |

**Why `c26be8a` was not deployed:** `--source .` ships whatever is on disk, and
the tree also holds the other session's in-flight `gvc.css` migration of
`hub.html` plus a new `/ui/gvc.css` route. Deploying from here would ship their
work mid-edit. Coordinate, then deploy.

Page shape as of `c26be8a`: 25 sections (21 procedures + home/index/glossary/
sources), 346 checkboxes, 34 expert blocks, 45 Component Index jumps, 3 diagrams.

---

## 2. What the Field Manual actually is

Written procedures for every trade GVC lists, plus the three estimate-catalog
stubs. Two registers in one page:

- **Plain** — the procedure in everyday language. Every doc opens with an
  "In plain words" box that's the whole thing in three or four sentences.
- **Full detail** — 34 `.expert` blocks with the assemblies, product options and
  specifications a lead needs. A toggle in the control bar shows/hides them and
  the choice persists per device.

The drywall docs are **deliberately named and ordered to match the Job Check
stage columns** (Hanging Status → Scrapping Status → Taped → 2nd Bed → 3rd Coat
→ Sanded → Text/Skim) so a crew member moves between the two tools without
translating. Do not rename them independently of Job Check.

**Access is the `fieldguide` feature, which is in `BASELINE`** alongside
`timeoff` — every provisioned user gets it with no admin action. That was
Jordan's call: it holds no customer or financial data and the point is one-tap
reach from a phone.

### The sourcing discipline — do not quietly break this

Every section ends with a "Where this came from" block, and `Sources & Method`
separates three tiers:

- **Standard** — GA-214/GA-216, AISI S100/S211, OSHA citations, manufacturer
  instructions, the listed assembly. These govern.
- **Benchmark** — vendor and forum production claims, flagged as ranges, never
  guarantees.
- **GVC practice** — ours, owned by us.

**There are deliberately NO production rates in the manual.** Published figures
disagree by 2× or more, and publishing one would hand estimators a number that
looks authoritative and isn't. Job Check stage data is the real source once
there's history. If someone asks you to add rates, that's the conversation.

Four docs are **outside GVC's listed trade scope** and say so in their own
provenance blocks: Cabinets, Doors & Hardware, FRP, Tectum. They carry standard
published practice, not our hard-won experience. They need a read from someone
who does that trade daily before they're anyone's only reference.

---

## 3. Checklist runs — architecture, and two locked decisions

A crew member starts a procedure checklist against a Monday job, works it, and
anyone on the crew can resume it. Mark starts the hang checklist, Robert
finishes it after lunch.

**Built by copying `subsystems/estimate/drafts.py`, not by inventing.** Same GCS
object + generation guard + last-writer-wins on `updated_at`, same defensive
caps, same no-op-on-stale. If you fix a bug in one, look at the other.

- `subsystems/fieldguide/runs.py` — store, object `portal/fieldguide-runs.json`
- Routes: `GET|PUT|DELETE /ui/api/fieldguide/runs[/{id}]`, plus
  `GET /ui/api/fieldguide/jobs` which **reuses `jobcheck_flow.list_active_jobs()`
  verbatim** so both tools always show the same job list.

### Locked decision 1 — no Monday writeback

Job Check remains the **only** writer of the Projects-board stage columns. Two
features writing the same column is how a stale run silently regresses a status
the office set. A finished run here is a record that the crew worked the
checklist, nothing more. Do not "improve" this without Jordan reopening it.

### Locked decision 2 — runs are shared, not private

A run that died with whoever's phone started it would not survive how the crews
actually work.

### Offline is the normal case, not the edge case

Jobsite signal is bad. `localStorage` is the working copy, the server is the
shared copy. The sync chip says **"Saved on phone only"** when that is the truth
rather than faking success. A stale write (older `updated_at`) comes back
`stale: true` and is **dropped**, so a phone that was offline for an hour cannot
roll back a run someone else has since advanced.

### Step identity — the non-obvious part

Each checkbox key is a **hash of (section id + step text)**, not a position
index. So adding or reordering a step does not shift everyone's saved marks. A
**reworded** step loses its mark — which fails to *unchecked*, never to
*wrongly-checked*. That's the safe direction for a checklist and it was chosen
deliberately. Keep it that way when you edit step wording.

With no active run the page behaves as it always did: marks save to that device.

---

## 4. The design work, and what's half-done

Jordan supplied a reference guide (`Metal Framing Layout Guide.html` +
`Framing guide design.pdf` in his Downloads) and asked for "this guide's style
with your guide's data, and have diagrams of."

**Extracted design language** — the reference is already on GVC brand:

| Role | Value |
|---|---|
| Structural stroke | `#548427` |
| Accent | `#7EB438` |
| Gold | `#B6985A` |
| Fill / track | `#DDD9CD`, `#EAE7DC` |
| Ground | `#FBFAF5` warm cream |
| Ink | `#141613`, `#3C4038` |
| Type | Montserrat display · Lato body |

**Diagram vocabulary**, read off the PDF's embedded images: green stroke on
structural members, grey fill for track, **dashed green centrelines for the
module**, **diagonal hatch for cut members**. All inline SVG via a `<pattern>`
def — no external assets, prints, scales on a phone. CSS classes are
`.dg-member .dg-track .dg-center .dg-dim .dg-lead .dg-hatchline .dg-t`.

**Done (3):** framing 16" o.c. plan · hanging butt-vs-tapered section ·
shaftwall liner-stud-liner plan.

**Queued (~9–12):** the four deflection track types side by side · CRC through
knockouts with the anchorage everyone omits · strap-and-block · Z-furring over
masonry · tall-wall levers · coat progression · ACT grid with border-tile
layout · California patch flange · FRP expansion gaps · HW-D joint anatomy.

⚠ **Validate every SVG as well-formed XML before committing.** A malformed one
renders as *nothing*, silently — no console error, no visible break in review.
The check that catches it:

```bash
python -c "import re,xml.etree.ElementTree as ET,pathlib; s=pathlib.Path('web/fieldguide.html').read_text(encoding='utf8'); [ET.fromstring(v) for v in re.findall(r'<svg\b.*?</svg>', s, re.S)]; print('all SVGs well-formed')"
```

### Fonts are inlined, and that was on purpose

Montserrat 600/700 + Lato 400/700, base64 woff2, latin subset — 82KB raw,
110KB encoded, in the page's own `<style>`. **Not** served from a route, because
`/ui/gvc.css` was uncommitted work in another session at the time and a page
carrying its own faces cannot break on a route that doesn't exist yet.

When the `gvc.css` migration reaches this page: **lift the `@font-face` block
into `gvc.css` verbatim and delete it from here.** Every other page gets the
faces for free and the manual stops carrying 110KB.

---

## 5. ⚠ The source-of-truth seam — read before you edit

Early in the build, `web/fieldguide.html` was **generated** from a scratchpad
file and pushed into the repo. **That is no longer true.** The repo file is now
canonical and carries work the scratchpad does not: the checklist-run engine,
the embedded fonts, the palette, and the diagrams.

**Regenerating from the scratchpad would silently clobber all of it.** Edit
`web/fieldguide.html` directly. For a targeted change in a 416KB file, a scripted
splice on a unique anchor beats read-then-edit and survives concurrent writers —
see `scratchpad/add_diagrams.py` for the pattern (it asserts the anchor is unique
and aborts without writing if it isn't).

---

## 6. Blocked on Jordan — nobody else can answer these

1. **FRP / Doors & Hardware / Tectum scope.** All three are *title-only* stubs in
   the estimate scope catalog (`portal/estimate/scope-catalog.json`), outstanding
   since 2026-07-14. Each doc asks the same three: furnish or install-only, who
   preps the substrate, who owns the tail (sealant / keying / field painting).
   **Do not let these docs be used for pricing until answered.** Answering also
   fills the catalog scope text.
   *Jordan's answer so far: "do what you can and I'll give more input" — so
   defensible defaults, clearly marked as assumptions, are wanted.*

2. **Written safety programs — he confirmed GVC has none and needs them.**
   Hazcom, respiratory protection with fit testing, and a silica exposure control
   plan. The Demolition and Insulation docs both reference them. These are
   compliance documents: a wrong one documents non-compliance, so they want
   careful drafting and a real review, not a fast pass.

3. **Who owns `gvc.css`, and can dark mode go in it?** Jordan said yes to dark
   mode and asked for a **toggle** (OS preference as default, control to
   override, remembered per person). `gvc.css` has **zero** dark hooks today and
   is now the file every page will depend on — so this is a portal-wide change to
   a file another session is actively building against. Settle ownership first.

4. **Trade review** of Cabinets / Doors / FRP / Tectum by someone who installs
   them daily.

5. **Is the active-job list acceptable at baseline?** `GET /ui/api/fieldguide/jobs`
   is gated by `fieldguide`, which is baseline — so every signed-in employee can
   list active job names and addresses. Judged not confidential and necessary for
   crews to pick their job, but it's Jordan's call. To narrow it, gate that one
   route on `jobcheck` and have crew pick from a text field.

---

## 7. Queued build work

### Done 2026-08-06 — stand-behind slice (r37) + Job Check deep-links (r38)
**11 procedures** grounded in Job Start / Job Check / GA-214 / existing manual
rules: Window Returns, Ceiling Finish Decision, Sound Walls & RC, Level 5 /
Text/Skim, RFI Field, Talking to the GC, Ops Lead, Receiving & Freight, Scaffold
& Lifts, Residential Field Habits, Skill Ladder.
**Job Check How-to links:** coat/skim/clean-out + Ops logistics anchors wired;
`status_19` remains board-scoped. Hub **r38**. Deploy via merge → Actions.

### Done 2026-08-06 — ops / onboarding / leadership slice (r36)
**21 procedures** — First Week through Inspection Photos (see prior note). Live
on `gvc-invoice-00133-hq4`. Hub **r36**.

### Still queued
- The remaining 9–12 diagrams (list in §4).
- Migrate the page onto `gvc.css` once §6.3 is settled.
- Photos for hard-to-diagram defects.
- **AI roadmap** under `#ai-roadmap` (document only until prioritized).
- Optional later: abuse-board, plywood-backing, auto-tools deep dive.

---

## 8. Track-specific gotchas

- **A malformed inline SVG renders as nothing, silently.** Always parse-check.
- **Don't `git add -A`.** Two sessions share this folder. Stage explicit paths.
  This track owns exactly: `web/fieldguide.html`, `subsystems/fieldguide/`, the
  `/ui/api/fieldguide/*` routes in `app/service.py`, and `docs/HANDOFF-field-manual.md`.
- **`gcloud` is not on the Bash PATH** on this box — PowerShell, full path. (Also
  in the other handoff.)
- **Auth-gated route smoke test:** a page route returns **303** when it exists,
  an API route returns **401**, and a missing route returns **404**. Always hit a
  deliberately fake path as the control, or the 303 proves nothing.
- **Deploying ships the whole disk.** Check `git status` for another session's
  work before running `--source .`.

---

## 9. If a human is picking this up rather than an agent

The manual is *already usable* — it's live and every employee can reach it with
no provisioning. What it needs from the field is **corrections**, and the fastest
route is: read a procedure in Full detail, and flag anything that doesn't match
how GVC actually does it. The framing and drywall pages carry our practice; the
four out-of-scope pages carry published practice and are the likeliest to be
wrong.

The single most valuable thing anyone can contribute is **real production rates
from our own jobs**, per stage. The manual deliberately has none, and Job Check
stage completions are the raw material.
