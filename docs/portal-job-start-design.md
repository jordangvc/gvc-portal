# Job Start — Sales → Operations handoff (Jordan + Jake, Jul 29, 2026)

Jake's ask: a real way to hand a won job from Sales to Operations. Jordan's
calls on the three open questions (AskUserQuestion, 2026-07-29): build it into
the **portal**, make it a **hard gate**, and **leave historical data alone**.

## Why this exists — what was actually broken

The handoff already "existed" on Monday and was quietly doing a third of the job.

A Bid Board workflow (id 1939926362) fires on
**Stage → Accepted** and:

1. creates an item on **Projects** (1918846405) in "New Projects (Not Started)",
   connecting Customers + Leads, setting `deal_stage` and `status0` = "Not Started";
2. posts to Slack `C0B5J4P16AH`: *"A new item was created in the Projects
   Dashboard **and Operations Dashboard**…"*

There is no second create node. **It never touches the Operations board** — but
it has been announcing that it did on every won job since May.

Verified against live board data 2026-07-29 (first 30 accepted bids):

| Column | State |
|---|---|
| `connect_boards1` "Team Tasks" → Operations | empty on 100% |
| `board_relation_mm44jdnw` "Operations" | empty on 100% (dead duplicate) |
| `board_relation_mm40ymfz` "link to Projects" | empty on 100% (dead duplicate of `connect_boards4`) |
| `date6` "Accepted Date" | **null on every won deal** |
| `files9` "Accepted Estimate" | empty on all but one |

Concrete breakage in that same sample: ~8 accepted bids have no Projects item at
all; Tedesco links to two projects, one an unrelated RestoPros job; Kaiker and
Esquire are Accepted but parked in the Lost Deals group. The group title "Won
Deals (**Verify in Projects Board**)" is a warning someone already encoded.

The deeper gap is not the missing create node. **Nobody had defined what a
complete handoff contains.** The Operations board carries exactly the fields ops
needs on day one — Lock Box, Scaffolding, Heater/Cans, Shower Instructions,
Window Type — and Projects carries Builder, Supervisor, Ceiling/Garage Finish,
Window Returns, Board Count. None of them are filled by the handoff. They are the
things Jake knows from selling the job and Mark/Robert re-discover in a driveway.

A deactivated "Complete Job Start Form" label on the Bid Board and an unused
"Job Start" button on Operations are the fossils of a previous attempt.

## What it is

`/ui/jobstart` (grant key `jobstart`) — the **only** path a won bid becomes an
operations job. Sales completes a Job Start packet; the portal creates the
Projects item AND the Operations item, stamps the bid, and posts a Slack notice
that names what it actually created.

Automation without a definition of done just moves incomplete records faster.
The packet — `JOBSTART_FIELDS` in `shared/boards.py` — IS the handoff contract,
and it is config so Jordan/Jake tune it without a deploy (the `JOBCHECK_COLUMNS`
precedent).

## Two parties, one gate

Revised 2026-07-29 after Jordan reviewed the earlier Drive-based draft. The
original design here had a **one-party** gate — Sales fills the fields, the
system creates the items. That does not fix the problem the handoff standard
actually diagnoses: *"no acceptance step… when it goes sideways, ownership is
fuzzy."* A packet nobody accepted is still a job that drifted across.

So the model is two-party:

| Gate | What it stops |
|---|---|
| **Field completeness** gates the **SEND** | Sales can't hand over a half-packet |
| **Ops acceptance** gates the **JOB** | Nothing reaches the schedule unaccepted |

**Monday items are created only in `accept()`.** That single fact is what makes
"a job belongs to Sales until Operations accepts it" true in the system instead
of true only on a wall card.

Rules that fall out of it:

- The **sender cannot accept their own packet** (admins excepted, and logged) —
  a handoff with one signature isn't a handoff.
- Completeness is re-checked **on send and again on accept**, so a packet can't
  be emptied out in between.
- An accepted or in-review packet is **read-only**. It's a record, not a form.
- **No "silence is acceptance."** The v1.0 wall card had that rule alongside "no
  signature, no crew" — they contradict each other, and the timing rule quietly
  voided the strongest sentence in the document. Here, unaccepted means
  unaccepted; the Slack ping is what stops it being ignored.

**Field mitigation:** the form **autosaves on every change**, per bid, shared
across devices via the portal state bucket. Jake can start a packet on a phone
in a driveway, lose signal, and finish at his desk without retyping.

## Flow

1. **Pick a won bid** — Bid Board rows at Stage = **Accepted**. Packets waiting
   on Ops sort to the top; that's the queue that blocks jobs.
2. **Prefilled** from what we already hold: customer, location, scope, estimate
   # and total, services, board count, scaffold, lot, takeoff link. Sales fills
   only what's genuinely missing.
3. **Fill the packet.** Required fields are marked and counted ("3 still needed").
4. **Send to Operations** — unlocks when required fields are complete. Renders
   the packet PDF and pings ops in Slack. *Nothing is written to Monday yet.*
