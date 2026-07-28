# GVC Portal — design brief for Claude (paste this into claude.ai)

**How to use this file:** open a new chat at claude.ai, paste everything between the
`─── PROMPT ───` markers, and send. Claude will build a design-system artifact you can
open on your phone and share. Iterate there. When you approve it, bring the artifact URL
back to Claude Code and it gets ported into `web/gvc.css`, which every portal page links.

Why a mockup first: today each of the ~12 portal pages carries its own private copy of the
CSS. That means a redesign costs 12 edits and the pages drift apart (they already have —
`activity.html` was carrying styles for form cards it doesn't contain). Designing once,
then extracting one shared stylesheet, fixes both problems permanently.

---

─── PROMPT ───

You're designing the visual system for an internal web portal used by a drywall and
interior-finishes contractor. I need a **design-system artifact** I can review on my phone,
share with my team, and then hand to a developer to implement.

## The company

Green Valley Contractors (GVC) — Brookville, Indiana, serving the Cincinnati/Dayton area.
Founded 1986, family-owned. Trades: drywall, metal framing, acoustic ceiling tile (ACT),
insulation, paint, demo, patch. Roughly a dozen people. We bid, build, and bill commercial
and residential work — everything from a single-family basement to a $1.6M office fit-out.

The tone should feel like the company: **established, precise, unfussy, trustworthy.** Not
a startup SaaS. Not construction-clip-art either — no hard hats, no caution-tape yellow, no
"blueprint" motifs. Think a well-run contractor's office: clean, sturdy, legible, a little
traditional. Quiet confidence.

## Who uses it, and where

Three very different contexts — the design has to serve all of them:

1. **Andrea (office manager)** — Windows desktop, all day, dense screens. Creates invoices
   and estimates, chases receivables, records check payments. She needs information density
   and fast scanning. She is not technical.
2. **Field crew and PMs (Mark, Robert, Ethan)** — phones, outdoors, gloves, bright sun,
   sometimes one-handed. They tap through a job checklist. They need big targets, high
   contrast, and zero ambiguity about what saved.
3. **Jordan (owner) and Jake (sales/PM)** — phone and laptop, quick checks between site
   visits. Skimming: what went out, what's stuck, what needs me.

**Mobile is not the afterthought.** Assume a phone in bright daylight is a primary case.

## The existing brand palette — keep these exact values

These are already in use across the portal and on printed documents. Treat them as fixed
inputs; build the system around them rather than replacing them.

```
Forest green (primary)   #235339
Deep green (hover/dark)  #1A3E2B
Gold (accent)            #C9A24B
Gold dark                #96763B
Ink (body text)          #1C1917
Muted text               #78716C
Hairline / borders       #E7E0CE
Page background          #FAFAF8
Green tint (surfaces)    #F2F7EE
Sand tint (surfaces)     #F3EBDD
```

Established conventions worth keeping (improve them, don't discard them):
- A **forest-green header bar with a 2.5px gold rule** under it, on every page.
- A **serif wordmark** (currently Georgia) with a circular "G" monogram — gold on dark green.
- **Gold is reserved for commitment actions** — the button that actually sends, saves, or
  records. Everything else is green or neutral. Gold should feel earned, not decorative.
- Body copy is a system sans-serif.

I'd like you to propose (with reasoning) refinements to type scale, spacing rhythm, border
radius, elevation, and how green/gold/neutrals divide the work. Push the craft — I care a
lot about the finish details. But stay inside the palette above.

## What the portal does — the screens to design

Every page shares one header (home link · page title · signed-in user · sign-out).

**Hub** — the landing page. A grid of tiles, one per tool, each with a name, one-line
description, and a Live / Coming-soon state. Tiles are filtered by the user's permissions.

**Invoice generator** — a long form (customer, job, line items, payment terms, notes),
a "Generate preview" step that renders a PDF, then a commitment step. Afterward it shows a
result panel with links (Stripe payment page, Gmail draft, Drive folder) and per-step
outcomes. This is the densest, most-used screen.

**Estimate generator** — same shape as the invoice form, plus a trade/scope picker: check a
trade, an editable description and price appear, saving it adds a line item.

**Change order** and **COI (certificate of insurance)** — smaller siblings of the same form
pattern.

**Paid by check** — upload a photo of a check, OCR reads it, the user confirms extracted
fields in a modal, picks which invoice(s) it pays (searchable list, running total vs. check
amount), and records it.

**Job Check** — the field crew screen. Pick a job from a searchable list, then a checklist
of trade-progress fields (dropdowns of colored statuses, dates, notes), and a sticky bottom
save bar showing how many changes are pending. Phone-first, 48px minimum targets.

**Activity** — an audit ledger: a filter bar and a table of everything the portal produced
(time, what it was, document number, customer, amount, who it was emailed to, per-step
outcome chips, result). Needs to work as a dense table on desktop and stacked cards on a
phone.

**Lien Watch** — deadline cards per job, sorted most-urgent-first, with severity levels
(ok / warning / critical / missed / unknown) and a legal-disclaimer banner.

**Admin** — a user list with permission checkboxes.

## What I want back

A **single self-contained HTML artifact** containing:

1. **A design-system section** — the palette with roles and usage rules, type scale with
   live specimens, spacing scale, radius/elevation, and every component rendered in all its
   states:
   - buttons (primary, secondary/ghost, gold commitment, disabled, loading)
   - form fields (text, number, date, textarea, select, checkbox) — default, focus, error,
     disabled — at both desktop and 48px field sizes
   - cards and section headers
   - data table (dense desktop) and its stacked-card mobile equivalent
   - status pills and chips, including a success/warning/danger/neutral set
   - banners: info, success, warning, error
   - modal, sticky save bar, empty state, loading state, and a "something failed" state
2. **Three full screen mockups** built from those components, with realistic GVC content
   (real-sounding job names, customers, dollar amounts — not lorem ipsum):
   - the **Hub**
   - the **Invoice generator** (form + result panel)
   - the **Job Check** screen shown at phone width
3. Short notes explaining the reasoning behind the important choices.

## Hard constraints — the implementation can't work around these

- **Plain HTML and CSS only.** No React, no Tailwind, no build step, no CSS preprocessor.
  The result must be liftable into a single static `gvc.css` file.
- **Nothing loaded from the internet.** No Google Fonts, no CDN, no icon packs, no remote
  images. System font stacks only. Any icon must be an inline SVG or a text glyph. (The
  production pages run under a strict content policy that blocks external requests.)
- **Use CSS custom properties for every token** (`--gvc-green`, `--gvc-space-3`, etc.) and
  define them once in `:root`. Component rules must reference tokens, never raw hex.
- **Accessible:** 4.5:1 contrast minimum on text, visible keyboard focus on every
  interactive element, real `<label>`s, 44px minimum touch targets (48px in field tools).
- **Responsive from 360px to 1440px**, no horizontal page scroll. Wide tables scroll inside
  their own container or restack.
- **Light mode only** for now.
- Don't invent a new logo. The circular "G" monogram plus the company name is the mark.

## How I'll judge it

Whether it looks like it belongs to a 40-year-old contracting company that takes its work
seriously — and whether Andrea can scan an invoice list quickly on a Windows monitor while
Mark can update a job status one-handed in a driveway. Craft in the details matters to me:
alignment, rhythm, restraint with color, the way a failed step reads at a glance.

─── END PROMPT ───

---

## After you approve the design

Bring the artifact URL back to Claude Code. Porting plan, in order:

1. Extract the approved tokens + components into `web/gvc.css` (new file, served from
   `web/` which is already `COPY`'d wholesale into the container — no Dockerfile change).
2. Convert pages one at a time, starting with `hub.html` (smallest, highest visibility),
   then `activity.html` and `jobcheck.html`, then the four big forms. Each conversion drops
   its private `<style>` block and links the shared sheet.
3. Bump the version marker in the hub footer (`Portal rN · date`) on every user-visible
   change — the repo's standing rule.
4. Deploy once per batch, not per page; each deploy is one Cloud Run revision and rolls
   back in one click.

**Deliberately out of scope:** the customer-facing PDF templates
(`templates/*.html.j2` — invoice, estimate, change order). Those are printed documents a
customer sees, the layouts are approved, and they're waiting on real brand vector files.
Separate project, higher stakes.
