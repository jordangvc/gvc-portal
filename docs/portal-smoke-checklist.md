# Portal smoke checklist (Andrea + Jake)

**Portal:** https://portal.greenvalleycontractors.com  
**Expect hub footer:** Portal **r53** (or newer). If you see older than r50, tell Jordan before deep-testing.  
**Purpose:** Quick pass/fail on every live tool — catch “looks fine to us, broken for the office” early.  
**Time:** ~20–30 minutes each. Do it on a phone *and* a laptop if you can (phone first for field tools).

Reply to Jordan with: tool name · ✅ / ❌ / ⏭ skipped · one-line note if anything felt wrong.

---

## Ground rules

1. **Prefer Preview / dry-run.** Only hit **Accept / Finalize / Send to Ops / Record** when the checklist says it’s OK — or when you’re doing real work anyway.
2. If a tile is **missing** on the hub, stop and tell Jordan (grants). Don’t assume the tool is broken.
3. Wrong data or a scary error → screenshot + which job/name you used. Don’t keep retrying a live write.
4. **Mark is sitting this round out** (capacity). Job Check can wait for him unless Jake has 2 minutes.
5. **Job names (locked):** `[Street Number Name], [City], [ST] [ZIP] | [Builder] | [Job Title]` — three pipe parts. Residential Job Title = `{Last} residence`. Commercial = real business name (don’t invent from the GC). If a field is wrong/blank, ask — don’t guess.

---

## Who does what

| Person | Focus |
|---|---|
| **Andrea** | Money + office: Hub home (billing needs), Estimate, Billing Hub, Invoice, Change Order, Paid by Check, COI, Activity, Time Off |
| **Jake** | Sales + handoff: Hub home, Morning Brief, Takeoff → Estimate, Estimate, Job Start, Field Manual, Job Check (light) |
| **Jordan** (optional) | Admin grants glance, Owner Pulse / GM Morning if you use them, Lien Watch (data still catch-up) |

Overlap is fine — two ✅s on Estimate is better than one.

---

## A — Everyone (2–3 min)

| # | Check | Pass looks like |
|---|---|---|
| A1 | Sign in at the portal URL | Your email shows; **role home** loads (not a blank page) |
| A2 | Hub home on phone | Needs / next actions readable; bottom dock usable; no horizontal scroll mess |
| A3 | Theme toggle on hub **or** Estimate / Job Check | Light ↔ Auto ↔ Dark sticks after refresh on the next page too |
| A4 | Sign out / sign back in | Comes back to hub without a dead end |

---

## B — Andrea (office / money)