5. **Ops accepts** — or **sends back** with a note saying what's missing, which
   returns the packet to Sales for editing.
6. On accept: Monday items created, bid stamped, **accepted packet PDF filed to
   the job's Drive folder**, links posted to Slack.

## The document

Jordan, 2026-07-29: *"There should be no handwriting… anywhere, because people's
handwriting sucks. Everything should be done online and be able to be sent out
as a PDF or Google Drive link."*

The packet is a **generated document**, never a printed form. It renders through
the same WeasyPrint path as the estimate / invoice / CO PDFs
(`templates/job_handoff.html.j2`), carries the acceptance block showing who sent
and who accepted, and on acceptance is filed to
`Projects/<year>/<Residential|Commercial>/<customer>/<job>/Handoff/` — the same
folder tree the estimate that won the job already lives in. One filename per
job, so a re-accept replaces rather than litters.

Before acceptance ops reads it as a **GCS preview link**; after acceptance the
**Drive link** is the record and appears in Slack and on the packet screen.

## Writes (on ACCEPT only, server-validated)

| Target | Action |
|---|---|
| **Projects** (1918846405) | adopt-or-create in "New Projects (Not Started)"; Builder, Supervisor, Location, Scope, Project Type, Ceiling/Garage Finish, Window Returns, Scaffold, Board Count, Lot #, Take-off link, Start/Expected Finish, Linked Opportunity, Customer, `deal_stage` + `status0` = "Not Started" |
| **Operations** (1920364853) | adopt-or-create in "Upcoming Projects (Not Started)" — **the item that was never being created**; Stage = Upcoming, Billable = Yes, Start Date, Scaffolding, Heater/Cans, Lock Box, Shower Instructions, Window Type, linked to the project and the bid |
| **Bid Board** (1918846027) | `date6` Accepted Date (null on every deal today), `connect_boards4` → Projects, `connect_boards1` → Operations |
| Drive | accepted packet PDF into the job's `Handoff/` folder; link surfaced |
| Slack | truthful notice naming both created items, with the packet link |
| Activity store | who sent / who accepted / bid / project / ops item / fields |

Three Slack notices, each only claiming what actually happened: `📋 packet ready
for Operations` (on send), `↩️ packet sent back` (with the note), `🤝 accepted by
Operations` (with packet, project, ops and bid links).

## Idempotency — adopt, never duplicate

The Monday automation still fires on Accepted and will race this flow. Every
write is **adopt-or-create**, so a race or a retry updates instead of duplicating:

- Projects: the bid's existing `connect_boards4` link wins → else match by name →
  else create.
- Operations: match by name (the `_find_ops_task` pattern from `monday/co.py`) →
  else create.
- A second handoff of the same bid updates both items in place.

**⚠ Required follow-up for Jordan (not done here — it's a Monday config change
on a live automation):** turn OFF the create node in workflow 1939926362, or
retire the workflow. Until then every Accepted bid still gets an
automation-created Projects item that this flow then adopts — correct, but the
misleading "and Operations Dashboard" Slack line keeps posting. This is the same
open item as the "Bid Sent Notice" reword already tracked in CLAUDE.md.

## Locked decisions

1. **The portal owns handoff creation.** Monday stays the source of truth for
   job data; the portal is the only sanctioned path from won bid to ops job.
2. **The packet is config, not code.** `JOBSTART_FIELDS` drives the form, the
   validation, and the writes from one spec. Adding a field is a config edit.
3. **Required set is deliberately small** — Builder, Supervisor, Location, Scope,
   Project Type, Start Date, Lock Box, Board Count. Over-requiring a hard gate is
   how gates get routed around. Everything else is prompted but optional.
4. **People columns are never written** (Ops. Owner, Sales Owner, Sales Lead).
   They need Monday user ids; the mirrors already carry the sales owner. Ops
   assigns its own owner — that's an ops decision, not a sales one.
5. **Money columns stay out of the gate** except Board Count, which ops needs for
   stocking. Contract Value/Estimate $ arrive via the Linked Opportunity mirror,
   never copied.
6. **Never creates a Customers-board record.** The bid's customer link is copied
   across; an unlinked bid is surfaced as a warning, not silently invented.

## ⚠ Unconfirmed — Jordan to verify (standing rule: confirm, don't assume)

The Bid Board has **two** relation columns pointing at Operations:
`connect_boards1` ("Team Tasks") and `board_relation_mm44jdnw` ("Operations").
Both are empty on 100% of accepted bids, so live data can't break the tie. This
build writes **`connect_boards1`**, env-overridable via
`GVC_MONDAY_BID_OPS_LINK_COL`. If Jordan says the other is canonical, it's a
one-env-var change, no deploy.

## Later (not v1)

- Photo attach at handoff (site photos → Drive → both items).
- Per-customer packet templates (Danis progress-bill jobs need AIA fields a
  residential basement doesn't).
- Reverse signal: ops flags an incomplete handoff back to the salesperson.
- Backfill of the historical breakage above — deliberately out of scope per
  Jordan's "leave history alone".