### B1 · Estimate Generator (`/ui/estimate`)
- [ ] Search finds a known bid (builder name or street — not only the estimate #).
- [ ] Load a bid → client / job fields prefill.
- [ ] Add or edit one line → **Generate Preview** → PDF looks right in the iframe.
- [ ] After Preview, **Estimate #** shows a real `EST-YYYY-MMDD-NNN` (not stuck blank).
- [ ] Sticky Generate/Accept still reachable on phone while scrolling.
- [ ] *(Optional, real work only)* Finalize → hello@ draft appears; Slack/QA notice if you normally get one.

**Skip Finalize** unless you meant to send that estimate.

### B2 · Billing Hub (`/ui/billing`)
- [ ] Page opens (not blank / not error).
- [ ] Ready-to-Invoice and/or Accepted sections show *something*, or an honest empty state.
- [ ] Long lists **scroll inside their pane** (page chrome stays put) — not one endless page scroll only.
- [ ] Search by builder **or** street fragment returns a known job.
- [ ] **Open invoice** (or equivalent) lands on Invoice with the job filled — not a blank form.

### B3 · Invoice Generator (`/ui/invoice`) — the Est→Inv path
- [ ] Look up the same project from B2 (or open via Billing Hub deep link).
- [ ] **Estimate import** card appears when an estimate exists (“Add estimate lines” and/or “Add estimate total”).
- [ ] Click one once → line(s) appear; button flips to Added / disables (no double-bill).
- [ ] **Generate Preview** → amounts look sane.
- [ ] Sticky Generate/Accept reachable on phone.
- [ ] *(Optional, real bill only)* Accept → Stripe + billing draft path you already trust.

**Do not Accept a test invoice** on a live customer unless Jordan OK’d a void/cleanup plan.  
**Corrections** live inside Invoice (“Correct / reissue…”) — there is no separate Correct tile.

### B4 · Change Order (`/ui/change-order`)
- [ ] Find a real project (URL or search).
- [ ] Client/job prefill; existing COs listed if any.
- [ ] Enter a small line → **Generate Preview**.
- [ ] Sticky Accept/Generate still reachable on phone while scrolling.

### B5 · Paid by Check (`/ui/check`)
- [ ] Upload a check or stub photo (can be a known already-paid one).
- [ ] Extract fills editable fields; invoice picker is searchable / scrollable.
- [ ] If already deposited → clear “already deposited” style message (no double write).
- [ ] *(Real deposit only)* Confirm & record → Stripe/Monday/Drive as usual.

### B6 · COI Generator (`/ui/coi`)
- [ ] Page opens; template status looks healthy (not “missing template”).
- [ ] Fill a **test** holder → Preview stamps the certificate holder block.
- [ ] *(Real COI only)* Finalize → hello@ draft + Drive filing.

### B7 · Activity (`/ui/activity`)
- [ ] Events load (not 503 / empty forever).
- [ ] Search for a customer name or “estimate” / “invoice” returns rows.
- [ ] Load more / filters don’t crash the page.

### B8 · Time Off (`/ui/timeoff`)
- [ ] Page opens; form embeds or a clear “not configured” notice (either is a pass if honest).

---

## C — Jake (sales / field handoff)

### C1 · Your Morning Brief (`/ui/morning`)
- [ ] Opens for your user (private brief, not someone else’s).
- [ ] Shows today’s stops / blockers / attention items, or a clear empty day.
- [ ] On phone: readable without pinching.

### C2 · Takeoff → Estimate
- [ ] Hub **Takeoff** opens `/ui/takeoff` (portal launcher) with Path strip + Back to hub.
- [ ] From that page, **Open Takeoff app** opens https://gvctakeoff.netlify.app/v2.html in a new tab (portal tab stays).
- [ ] *(When you have a takeoff ready)* Export for Portal → draft shows up in Estimate Generator (or the path you were shown).
- [ ] If export isn’t ready today → ⏭ skip and note it.

### C3 · Estimate Generator (sales path)
- [ ] Find an open bid by customer / street.
- [ ] Prefill looks right (no reinventing the job name by hand).
- [ ] Preview PDF once; Estimate # fills after Preview.
- [ ] Sticky Generate still usable on phone.

### C4 · Job Start (`/ui/jobstart`) ⭐ highest value for you
- [ ] Tile visible (if not → Jordan must grant `jobstart`).
- [ ] Bid list loads; you can pick a bid (open or accepted — whatever the page shows).
- [ ] Prefill banner or source tags appear when scope review / board data exists.
- [ ] Proposed job name shows **3 parts** with pipes (`Street, City, ST ZIP | Builder | Job Title`) — warn if `|` missing or Job Title blank.
- [ ] Required fields clear; optional blocks don’t bury the form.
- [ ] *(Real handoff only)* Send to Ops → Ops can see it; **do not Accept your own packet** unless you’re admin and mean to.
- [ ] After a real Accept (Ops): Projects + Ops items exist, packet PDF in Drive Handoff/ — confirm once with Jordan/Ops.

**First live send of a packet is the real PDF smoke** — if something fails, stop and ping Jordan with the bid name.

### C5 · Field Manual (`/ui/fieldguide`)
- [ ] Opens without a special grant (everyone should see it).
- [ ] Plain / Full detail toggle works; a Component Index jump lands on the right section.
- [ ] Theme toggle works (matches hub preference if you set one).
- [ ] *(Optional)* Open **Coach** on a procedure you know — steps look useful, not empty fluff. Note which page if Coach feels generic.
- [ ] Readable on phone.

### C6 · Job Check (`/ui/jobcheck`) — light pass only
- [ ] Active jobs list loads; search finds a job.
- [ ] Open one → **status picker** (tap-to-choose labels) shows current Monday values — not a broken blank form.
- [ ] Change one harmless field (e.g. Notes) → **Save to Monday** → confirmed value sticks.
- [ ] If the job needs a Projects link, the in-app link panel makes sense (or shows a clear need).
- [ ] **Mark Ready to Invoice** — only tap if the job *is* ready; confirm it shows on Billing Hub for Andrea.

Skip deep Job Check if you’re slammed; one save is enough for this round.

---

## D — Jordan-only / defer

| Tool | Why deferred |
|---|---|
| Admin | Grant check only — confirm Andrea has invoice/estimate/coi; Jake has estimate + jobstart (+ takeoff). |
| GM Morning / Owner Pulse | Role tiles; smoke when you use the huddle. |
| Lien Watch | Banner says data catch-up / counsel — open once; don’t treat deadlines as gospel yet. |
| Mark’s full Job Check pass | When he has bandwidth. |
| Stripe online → Monday Paid | One-time: set `STRIPE_WEBHOOK_SECRET` (docs/DEPLOY-IN-BROWSER.md). `/health` → `stripe_webhook_secret_present`. |
| P5 Ready-to-Invoice scheduler | Actions → **P5 activate ready-to-invoice** → `check` then `activate` (stays dry_run). Live staging only after gauntlet — see docs/P5-SPEC-invoice-consumer.md. |

---

## Reply template (copy/paste)

```
Portal smoke — <name> — <date> — phone / laptop / both

A Hub home: 
B1 Estimate: 
B2 Billing Hub: 
B3 Invoice + estimate import: 
B4 Change Order: 
B5 Paid by Check: 
B6 COI: 
B7 Activity: 
B8 Time Off: 

C1 Morning: 
C2 Takeoff→Estimate: 
C3 Estimate (sales): 
C4 Job Start: 
C5 Field Manual (+ coach/theme): 
C6 Job Check (status picker): 

Blockers / weirdness:
1.
```

Use ✅ ❌ ⏭ and a few words. Screenshots welcome on ❌.

---

## Suggested first round

1. **Jake:** A (hub) → C1 → C4 → C5 (Morning, Job Start, Field Manual). Add C3 if he has a bid handy.  
2. **Andrea:** A (hub) → B2 → B3 → B1 (Billing Hub → Invoice import → Estimate preview). Add B4–B8 as time allows.  
3. Compare notes once — anything both hit is priority.
