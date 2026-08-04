# GVC Portal System — Working Memory

The internal employee portal: portal.greenvalleycontractors.com → Cloud Run
service `gvc-invoice` (project `gvc-invoice-system`, us-central1). Started as
invoicing; now also estimates, change orders, and paid-by-check, with takeoff +
material-stocking next. THIS file is the DEEP memory for portal work — architecture
first, then the dated build history, locked decisions, board IDs, and backlog.
Workspace-level orientation (the other directories, people, what's in flight) lives
in `~/Documents/GVC/CLAUDE.md` — a new agent should skim that first, then read this.

See also: `AGENTS.md` (agent quickstart + how to add a module) and
`docs/portal-modularization-2026-06.md` (structure rationale + deploy runbook).

## ✨ ESTIMATE → JAKE PLAN FOLDER PDF — BUILT 2026-08-04

On estimate finalize, when **Plan Folder #** is known, the PDF is ALSO uploaded to
the root of Jake's numbered plan folder (not only `Projects/.../Estimate/`).

- Form field `plan_folder_number` on `/ui/estimate` (Estimate details).
- Prefill from Bid Board column `text_mm5rjq00` via `build_prefill` / `lookup_bid`.
- Finalize: `set_plan_folder_number` on the bid (soft-fail) +
  `DriveUploader.find_numbered_child_folder(JAKE_PLAN_FOLDER_ROOT, n)` +
  `upload_pdf_to_folder` into that folder root (soft-fail).
- Missing / ambiguous / no_access never blocks Gmail, Monday, or the Estimate/ path.
- Helper: `subsystems/estimate/plan_folder.py`. Tests: `tests/test_estimate_plan_folder.py`.

## STANDING RULE — confirm before assuming (full rule in root CLAUDE.md)
Never design/code against unconfirmed foundational facts: whether two accounts/logins
are the same, which system owns which entity + sync direction, the auth model,
board/column/project IDs, env/secret layout, deploy targets. Ask Jordan a short question
first. Gotcha already burned once: hello@ and billing@ are the SAME renamed Google
account (ONE Gmail token). Monday is the source of truth; one-way sync Monday → all.

## Architecture & layout (layered package, since 2026-06-25)
This repo (`~/Documents/GVC/GVC_Portal_System/`) is the CANONICAL portal code,
refactored from the flat `gvc_invoice/` repo (now FROZEN legacy — do NOT edit it).
Build-history entries BELOW dated before 2026-06-25 use the OLD flat module names —
translate them via the 'Old flat → new home' map in this section.
Behavior was preserved (verbatim function moves; 67/67 compile, imports resolve,
200 pure tests pass). Legacy memory snapshot: `CLAUDE.legacy-2026-06-25.md`.

**Layers** — imports flow ONE way: `app → orchestrators → subsystems/adapters → shared`.
- `app/service.py` — FastAPI routes + auth ONLY (web entry: `app.service:app`).
- `orchestrators/` — one flow per operation: `invoice_flow` (process_one + _run/
  _run_correction + CLI `main`), `check_flow`, `estimate_flow`, `change_order_flow`.
- `subsystems/` — domain logic: `invoice/{model,pdf,correct,drafts,aia}`,
  `estimate/{number,drafts}`, `change_order/{document,number}`, `checks/deposit`.
- `adapters/` — external systems (all outbound I/O): `stripe_invoice`, `drive`,
  `gmail`, `gcs`, `vision`, `slack_notify`, `monday/{client,co,estimate}`.
- `shared/` — `paths`, `money`, `boards` (Monday board IDs, env-overridable),
  `errors` (HTTP {code,detail,advice} envelope), `access`, `auth`, `portal_store`,
  `activity`, `activity_read`. Bottom of the graph; imports nothing internal.

**Old flat → new home** (for remapping the legacy notes below): `invoice.py` SPLIT
into `adapters/stripe_invoice` + `subsystems/invoice/{model,pdf}` +
`orchestrators/invoice_flow` (process_one) + `shared/money`; `service.py` →
`app/service.py` (+ `_run`/`_run_correction` → invoice_flow, `_friendly_error`/
`humanize_validation_message` → `shared/errors`); `monday.py` →
`adapters/monday/client.py`; `monday_co`/`monday_estimate` → `adapters/monday/{co,estimate}`;
`drive`/`gmail`/`gcs`/`vision`/`slack_notify` → `adapters/`; `estimate.py` →
`orchestrators/estimate_flow.py`; `change_order_flow.py` → `orchestrators/`;
`change_order.py` → `subsystems/change_order/document.py`; `co_number` →
`subsystems/change_order/number`; `estimate_number` → `subsystems/estimate/number`;
`estimate_drafts`/`invoice_drafts` → `subsystems/{estimate,invoice}/drafts`;
`invoice_correct` → `subsystems/invoice/correct`; `check_deposit` →
`subsystems/checks/deposit`; `access`/`auth`/`portal_store`/`activity`/`activity_read`
→ `shared/`. Monday board IDs now centralized in `shared/boards.py`. Repo-relative
paths (templates/assets/web/.env/creds) centralized in `shared/paths.py`.

**DEPLOY (new path + entrypoint; Cloud Run service name / URL / env / secrets UNCHANGED):**
`cd ~/Documents/GVC/GVC_Portal_System && gcloud run deploy gvc-invoice --source .
--region us-central1 --project gvc-invoice-system --account=hello@greenvalleycontractors.com`.
Entry is now `uvicorn app.service:app`; the Dockerfile's build-time `import app.service`
is the smoke test. (Legacy deploy commands below say `cd ... gvc_invoice` — use
`GVC_Portal_System` instead.) Run the full suite in the WeasyPrint venv before deploy.

**Adding a new app (takeoff, material stocking):** new `orchestrators/<app>_flow.py`
+ `subsystems/<app>/` + any `adapters/` module + a thin route in `app/service.py`;
reuse `shared/`. Apps cooperate via an orchestrator/shared function — never by
importing each other's internals. Full guide: `GVC_Portal_System/AGENTS.md` +
`GVC_Portal_System/docs/portal-modularization-2026-06.md`.

---
*Build history, locked decisions, board IDs, and the dated session log follow. Entries before 2026-06-25 reference the OLD flat module names — use the 'Old flat → new home' map above to translate.*

## 🤝 JOB START — Sales → Operations handoff, BUILT + ✅ DEPLOYED 2026-07-29
Jake's ask (via Jordan): a real way to hand a won job from Sales to Ops.
✅ **LIVE: revision `gvc-invoice-00075-tqg`** (deployed from portal-current as hello@, 100% traffic,
was 00074-gn7). Post-deploy /health: monday_configured ✔ · slack_token_ok ✔ (bot gvcreporting) ·
drive_configured ✔ · gmail_ready ✔ · grants_backend gcs, store ok, 6 users. Route probe:
/ui/jobstart 303 (auth redirect, not 404) · /ui/api/jobstart/bids 401 · /ui/fieldguide 303.
⚠ GOTCHA (cost two failed calls): **gcloud is NOT on the Git Bash PATH on Jordan's PC.** Use the
PowerShell tool with the full path `C:\Users\jorda\AppData\Local\Google\Cloud SDK\google-cloud-sdk\
bin\gcloud.cmd`, and pass `--source "C:\Claude\GVC Invoice portal\portal-current"` explicitly rather
than relying on cwd.
REMAINING BEFORE FIELD USE (admin, in this order): (1) **grant `jobstart`** in /ui/admin to Jordan,
Jake, Mark, Robert — it is deliberately NOT implied by `estimate`, so NOBODY holds it yet and the tile
is invisible to everyone; (2) confirm the service account can read Jake's Completed Plans folder — the
definitive 10-second test is to open /ui/jobstart, pick the Bryant/Jent bid, and look for the green
"Prefilled from Jent-Bryant Res - Scope Review.pdf" banner. If it says no scope review matched, share
folder 1X1vuutnTuCN0hxTZSANmm3QC6SQ41Gc0 with gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com
as Viewer (the folder's permission list shows a domain-reader for greenvalleycontractors.com, which
does NOT cover the service account's own domain); (3) optional
--update-env-vars GVC_JOBSTART_SLACK_CHANNEL=<id> (falls back to GVC_JOBCHECK_SLACK_CHANNEL).
ROOT-CAUSE FINDING (verified live on the boards, not assumed): a handoff automation ALREADY
existed and was doing a third of the job. Bid Board workflow **1939926362** fires on Stage →
Accepted, creates a Projects item in "New Projects (Not Started)", and posts to Slack
C0B5J4P16AH claiming "a new item was created in the Projects Dashboard **and Operations
Dashboard**". That workflow has ONE create node, targeting Projects only.
⚠ CORRECTION (2026-07-29, after Jake-meeting transcripts): an earlier draft of this entry said
the handoff "has NEVER touched the Operations board." That was OVERREACH — what was actually
verified is that the BID BOARD's ops-link columns are empty, which is a different claim. Per the
10:43 meeting, Jordan **deliberately disabled the accepted-bid automation a few days ago** because
it was creating DUPLICATES ("it creates like 2 projects, 2 operations"), root-caused to Joe
copy-pasting the recipe. THE DUPLICATE IS FOUND: legacy automation **19062630** (active=FALSE,
trigger deal_stage index 1 = Accepted, action = create item on 1918846405 group
new_group25317__1) duplicates workflow 1939926362 exactly. So the ~8 accepted bids with no
Projects item are explained by the automation being OFF, not by it being subtly broken.
ALSO FOUND (legacy automations, all still ACTIVE, all pointing at PRE-PORTAL Job Start forms that
this tool replaces — retire them or Jake gets sent to the wrong surface): **10484288** "To Send the
Job Start Form" (button0 → PandaDoc eform b28624b9-8a12-4496-b238-8e06d02d958d to deal_owner);
**13529570** (deal_stage index 12 "Complete Job Start Form" → "THIS PROJECT IS READY! PLEASE
COMPLETE JOB START FORM here https://wkf.ms/3CcJlp4"); **10484181** "To Send Job Check Form"
(button2 → PandaDoc eform d19b40ee-b490-4aa7-bb9f-b7c6cde1f169). Separately **10483015** (ACTIVE)
creates a "Site Measure - {name}" item on Operations from button_1 — that one is unrelated to the
handoff and should stay.
Evidence over the first 30 accepted bids: `connect_boards1`/`board_relation_mm44jdnw` (both →
Operations) empty on 100%; `board_relation_mm40ymfz` (dup of connect_boards4) empty on 100%;
**`date6` Accepted Date null on EVERY won deal**; ~8 accepted bids have no Projects item at all;
Tedesco links to TWO projects (one an unrelated RestoPros job); Kaiker + Esquire are Accepted but
sitting in the Lost Deals group. The group title "Won Deals (Verify in Projects Board)" is a
warning someone already encoded. Deeper gap: nobody had defined WHAT a complete handoff contains —
Ops carries Lock Box/Scaffolding/Heater-Cans/Shower/Window Type and Projects carries Builder/
Supervisor/Ceiling+Garage Finish/Window Returns/Board Count, and the handoff filled none of them.
Fossils of a prior attempt: a DEACTIVATED "Complete Job Start Form" Bid Board label + an unused
"Job Start" button on Operations.
DESIGN ARC THIS SESSION (recorded because the reversals matter): Jordan first picked portal +
hard gate; then surfaced a Drive-based Handoff Standard v1.0 (Google Doc + wall card) whose own
closing section said "runs on Drive discipline right now — deliberately… automating a broken
process just breaks it faster", and picked "Drive process, nothing else"; then rejected the
paper artifacts outright — **"no handwriting, any motherfucking thing anywhere… Everything should
be done online and be able to be sent out as a PDF or Google Drive link."** FINAL MODEL = portal,
prefilled, two-party, generated document. Two contradictions in v1.0 were fixed on the way:
"no signature → no crew" vs "silence is acceptance" (kept the signature, DROPPED silence-as-
acceptance), and a wall card tagline off the brand guide's own Collaborative/Respectful voice.
LOCKED — TWO PARTIES, ONE GATE: field completeness gates the **SEND**; **ops acceptance gates the
JOB**. Monday items are created ONLY in `accept()` — that is what makes "a job belongs to Sales
until Ops accepts it" true in the system. Sender cannot accept their own packet (admins excepted,
logged). Completeness re-checked on send AND accept. Accepted/in-review packets are READ-ONLY.
BUILT: shared/boards.py += JOBSTART_FIELDS (**the handoff contract as editable config** — one spec
drives form + gate + writes; 19 fields, 8 required: project_type/builder/supervisor/scope/
**exclusions**/start_date/board_count/lock_box) + JOBSTART_HARD_EXCLUDED_IDS/TYPES (contract+money
columns unreachable even if added to config — regression-tested) + group/stamp column ids. A field
may target BOTH boards (Scaffold genuinely lives on each) and `targets: ()` means PACKET-ONLY (no
Monday column — gc_confirmed_on). adapters/monday/jobstart.py (NEW): fetch_accepted_bids /
get_bid_detail (prefill + raw location copied verbatim rather than reconstructing lat/lng) /
get_field_labels (live label sets) / **hand_off = ADOPT-OR-CREATE** (Projects: existing
connect_boards4 link → by name → create; Operations: by name → create) so the still-live legacy
automation races into an UPDATE, never a duplicate; stamps date6 + both link columns.
subsystems/jobstart/{drafts,packet}.py (NEW): drafts = per-BID packet store w/ the 4-state machine
(draft → with_ops → sent_back/accepted, EDITABLE_STATUSES, last-writer-wins, keyed by bid so two
people collaborate instead of racing); packet = pure build_context + render_packet_pdf.
templates/job_handoff.html.j2 (NEW, brand palette #548427/#7EB438/#D6D2C4/#B6985A) w/ an acceptance
block naming who sent + who accepted. orchestrators/jobstart_flow.py (NEW): pure packet_fields/
missing_required/shape_value/build_writes + send_to_ops / send_back / accept. accept() = create
Monday → mark accepted → render → **DriveUploader.ensure_handoff_folder** (NEW in adapters/drive.py:
Projects/<year>/<Res|Comm>/<customer>/<job>/Handoff/, same tree as the estimate) → upload_or_replace
→ Slack. slack_notify += notify_job_start_sent / _sent_back / _handoff — each claims ONLY what
happened (the whole point: the legacy notice lied). app/service.py += 7 routes (71 total, was 58);
access.py += `jobstart` feature (NO implication from `estimate` — admin grants it); web/jobstart.html
(mobile-first, autosave, sales-fill + ops-review modes); hub tile; footer r7 → r8.
VERIFIED: py_compile clean across all touched modules; `import app.service` OK (71 routes, all 7
jobstart paths present); jobstart.html JS `node --check` clean; Jinja template renders w/ real
sample data (9 content probes pass); gate/shaping/hard-exclusion/state-machine logic all asserted
green incl. the "contract column added to config stays blocked" regression and "status survives an
edit" regression. ⚠ NOT verified locally: the WeasyPrint PDF binary — weasyprint isn't installed on
Jordan's PC (tests/ + venv still lost post-Joe). The render call is byte-identical in shape to
estimate/CO/invoice, and weasyprint>=60 is in requirements.txt so the Cloud Run image has it, but
**the first live send is the real PDF smoke test**. No Dockerfile change needed (COPYs whole packages).
🔁 SESSION PART 2 — INGEST + JAKE'S CONVENTIONS (same day, after the 10:43/11:12 Jake-meeting
transcripts + Jake's "Estimating Pipelines Reference" doc 1tFBHnyXxidyXb4Vfr8D43p3FpNXmu-1wztytugeY2ZY
were surfaced). Jordan's driver: "the handoff has to pull the data from somewhere or it becomes an
input job for you. We don't want an input job for you."
  • **subsystems/jobstart/scope_review.py (NEW)** — PURE parser for Jake's scope review, the packet's
    PRIMARY source per Jordan ("your scope review is going to be the most valuable"). Verified against
    the REAL doc 1s2bmD96CArGcfuiphwsXvmY2cyI_AimGk5-BZWblgQg ("KPMG Cincinnati Renovation Scope
    Review") — structure is PROJECT INFO key/values, then trade sections FRAMING/INSULATION/DRYWALL/
    FRP/ACT/PLYWOOD/CEMENT BOARD/PAINTING, then NOTES, then "Walk Through Notes". THREE extractions in
    value order: (1) **[NEEDS CLARIFICATION] lines → open questions** (Jake: the scope review "lists a
    lot of things that Rob might have minor questions on" — nothing else in GVC holds these, and they
    are what become change orders when unread); (2) **exclusions** (NIC / not-in-GVC-scope / by others
    / "No X scope found"); (3) **scope** (only trades with real work — a section whose sole line is
    "No FRP scope found" is NOT in scope). 🐛 TWO BUGS CAUGHT BY TESTING AGAINST THE REAL DOC: Google
    Docs export ESCAPES the marker as `\[NEEDS CLARIFICATION\]`, so unescaping must happen before any
    match (first run silently found ZERO questions); and ACT/FRP must not be `.title()`d into
    "Act"/"Frp". Contractor-contact extraction is greedy-to-last-paren (a contact's own phone carries
    parens — "Dave K (513) 555-0142" — and non-greedy stopped inside the area code) and tolerates a
    plain hyphen as well as the template's em-dash, preferring whichever fragment holds a phone number.
  • **subsystems/jobstart/ingest.py (NEW)** — the PURE merge layer implementing Jordan's stated
    precedence: **packet (typed) > scope review (Drive) > board updates > Bid Board columns**. A value
    a human typed is NEVER overwritten by an automatic source. Returns a `sources` map so the UI can
    tag each prefilled field ("from the scope review") — a value Jake can't trace is one he'll retype.
    `from_updates()` parses Monday update bodies for "Label: value" lines. 🐛 BUG CAUGHT: Monday update
    bodies are HTML and `</p><p>` collapsed to a space, so the first label swallowed every later field
    ("Lock box: 4417 front door  Board count: 340") — block-close tags must become newlines BEFORE
    tags are stripped. Newest update wins.
  • **adapters/drive.py** += `find_scope_review()` (lists every "Scope Review" file across all shared
    drives, scores against distinctive job tokens with a _STOPWORDS list so geography/boilerplate can't
    match one job to another job's document; zero token hits ⇒ None ⇒ fall back to Monday) +
    `read_document_text()` (Docs via text/plain export, PDFs via the pypdf already shipped for the COI
    stamper; returns "" on anything unreadable) + `ensure_handoff_folder()`. `import sys` added — it
    was missing and py_compile can't catch a NameError.
  • **adapters/monday/jobstart.py** += `fetch_item_updates()` (keeps the handoff CURRENT rather than a
    snapshot) and **`_write_with_fallback()` / `FRAGILE_COLUMNS`**. The latter is straight off Jake's
    doc: `location5` is "API-blocked; use text23 as address-text workaround" and `connect_boards5` is
    "API-blocked, attempt anyway, flag for manual entry". Everything went out in ONE
    change_multiple_column_values mutation, so one rejected column would have failed the WHOLE handoff
    — now the write is attempted, and on rejection the fragile columns are dropped, retried, and
    surfaced to the user as "set these by hand (Monday API limitation, not a portal bug)".
  • **subsystems/jobstart/gc_confirm.py** += `scope_warnings()` / `scrub_client_scope()` enforcing
    JAKE'S OWN client-facing rules from his Bid Description pipeline: **never show square footage** and
    **never say 1-side/2-side**. This was a live defect — the GC email piped the scope straight
    through, and the scope is often ingested from the scope review, which is an INTERNAL document full
    of both ("~8,775 SF per occupant load calc", "2 layers on symbol side"). His doc says "re-scan
    specifically for stray square footage and 1-side/2-side phrasing — the two most common slip-ups",
    so that re-scan is now automated. A parenthetical that exists BECAUSE of the figure is dropped
    whole (removing just the number left "Floor 34 ( per occupant load calc)"), while legitimate ACT
    spec parens survive — his rules REQUIRE specific ACT callouts ("Suprafine XL", 9/16", 2'x2').
  • shared/boards.py += packet fields `open_questions` (→ Operations `long_text_mkpzf3je`),
    `allowances`, `gc_pm`/`gc_email`/`super_email` (packet-only). 24 fields, 8 required.
  • templates/job_handoff.html.j2 += "Open questions — answer before the crew mobilizes" (gold-ruled,
    its own visual weight) + Allowances section. web/jobstart.html += per-field source tags and a
    "Prefilled from {doc} — {trades} · N open questions · N exclusions" banner.
✅ NAMING — DECIDED 2026-07-29. Jordan: "We prefer the -Pipe- | it looks so much better." The standard
is Jake's, from his Estimating Pipelines Reference: **`[Street Name/Number] | [Builder/Client]`**,
pipe not dash, no city/state/ZIP/job-type in the title (those live in fields), prefixes CO_/STU-/PL-/
WAR- preserved. 2 or 3 parts both valid (his own worked example keeps a client AND a builder).
BUILT: **subsystems/jobstart/naming.py** — `to_standard()` converts a legacy bid name to the standard
and, per Jake's own rule, **never invents a missing builder** — it returns ok=False + a note and the
UI asks (a linked Customer from the bid may supply it, since that's a recorded fact not a guess).
`simplify_street()` drops street suffix / city / state / ZIP ("9761 Gertrude Lane, Cincinnati OH 45231"
→ "9761 Gertrude"); peels a trailing comma-less city ("937 Madison Ridge Lawrenceburg" → "937 Madison
Ridge") and a descriptor stuck on without a clean separator ("Steele Properties -New House" →
"Steele Properties"). web/jobstart.html shows the standard, what it renamed, and warns on a missing `|`.
🛡 **THE DUPLICATE SAFEGUARD — the load-bearing part of this change.** Adopt-or-create finds existing
Projects/Ops items BY NAME, and every item predating today uses an older convention. Exact-match-only
would have missed them and created duplicates — exactly the failure Jordan asked to be guarded against
in the 10:43 meeting. So `adapters/monday/jobstart.find_item_by_name` now probes Monday on the single
most distinctive token (the street number) and scores candidates via `naming.best_match`: exact always
wins, else highest Jaccard-over-distinctive-tokens above MATCH_THRESHOLD 0.5 (+0.35 boost when both
names share a street number), and an AMBIGUOUS top-2 returns None — adopting the wrong job is worse
than creating a new item. Asserted: legacy↔pipe pairs score 1.00 and match; different jobs for the
same builder (21435 Abbys/Greg Gavin vs 23946 Grubbs/Greg Cross; 12333 vs 12445 Pollard) score 0.20–0.33
and correctly do NOT match.
⏸ DEFERRED BY JORDAN (2026-07-29, "Dont spend those tokens to fix the pipe. DO it one day we have extra
before a reset"): the three boards' own `item_terminology` strings still describe the OLD conventions —
Bid Board "Location_opportunity name -builder_type", Projects "Location_project name -builder_type",
Operations "Address - customer name - project type". Cosmetic only, because matching is token-based and
nothing reads those strings — but they teach a new hire the wrong format. Low-priority cleanup, do it
on a spare-budget day. DO NOT re-open the decision itself: pipe won.
VERIFIED (part 2): py_compile clean; `import app.service` OK (72 routes, 8 jobstart); jobstart.html JS
`node --check` clean; parser asserted against the real KPMG doc (contractor/contact/project-type/
trades/3 questions/6 exclusions, FRP+Cement Board correctly excluded, clarifications never leaking
into exclusions, garbage input ⇒ found=False); ingest precedence asserted (typed value survives all
three automatic sources, whitespace never wins a slot, newest update wins); scrubber asserted (SF and
side-phrasing gone, ACT specs kept, no "( " husks); packet PDF renders with the new sections.
DEPLOY (admin): full suite in the WeasyPrint venv → `--source .` → grant `jobstart` to Jake/Jordan/
Mark/Robert in /ui/admin → optional --update-env-vars GVC_JOBSTART_SLACK_CHANNEL (falls back to
GVC_JOBCHECK_SLACK_CHANNEL) → smoke: open /ui/jobstart, pick an accepted bid, confirm prefill, send
→ ops user accepts → Projects + **Operations** items exist, Bid Board Accepted Date + both links
stamped, packet PDF in the job's Handoff/ folder, Slack shows all four links.
OPEN: (1) ⚠ **turn OFF the create node in workflow 1939926362** (or retire it) — until then every
Accepted bid still gets an automation-created Projects item this flow adopts, and the misleading
"and Operations Dashboard" line keeps posting; same family as the "Bid Sent Notice" open item.
(2) ⚠ UNCONFIRMED: Bid Board has TWO Operations relation columns — `connect_boards1` ("Team Tasks")
and `board_relation_mm44jdnw` ("Operations"), both empty on 100% of accepted bids so live data
can't break the tie. We write connect_boards1; env `GVC_MONDAY_BID_OPS_LINK_COL` flips it with no
deploy. Jordan to confirm which is canonical. (3) No GC scope-confirmation email template yet —
highest-ROI item in Jordan's own standard (every GC correction = a change order not eaten); the
packet only records the date it was sent. (4) Historical breakage above deliberately NOT backfilled
("leave history alone"). (5) Superseded paper artifacts live in Jordan's Downloads
(GVC_Handoff_Wall_Card_v1.1.html / GVC_Handoff_Sheet_v1.1.html) — dead, kept only as a record.

## ✨ SENT-WATCHER (true "emailed to client" detection) — BUILT + DEPLOYED + LIVE 2026-07-26
Context: Joe left GVC; this session was driven by Jordan (owner) + Claude on Jordan's Windows PC.
The canonical repo was RECOVERED from the Cloud Run deploy bundle (gs://run-sources-…/1784748266…zip,
the Jul-22 deploy) into `C:\Claude\GVC Invoice portal\portal-current\` — ~/Documents/GVC on Joe's Mac
is unavailable. ⚠ tests/ is NOT in the deploy bundle (.gcloudignore), so the 441-test suite is LOST
on this machine — verification for this deploy = py_compile + stubbed `import app.service` (58 routes)
+ live dry-run. Recovering tests from Joe's Mac or rebuilding them is an open item.
PRECEDING INCIDENT (2026-07-24): hello@ password reset (post-Joe security sweep) revoked the Gmail
refresh token → invalid_grant → no drafts since. Fixed by re-mint (new Desktop OAuth client
"GVC Invoice CLI Win", client JSON kept at repo .google-oauth-client.json) + gmail-token secret v2/v3
+ revision bounce. The gotcha rediscovered: adding a secret VERSION does nothing until a new revision
starts (min-instances=1 keeps the old token mounted).
THE FEATURE — Andrea clicking Send in Gmail now produces a truthful signal (the old notices fired at
DRAFT time; Monday automation "Bid Sent Notice" posts "sent to client" on item creation — still
misleading, see OPEN):
  • adapters/gmail.py: SCOPES += gmail.readonly; _load_credentials now loads the token with its OWN
    granted scopes (scopes=None) so pre-readonly tokens keep refreshing (pinning SCOPES would have
    broken drafts until re-auth); NEW GmailScopeMissing + find_sent_message(subject, newer_than_days)
    — the portal's ONLY Gmail read (messages.list/get on in:sent).
  • adapters/slack_notify.py: NEW notify_invoice_emailed (→ billing channel) + notify_estimate_emailed
    (→ estimates channel), "📤 … emailed to client" wording.
  • adapters/monday/client.py: INV_COL_EMAILED_ON = date_mm5kfwr8 (env GVC_MONDAY_INV_EMAILED_ON_COL;
    column created 2026-07-26 via Monday MCP) fetched on every invoice row (+issue_date) ;
    stamp_invoice_emailed() sets the date + flips Status "Draft Ready"→"Invoice Sent" ONLY from
    Draft Ready (never regresses Paid/Void/Partially Paid/office-set states).
  • adapters/monday/estimate.py: COL_EMAILED_ON = date_mm5kn8d2 (env GVC_MONDAY_BID_EMAILED_ON_COL;
    created same day) + fetch_pending_estimates(mc) (Estimate # set, Emailed-on empty, paged) +
    stamp_estimate_emailed(). NOTE: Stage "Sent to Client" at draft time is UNCHANGED (other
    automations key off it) — Emailed-on is the truthful column.
  • NEW orchestrators/sent_watch_flow.py check_sent(limit_days=45, notify_backfill_hours=48, dry_run):
    work list = rows with no Emailed-on and issue/estimate date within limit_days (dateless = old =
    skipped, keeps sweeps bounded); Gmail subject search "Invoice {id}" / "Estimate {id}"; per-item
    graceful; state/dedup = the Emailed-on column itself; sends older than 48h stamp QUIETLY (no
    Slack) — that's how the first sweep backfilled history without spamming; stamp-failure skips the
    Slack ping so a retry can't double-post; GmailScopeMissing/NotConfigured aborts the sweep
    (ok=false + code). Slack times rendered America/New_York; requirements += tzdata (slim image has
    no tz db — caught by the Windows sandbox import test).
  • app/service.py: POST /v1/tasks/check-sent (X-API-Key, CheckSentRequest {dry_run, limit_days}).
LIVE STATE: deployed rev gvc-invoice-00064-54r (gcloud from Jordan's PC, hello@); gmail-token secret
v3 = hello@ token WITH readonly (minted 2026-07-26); first live sweep: 37/37 invoices + 49/49
estimates backfill-stamped, 2 fresh notices (est 2026-0724-002 — dup Bid rows ⇒ 2 pings, known),
0 errors. Cloud Scheduler job **gvc-sent-watch** (us-central1, */10 min, America/New_York) POSTs the
endpoint with X-API-Key from gvc-service-api-key. GOTCHA: Windows gcloud.cmd mangles quoted JSON in
--message-body (stored {dry_run: false} unquoted → FastAPI 422 → scheduler FAILED_PRECONDITION);
fixed via --message-body-from-file. If the key secret rotates, update the job's header.
OPEN: retire/reword the Monday "Bid Sent Notice" automation (posts "sent" at creation; dev notes say
1939926355 was OFF since 06-29 — something re-enabled it or a second automation exists); recover or
rebuild tests/; off-machine backup of this repo; June 500/503s on /ui/api/activity + /ui/api/check
unexplained; Owneradmin@ service account undocumented — review.

## 🔧 COI BULK REVISIONS — BUILT 2026-07-16 (the former GM's post-first-annual-run feedback), ships next --source . deploy
The former GM ran the full annual list live 2026-07-15/16; three fixes from that run (SEMANTICS CHANGE
included — supersedes part of the 07-14 locked ledger semantics):
(1) INVALID ROWS NOW MARKED NO — skipped rows (missing name/address/email) get NO written to the
Sent column on the FINAL chunk of a finalize run (was: cell untouched → 21 skipped rows invisible
in counts). Idempotent (already-NO cells skipped; re-runs re-sweep cleanly since invalid state is
computed from the data columns, not the Sent cell). Returned as `invalid_marked`
[{row_number,name,reasons,writeback_error?}]; per-row write failures loud but non-fatal.
(2) BATCH-COMPLETE UX replaces the auto-loop — coi.html no longer loops chunks in one sitting
(a dropped response mid-loop surfaced as "Network error" even though the server finished the
batch — the former GM's run hit this after every ~15). Now: run batch → "✅ Batch {n} complete — X drafted
so far, Y remaining" + **Continue the run ▸** button; connection drop → same Continue path with
"server usually finishes the batch; drafted rows are YES and will skip" (cursor kept at last
CONFIRMED next_after_row — safe: YES rows skip by state, lost-chunk NO rows simply retry, Gmail
dedup-by-identifier prevents dupes). Counters (done/failed/batch#) persist across batches in
`bulkState`; Create-drafts + review buttons locked during an active run.
(3) ACCURATE FINAL TOTALS + SLACK REWORD — final chunk always returns `sheet_totals`
{yes,no,invalid} from a FRESH post-run read (computed regardless of Slack channel; no=errored
attempts DISJOINT from invalid=skipped). UI final card shows "N drafted this run" + sheet ledger
line. bulk_summary_message rewritten: "• {yes} succeeded (marked YES) · {no+invalid} failed
(marked NO)" + separate lines "{no} draft attempt(s) errored — will retry…: row(s) …" and
"{invalid} skipped — missing name/address/email…: row(s) …". ROOT CAUSE of the former GM's "87 failed rows"
confusion: the old message printed the NO ROW NUMBERS after a colon ("Failed rows (marked NO):
87" = sheet row 87, count was 1) — row lists are now always prefixed "row(s)" and capped at 15
w/ "(+N more)". Files: subsystems/coi/bulk.py (semantics doc + _row_list + new
bulk_summary_message signature +invalid_rows), orchestrators/coi_flow.py (final-chunk sweep +
sheet_totals + summary wiring), web/coi.html (bulkState machine: bulkRunBatch/bulkShowContinue/
bulkFinish/markInvalidRowsSkipped). NO new deps/env/routes (service.py untouched).
TESTS: test_coi_bulk now 25 (+2: clean-run message, sweep-writeback-failure non-fatal; others
updated to new wording/sweep). Sandbox suite 407 passed / 1 known WeasyPrint-stub fail
(test_change_order_flow); import app.service OK (52 routes); coi.html JS node --check clean;
py_compile clean. RUN FULL SUITE IN THE WEASYPRINT VENV BEFORE DEPLOY.
SMOKE after deploy: re-run the annual sheet → first batch ends with "Batch 1 complete" +
Continue (no error wording) → continue to the end → final card shows drafted-this-run + sheet
ledger; sheet shows NO on every missing-info row; #team-annual-maintenance summary reads
"{yes} succeeded … {n} skipped — missing name/address/email: rows …". NOTE: the underlying
dropped-response cause (likely per-request latency at chunk=15) is UNFIXED by design — the UX
now absorbs it; if drops persist, lower GVC_COI_BULK_CHUNK (env) or raise the Cloud Run
--timeout.
---

## 🔶 CO APP PARITY (Find-the-Project search / drafts / revision) + NEW CO MONDAY MODEL — BUILT 2026-07-17, ships next --source . deploy
The former GM's ask: port the estimate app's customer-info logic, draft-save logic, and edit/revision logic
into the Change Order app. Design was locked + Monday columns created 2026-07-16 (see that session's
notes below / docs/portal-co-parity-design.md for the original plan); THIS session built all of it.
LOCKED (unchanged from 2026-07-16): "Find the Project" text search OR URL prefill (+phone mapping);
drafts = exact estimate/invoice pattern; revision = same CO number forever, e{n}- Drive archive,
Billed CO = WARN+allow; CO subitems dead — top-level Projects item `CO.{n} - {parent title}` in the
parent's group + an Operations-board task. Legacy subitem COs: left untouched on revision (no
migration) — CONFIRMED via AskUserQuestion this session (the former GM picked "leave the subitem untouched"
over voiding it, so mark_billed/list_billable_cos on the OLD subitem model still matter and are kept).
BUILT: subsystems/change_order/drafts.py (NEW — sibling of estimate/invoice drafts, object
portal/change-order-drafts.json, gate `change_order`, reuses the estimate pure helpers verbatim);
subsystems/change_order/revision.py (NEW — sidecar_filename `{id}.gvc-co.json` / co_pdf_filename /
merge_revision_prefill(_link.monday_item_id injection) / prior_total; REUSES
subsystems.estimate.revision.next_archive_name/archive_version rather than re-implementing the e{n}-
rule). adapters/monday/co.py REWRITTEN: fixed the 07-16-found bug (project_number now reads
text_mm4fvj91 + falls back to the Linked Opportunity's Bid Board Estimate #, since `numbers9` no
longer exists); NEW search_projects (Find-the-Project text search) / list_co_items / find_co_item;
NEW write_back() targeting the top-level-item model (create-or-update by Project # match, CO Status
ALWAYS resets to Drafted on revise, Billed-revision surfaces `monday_billed_warning` but never blocks,
Ops task created/updated via `_find_ops_task` by name + `link_to_projects` → [CO item, parent]);
old create_co_subitem/list_billable_cos/mark_billed/mark_billed_batch KEPT verbatim (LEGACY block,
still used by the invoice CO-billing writeback + old subitem COs — untouched). 🐛 Caught + fixed
during build (regression-tested in test_monday_co.py): a CO loaded straight from a CO-id search (no
parent resolved) must reuse the CO item's OWN existing title verbatim, never re-wrap it through the
`CO.{n} - {title}` formatter again — that would double-prefix to "CO.1 - CO.1 - …" and orphan the
Ops-task-by-name lookup. orchestrators/change_order_flow.py: `revise` param (requires
change_order.co_number, mirrors estimate's identifier requirement); **now mutates a deepcopy of
`data` to persist the resolved co_number before it's used** (co_number was previously never written
back into the payload, which would have left every sidecar's change_order.co_number blank — caught
before shipping, regression-tested); Drive step gained the same archive-on-collision + JSON sidecar
pattern as estimate_flow (e{n}- prefix, paired PDF+sidecar archive); Gmail dedup-by-co_number updates
the unsent draft in place; Slack gets revised/version wording. adapters/slack_notify.py: NEW pure
`_co_message` (mirrors `_estimate_message`) + `notify_change_order_drafted(revised=, version=)`.
shared/boards.py += OPERATIONS_BOARD_ID (env GVC_MONDAY_OPERATIONS_BOARD_ID, default 1920364853).
app/service.py: ChangeOrderRunRequest += revise; NEW GET /ui/api/change-order/search,
GET /ui/api/change-order/original (revision sidecar lookup), full drafts CRUD
(GET/PUT/DELETE /ui/api/change-order/drafts[/{id}]); lookup route unchanged shape (context now
includes existing_cos + client_phone from the rewritten get_project_context). web/change-order.html:
"Find the Project" card (URL / text search / CO-number search all route correctly — a search hit
whose Project # parses as a CO id offers "Load for revision" instead of "Load"), existing-CO list
under a loaded project with Billed badges, CO number field + revise checkbox (identical UX pattern to
estimate's identifier+revise-row), full draft autosave (localStorage `gvc_co_drafts_v1` + shared GCS,
save-state chip, resume/delete/start-new), revised-result rendering (drive_archived, monday_billed_warning,
separate CO-item/Ops-task links). NO new deps/Dockerfile change (subsystems/adapters COPYed whole).
TESTS: +64 across tests/test_co_drafts.py (4) / test_co_revision.py (20, incl. Slack wording +
adapters/monday/co.py's pure builders) / test_monday_co.py (6, write_back orchestration incl. the
double-prefix regression) / +3 in test_change_order_flow.py (revise validation + the co_number
sidecar-persistence fix, Drive faked). Ran the FULL suite in a fresh venv with REAL WeasyPrint 69.0
(not stubbed) — **441 passed, 0 failed** (was 405 pre-session); `import app.service` OK (57 routes,
up from 52); change-order.html JS parse-checked clean + a static id/field-name cross-check against
the HTML (zero dangling references). Full runbook + column ids: docs/portal-co-parity-design.md.
DEPLOY (admin): run the full suite in the WeasyPrint venv once more on your machine → `--source .`
deploy → smoke: paste a Monday Project URL → client/job/phone fill + any existing COs listed → Accept
→ Projects gets `CO.1 - {title}` in the parent's group + Operations gets a matching Upcoming task →
search that same CO number → "Load for revision" → edit an amount → Update this Change Order → same
CO number, e1- archive in Drive, CO item + Ops task updated in place (not duplicated), #change-orders
posts REVISED wording. OPEN (unchanged): verify the Customers-board phone column id is right in prod
(read via `priority`/`contact_phone` — confirm against a real Customer row); invoice CO-billing
writeback still targets the legacy subitem columns for old COs — a CO-billing front-end rework would
need to learn the new top-level-item columns too, not built this session.

## 🔶 CO APP PARITY — DESIGN LOCKED + COLUMNS CREATED 2026-07-16 (superseded by the BUILT entry above)
Historical record of the design-lock session — the plan below was fully implemented 2026-07-17;
kept for the column-id provenance and the reasoning behind each locked decision.

## ✨ ESTIMATE SCOPE SELECTION — new estimate section + 2 PDF pages, BUILT 2026-07-14, ships next --source . deploy
The former GM's ask: a "Scope Selection" section on the estimate form — checkboxes for GVC's standard offerings;
checking one reveals an EDITABLE textarea prefilled with our standard scope + a price input + a Save
button; Save adds a line item AND appends the scope to a "Scope Details" section. Final PDF section
order: Primary Estimate → Scope Details → Standard Deliverables (existing) → Additional Services (full
offerings menu). LAYOUT TWEAK (decided 2026-07-14, post-dry-run): Scope Details FLOWS below the Special
Notes/Notes area on the same page (~2-line gap, margin-top:30pt), NOT its own page — Standard Deliverables
(.terms) keeps its page-break-before so there's always a clean page after Scope Details; Additional
Services still its own page. Tightened a sample dry-run from 6→5 pages. Source content = the "GVC service offerings" Google Doc (1PnfnuVBF6P3UsszGhZBo6s94Kn9nFXXmCOW-WQjC0Ac).
DESIGN LOCKED (decided 2026-07-14): (1) catalog SoT = GCS state bucket portal/estimate/scope-catalog.json
w/ in-page ADMIN editor (COI-template pattern; edit wording/prices w/o deploy); (2) seed the 4 FILLED
trades (Drywall/Metal Framing/ACT/Insulation) from the doc, STUB FRP/Door&Hardware/Tectum (title only,
admin fills in-app) — "Edward Jones" in the doc is a project sample, omitted; (3) Scope Details bullets =
auto-split scope prose into 1 sentence-per-bullet at render, hard newline = forced boundary; (4) checkbox
granularity = NAMED SCOPES grouped by trade (select several per trade, each = own line item + own scope
block); (5) line item name = "{Trade} - {Scope}" (e.g. "Drywall - 5/8\" Board"); Scope Details = 3-LEVEL
hierarchy Trade→scope→descriptor bullets, multiple scopes group under one trade bullet (per the former GM's mid-build
note).
DATA MODEL (single-list, no 2nd SoT): scope lines carry extra fields scope_trade/scope_title/scope_detail
(+scope_key for UI re-sync) ON the existing line_items list; enrich() keeps them, builds
estimate.scope_details (grouped) + top-level additional_services; render passes additional_services.
Plain manual lines have these absent — validation ignores them.
BUILT: subsystems/estimate/scope_catalog.py (NEW — DEFAULT_TRADES seed; pure validate_catalog/find_scope/
split_scope_bullets [sentence split, abbrev+decimal-safe, newline-forced]/group_scope_details/
build_additional_services/catalog_counts; GCS load_catalog [NEVER raises — falls back to default so the
tool works pre-seed] / catalog_info / put_catalog [validated, generation-guarded, last-writer-wins; reuses
portal_store._blob]); orchestrators/estimate_flow.py (enrich(data, catalog=None) + scope_details/
additional_services; process_estimate loads catalog best-effort; render_estimate_pdf passes it);
templates/estimate.html.j2 (Scope Details + Additional Services pages; 3-level bullets via fixed-indent
::before glyphs for predictable WeasyPrint); web/estimate.html (Scope Selection card: catalog-driven
checkboxes → editable textarea+price+Save → "{Trade} - {Scope}" line item w/ badge + hidden scope_detail;
collectData/applyData round-trip; draft resume re-ticks boxes; admin catalog editor add/remove trades+
scopes, edit title/text/price → POST); app/service.py (EstimateScopeCatalogRequest; GET /ui/api/estimate/
scopes [estimate grant → catalog+can_manage] + POST [admin → validate+store]); scripts/
seed_estimate_scope_catalog.py (NEW, optional idempotent seed/reset). Feature gate = reuses `estimate`
(catalog EDIT = admin). NO new deps / NO Dockerfile change / NO requirements change (stdlib + existing
jinja2/weasyprint/google-cloud-storage; new module rides subsystems/ COPYed whole).
ENV (optional): GVC_ESTIMATE_SCOPE_CATALOG_OBJECT (default portal/estimate/scope-catalog.json),
GVC_ESTIMATE_SCOPE_CATALOG_CACHE_TTL (default 30). Admin EDIT needs GVC_PORTAL_STATE_BUCKET (already set
prod since 2026-07-02); READS work without it (default catalog).
TESTS: tests/test_estimate_scope_catalog.py 26 (bullet-split incl. real doc text + abbrev/decimal/newline;
validate incl. dedup/price coercion/rejects; group_scope_details ordering+fallback; build_additional_
services; default integrity 7 trades/9 scopes; enrich e2e). Sandbox: 26 pass (WeasyPrint stubbed + qrcode
installed); py_compile clean; template renders 4 pages IN ORDER w/ correct 3-level hierarchy (verified via
Jinja render + structural asserts); estimate.html JS node --check clean. FULL suite in WeasyPrint venv
NOT re-run here — run before deploy.
DEPLOY (admin): run full suite in WeasyPrint venv → `--source .` deploy (repo root, hello@) → smoke: open
/ui/estimate → Scope Selection loads → check a scope → edit + price → Save → "{Trade} - {Scope}" line
appears → Generate Preview → PDF shows Scope Details (trade→scope→bullets) + Additional Services (all 7
trades), Standard Deliverables unchanged & in order → (admin) "Manage standard scopes" → fill FRP/Doors/
Tectum → Save → reopen, new text prefills. Full design + runbook: docs/portal-estimate-scope-selection-design.md.
OPEN: fill the 3 stubbed trades w/ real scope text (in-app or --from-file); "Restore defaults" button
(needs a small reset route — for now re-run seed w/ --force); optional-scope Scope Details grouping is
combined w/ main by design (revisit if a separate subsection is wanted).

## ✨ COI GENERATOR — new portal app, BUILT 2026-07-14; PHASE 1 LIVE (smoke-tested), PHASE 2 (bulk) + revisions ship next --source . deploy
The former GM's ask: store the current blank COI (agent-issued ACORD 25, renews annually — exp May 2027),
stamp the CERTIFICATE HOLDER box (Name/Project Name + address) per the CMsquared example, draft
the email to a contact (name+email), Slack notice, Monday update (placeholder), then later a bulk
"Annual COI List" run. DESIGN LOCKED (decided 2026-07-14): template SoT = GCS STATE bucket
portal/coi/template.pdf(+ -meta.json) w/ ADMIN upload UI on the page (annual swap without deploy);
Slack = a NEW annual-maintenance channel the former GM was creating (env GVC_COI_SLACK_CHANNEL, ID-based, NO
named fallback — unset ⇒ clean skip, never misroutes); access = NEW `coi` feature (access.py
FEATURES, admins via *); Drive = dedicated "COIs Sent/<year>/" top-level tree (builder-level docs,
often pre-project — deliberately NOT under Projects/). GEOMETRY calibrated to the real CM2 COI
(its holder block = one FreeText annot; appearance stream decoded): x=42, baselines 109/99/89,
10pt leading, ~8pt Helvetica — verified our output pixel-matches the example (positions within
0.1pt; render compared side-by-side). Both agent PDFs are empty-password ENCRYPTED — template
uploads are normalized (decrypt+rewrite via pypdf) at store time.
BUILT: subsystems/coi/{stamp,template}.py (pure helpers holder_lines/wrap/coi_filename
"COI - {name} - {expiry_label}.pdf"/coi_identifier/pretty_expiry; stamp_certificate_holder
overlays via pypdf+reportlab, preserves the 121 checkbox widget annots); orchestrators/coi_flow.py
(dry-run = stamp+GCS preview; finalize = Drive→hello@ draft [dedup key COI-<slug>, re-finalize
updates the unsent draft]→Slack notify_coi_drafted→Monday placeholder→notify_finalize_degraded,
all graceful per-step); adapters/monday/coi.py = PLACEHOLDER (env GVC_MONDAY_COI_BOARD_ID unset ⇒
SKIPPED; set ⇒ bare name-only item, NO columns assumed — the former GM flagged GC Billing Profiles as a
candidate but doesn't trust it; board decision open, see docs/portal-coi-design.md §4);
drive.ensure_coi_folder; slack_notify._coi_message/notify_coi_drafted; app/service.py routes
GET /ui/coi + POST /ui/api/coi/run + GET/POST /ui/api/coi/template (POST=admin replace, validates
+normalizes) + /health slack_coi_channel; web/coi.html (header standard, preview iframe, template
card w/ admin upload); hub tile (data-feature=coi); scripts/seed_coi_template.py (CLI seed,
idempotent). NEW DEPS: pypdf + reportlab (requirements.txt — install into the WeasyPrint venv
before running the suite locally).
TESTS: +39 (test_coi_stamp 13 / test_coi_template 8 / test_coi_flow 18 incl. finalize fakes +
Monday placeholder contract + Slack wording). Sandbox suite 356 passed / 1 failed =
test_change_order_flow (KNOWN sandbox-only WeasyPrint stub). import app.service OK (49 routes,
4 COI). coi.html JS node --check clean.
DEPLOY (admin, full runbook docs/portal-coi-design.md §6): 1) full suite in WeasyPrint venv (pip
install pypdf reportlab there first) → 2) --source . deploy → 3) seed template (admin UI on
/ui/coi OR scripts/seed_coi_template.py --file "~/Downloads/COI - BLANK - expMay_2027.pdf"
--expiry-label expMay_2027) → 4) grant `coi` in /ui/admin → 5) create the maintenance channel +
invite @gvc_reporting + --update-env-vars GVC_COI_SLACK_CHANNEL=<ID> → 6) smoke: dry-run preview
→ finalize → hello@ draft w/ attachment + Drive "COIs Sent/2026/" + a DELIVERED Slack message.
PHASE 1 SMOKE-TESTED LIVE 2026-07-14 (the former GM: "test flow for a single COI worked well"; deployed w/
env GVC_COI_SLACK_CHANNEL=C0BHBDG49QS + the 07-06 pending envs; template seeded expMay_2027 via
scripts/seed_coi_template.py — NOTE: local seed hit two rakes now fixed/documented: venv lacked
google-cloud-storage [pip install -r requirements.txt] and the NEW repo never got the untracked
secrets from gvc_invoice — SA json restored via `gcloud secrets versions access latest
--secret=google-service-account > .google-service-account.json`; .gcloudignore now excludes
.google-service-account.json/.gmail-token*.json/.google-oauth-client.json so local keys never ride
--source . uploads).
PHASE 2 + REVISIONS — BUILT 2026-07-14 same day (the former GM's post-test asks), ships next --source .
deploy, NO new env/deps: (1) email subject "Green Valley Contractors — COI — {name}" (was
"Certificate of Insurance") + closing "The Green Valley Team"; (2) template upload MOVED to
/ui/admin ("COI blank template" card; /ui/coi = read-only status; GET /ui/api/coi/template gate
loosened to coi-OR-admin, POST stays admin); (3) BULK "Annual COI List" BUILT against the REAL
sheet (id 1J8CyTfjjJ5kmYWVO9YqAQ9rR7KoclV1bZZNBCPy5Fu8, ~104 rows, inspected via Drive MCP):
header sits BELOW 3 merged banner rows, cols "Client/Builder Name|Project Name|Mailing Address|
Contact Name|Contact Email|Sent" (synonyms tolerated), single-line comma addresses shaped via
split_single_line_address (strips ", USA", splits street at first comma). NEW adapters/sheets.py
(Sheets API v4 read + write_cell, SAME SA json, no new dep; pure spreadsheet_id_from_url/
column_letter) + subsystems/coi/bulk.py (PURE find_header_row/map_columns/build_plan/
entry_to_coi_payload/bulk_summary_message; ledger semantics LOCKED: YES→skip forever,
attempted-fail→NO [retries next fresh run], invalid rows NOT attempted + cell untouched,
blank contact name falls back to builder name, duplicate builders flagged [same slug ⇒ later row
overwrites draft/PDF]) + coi_flow.process_coi_bulk (dry-run = full parse + review + sample preview
of first ready row, NO writes; finalize = CHUNKED: ≤GVC_COI_BULK_CHUNK(15) ready rows past
after_row cursor per call → {results,next_after_row,remaining}, UI loops until remaining=0 —
Cloud Run timeout-safe at 104 rows + resumable; per row stamp→Drive[shared folder/chunk,
non-fatal]→hello@ draft[dedup=holder slug; Gmail failure fails the ROW]→YES/NO writeback; final
call posts ONE Slack summary of SHEET-STATE ledger [stateless truth: "X YES · Y NO · Z needing
attention"], channel unset ⇒ clean skip; Monday = skipped placeholder) + POST /ui/api/coi/bulk/run
+ coi.html "Doing bulk annual COIs? Check this box." → sheet link → Load & review table → Create N
drafts w/ live per-row results + progress. Sheet-not-shared → clean 422 naming
gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com (Editor needed for writeback).
TESTS: suite now 405 passed / 1 known WeasyPrint-stub fail in sandbox (test_coi_bulk 24 new);
import app.service OK (52 routes); coi.html + admin.html JS node --check clean.
REMAINING (admin): --source . redeploy → SHARE the sheet w/ gvc-invoice-bot@ as EDITOR → bulk smoke
per docs/portal-coi-design.md §6. OPEN: Monday board decision (then real column mapping in
adapters/monday/coi.py + bulk logging); .xlsx-upload alternative deliberately dropped (list lives
in Sheets).
CHANNEL CREATED 2026-07-14: #team-annual-maintenance, id **C0BHBDG49QS**, PRIVATE (⇒ bot
membership mandatory). @gvc_reporting INVITED + membership verified via Slack MCP 2026-07-14
(members: [former-GM acct]/andrea/jordan/claude/gvc_reporting) — channel is post-ready. Env value:
GVC_COI_SLACK_CHANNEL=C0BHBDG49QS (riding the deploy w/ the 07-06 pending envs — ops-alerts
reroute C0BE9S4C3JT membership re-verified 2026-07-14: bot IS a member).

## ✨ PARTIAL CHECK PAYMENTS (Paid by Check) — BUILT 2026-07-06, ships next --source . deploy
INCIDENT: a builder's check partially paid several invoices; the flow's only options were
full-pay or "Record anyway" — the override marked every invoice FULLY paid in Stripe+Monday
(note "Out of band payment for invoice K1KNZJ6W-0002"). ⚠ Stripe CANNOT un-pay a paid invoice —
those rows need manual cleanup (get identifiers from Jordan; options: new balance-due invoice for
the shortfall [recommended] or Monday-only correction). DESIGN LOCKED (decided 2026-07-06, all three
recommended options accepted): (1) Stripe = NATIVE partial out-of-band — PaymentRecord.
report_payment + Invoice.attach_payment (basil 2025-03-31+; shipped SDK stripe-python 15.3 pins
2026-06-24.dahlia, both methods verified present); invoice stays open w/ accurate
amount_remaining, auto-flips paid at full coverage. (2) UX = editable per-invoice allocation
amounts, prefilled to each invoice's remaining balance, oldest-first auto-split helper;
allocations must sum EXACTLY to the check (no blanket override in split mode). (3) Monday =
new Status label "Partially Paid" (auto-created via create_labels_if_missing; NOT in
CLOSED_INVOICE_STATUSES → row stays in the picker) + NEW "Balance Due" numeric column synced
from Stripe amount_remaining at every commit; matcher/sum-gate now use remaining balance
(deposit.effective_amount_cents) not original Amount.
BUILT: stripe_invoice.record_partial_out_of_band (idempotency key gvc_check_{no}_{ident}_{cents},
24h double-credit shield); deposit.py pure suggest_allocations/validate_allocations/
partial_note_line/effective_amount_cents; client.py set_invoice_partially_paid (+Balance Due
sync; set_invoice_paid now zeroes Balance Due) + INV_COL_BALANCE_DUE (env
GVC_MONDAY_BALANCE_DUE_COL, EMPTY=feature-off, reads/writes skip gracefully) + dynamic
_invoice_column_ids; check_flow commit_check(+allocations dict) — ALLOCATED mode: Stripe-
authoritative balance gate (409 ALLOCATION_INVALID), partial→PaymentRecord+Monday
PartiallyPaid+balance, full-balance alloc→classic paid_out_of_band, retry: note-line dedupe +
already-deposited skip + banked-amount accounting; legacy no-allocation mode UNCHANGED (its
SUM_MISMATCH advice now mentions partials); extract returns balance_due/effective_cents +
suggested_allocations; service.py commit route +allocations JSON form field; check.html
allocation editor (per-invoice apply-$ inputs, partial badges, auto-split button, over-alloc
guard; override checkbox hidden in split mode). scripts/add_balance_due_column.py creates the
column + prints the id (idempotent).
DEPLOY ORDER (admin): 1. ✅ DONE 2026-07-06 — "Balance Due" column created on 1931784889 via the
Monday MCP: id **numeric_mm50nvjq** (scripts/add_balance_due_column.py kept as idempotent
verify) → 2. --source . deploy WITH --update-env-vars
GVC_MONDAY_BALANCE_DUE_COL=numeric_mm50nvjq (rides the same deploy as the ops-alerts reroute +
stale-pointer payloads) → 3. LIVE SMOKE (required — Payment
Records API may be account-gated; if report_payment 404s/errors, the partial fails clean per-
invoice and everything else still works; fallback then = Monday-only partial tracking): take a
real open invoice, record a small partial → Stripe invoice shows partially-paid w/ correct
amount_remaining, Monday row = "Partially Paid" + Balance Due + note; then a second check for
the remainder → invoice flips paid, row → Paid, Balance Due 0.
TESTS: tests/test_check_partial_payment.py (12: pure x5 + commit x7). Suite 318 green in
sandbox (was 306); import app.service OK; check.html JS node --check clean.
KNOWN GAPS: (a) retry where Stripe partial succeeded but Monday write failed AND >24h passed
could double-credit on re-run (idempotency window) — note-line dedupe covers the common case;
(b) hosted invoice page doesn't accept partial ONLINE payments (Stripe limitation) — partials
are check-side only; (c) the wrongly-full-paid invoices from the incident need manual cleanup.

## 🔁 RENAME — Slack "#leads" channel is now "#bids" (2026-07-06)
SAME channel id C0B562L3PTR — the portal posts by ID (GVC_ESTIMATES_SLACK_CHANNEL=C0B562L3PTR),
so NO env/code/deploy change; bot membership survives a rename. Estimate + revision notices
keep landing. Dated entries below say "#leads" — same channel. Pairs with the 07-02 "Bid Board"
board rename. SWEEP DONE 2026-07-06: root ~/Documents/GVC/CLAUDE.md, this file's current-state
lines, estimate.html UI text, code comments + test literals all updated to "#bids"; the report
system was checked and has NO #leads-by-name references (nothing to break). Historical incident
entries below intentionally keep the "#leads" wording.

## 🔧 STALE STRIPE POINTER on Invoices board — check-commit SELF-HEAL + correction hardening (BUILT 2026-07-06, ships next --source . deploy)
INCIDENT (Greg Gavin, CU-0166): check recording failed "Stripe [CU-0166]: Voided invoices cannot
be paid" while the CURRENT CU-0166 was OPEN in Stripe ($11,300, in_1TgkRWApcln2OQNSddVVqOf2).
ROOT CAUSE: the Monday row's Stripe Invoice ID (text_mm3qse5f) still pointed at a VOIDED Jun-9
invoice — the bill predates the server-side ledger write (~06-25), so the void+re-bill never
repointed the row. (User-confusion note: Stripe's Customers→Payments tab shows PAYMENTINTENT
states — "Incomplete"=abandoned pay page, "Canceled"=PI auto-canceled when its invoice was
voided. Invoice status lives on the Invoices tab.) The former GM fixed the row by hand 2026-07-06; retry OK.
CONFIRMED: the "correction must update the board" core is ALREADY covered since the 07-03 deploy
(write_invoice_ledger upserts on Document # on EVERY live run — same-number re-bills repoint the
row; correction revisions void the old row + create the Rev-N row). What was missing = healing
for rows that predate it + a fallback when a row still points at a void invoice.
BUILT (all tested, 306 suite green in sandbox, import app.service OK; NO new deps/env/Dockerfile):
  • CHECK-COMMIT VOID FALLBACK + SELF-HEAL (check_flow.commit_check): _stripe_state now also
    returns the invoice's customer; if a row's Stripe invoice is "void", resolve the CURRENT
    invoice via NEW stripe_invoice.find_current_invoice_for_identifier(identifier, customer_id)
    — merges the customer-scoped Invoice.list (strongly consistent) + metadata gvc_invoice_id
    Search (customer-independent), ranks via pure pick_current_invoice (open>draft>paid>
    uncollectible, never void/zombie, newest on tie) — pay THAT invoice, then repoint the row
    (NEW MondayClient.repoint_invoice_stripe: writes text_mm3qse5f + pay-link link_mm3qg47g +
    idempotent note; Status untouched). No replacement found → clean per-invoice COMMIT_PARTIAL
    error ("row points at VOID in_x, likely reissued — fix the row's Stripe Invoice ID") instead
    of the raw Stripe error; other invoices on the check still record.
  • CORRECTION HARDENING (invoice_flow): revision void-note now carries the NEW Stripe id
    ("Voided — reissued as X (Stripe in_y) on DATE"); revision result gains step
    "ledger_row_missing" when the new row didn't sync; notify_finalize_degraded now ALSO fires
    on a failed/missing ledger write (was silent — the failure class behind this incident).
  • ONE-TIME AUDIT: scripts/audit_invoice_ledger.py — sweeps open rows on 1931784889 for void/
    dead Stripe pointers, reports; --fix repoints via the same resolver. Run locally from repo
    root w/ MONDAY_API_TOKEN + STRIPE_API_KEY env (read-only without --fix). RUN THIS ONCE —
    more pre-06-25 rows may be stale.
TESTS: NEW tests/test_check_void_fallback.py (9: ranking x4 + commit fallback x5, all I/O faked).
Suite 306 passed in sandbox (was 297). SMOKE after deploy: record a check against a row pointing
at a voided invoice → expect payment on the replacement + "row Stripe id fixed: in_old → in_new"
step + row's Stripe ID column updated.
ALSO PENDING (decided 2026-07-06): REROUTE portal error alerts #gvc-ops-alerts → the NEW
#portal-bugs-and-improvements (PRIVATE, id C0BE9S4C3JT, created by Andrea 07-02). Env-only:
--update-env-vars GVC_OPS_ALERTS_CHANNEL=C0BE9S4C3JT (moves the 5xx handler + degraded-finalize
+ grants-tripwire alerts; estimate/CO/billing notices unmoved; the report system's webhook posts
to #gvc-ops-alerts are separate and stay). REQUIRES @gvc_reporting invited to the channel
(private ⇒ membership mandatory). Verify by a DELIVERED message (force a 5xx), never config
presence. Once verified, update the channel table in the 06-29 CURRENT STATE entry.

## ✨ MULTI-INVOICE CHECKS (Paid by Check) — DEPLOYED + VERIFIED LIVE 2026-07-03
Andrea's ask: one check + stub covering SEVERAL invoices — record it against ALL of them.
DESIGN LOCKED (decided 2026-07-03): each selected invoice is paid IN FULL (Stripe
pay(paid_out_of_band) is all-or-nothing per invoice; partials/short-pays = manual, out of
scope); sum gate = selected invoices must total the check amount, WARN + explicit
"Record anyway" override (server enforces via 409 SUM_MISMATCH unless allow_mismatch=1 —
UI checkbox mirrors it); check image filed into EVERY invoice's Drive folder (per-project
payment record, idempotent per filename); amount-COMBINATION auto-suggest included.
BUILT: subsystems/checks/deposit.py — NEW pure match_invoices (stub identifiers in
memo/reference: ALL hits matched high [was: >1 hits = ambiguous "pick one"]; falls back to
old single rules; then find_amount_combination), find_amount_combination (unique-subset-sum
over the payer's open invoices, fail-safe: ambiguous/no-subset/>20 priced rows → None, subset
cap 6), sum_check, multi_deposit_plan (per-invoice deposit_plan + ALREADY_DEPOSITED_MULTI_
MESSAGE when every invoice already paid; retry re-plans + skips recorded invoices).
check_flow.py: extract returns `matches` list (+`match` kept for single-hit compat);
commit_check now takes monday_item_ids (deduped) + allow_mismatch — re-fetches every row,
per-invoice Stripe state, guard, sum gate, then per invoice Stripe→Monday→Drive; per-invoice
failures DON'T stop other invoices (errors aggregated → 502 COMMIT_PARTIAL, retry-safe);
one Slack notify_payment_recorded for the batch. monday/client.py set_invoice_paid
+covers=[ids] → note reads "Paid by check #N on DATE (check covers 3 invoices: A, B, C)"
(idempotent, note-line dedupe unchanged). app/service.py commit route: +monday_item_ids CSV
(legacy monday_item_id still honored) +allow_mismatch. web/check.html: multi-select picker
(toggle rows), running total vs check amount, mismatch warning + "Record anyway" checkbox,
matches pre-selected, per-invoice step results. TESTS: tests/test_check_deposit.py 34 green
(+11); FULL suite 297 green in sandbox (all deps installed); import app.service OK; check.html
JS node --check clean. NO new deps/env/Dockerfile change.
✅ DEPLOYED + VERIFIED LIVE 2026-07-03 (the former GM: "everything worked without a hitch") — the
multi-invoice flow works in prod end-to-end. ⚠ NOTE: this `--source .` deploy ALSO shipped the
pending 2026-07-02 payloads (grants missing-store tripwire, /health live Slack token probe,
estimate revision, Bid Board rename) — those are now LIVE but each still needs its OWN
smoke test per its entry below (esp. /health slack_token_ok + slack_bot_user=gvc_reporting
after the token fix, and the estimate-revision e{n}- archive flow).

## 🚨 INCIDENT — grants wiped by preview-bucket lifecycle rule (2026-07-02)
Team locked out; /ui/admin showed zero users; [former-GM acct] fine (env superadmin). ROOT CAUSE:
`portal/grants.json` lives in the PREVIEW bucket (GVC_PORTAL_STATE_BUCKET never set) and the
bucket-wide `{Delete, age:7}` lifecycle rule from docs/cloud-run-deploy.md §4b deleted it 7 days
after the last admin write (each write resets the object's age; edits stopped → clock ran out
overnight Jul 1→2). `_read_object` treats NotFound as an empty doc + access.py denies by default
→ silent org-wide lockout, no alert. Ruled out: env-var wipe (live /health showed all current
Slack env intact) and backend flip (empty user list, not the read-only banner). Same-bucket
exposure: estimate-drafts.json (survives only via frequent rewrites) + hr_private.json.
FIX (the former GM, gcloud via hello@ — full runbook docs/incident-2026-07-02-grants-lifecycle-wipe.md):
restore via GCS soft delete (7-day window; deletion was <24h old) → create dedicated
`gs://gvc-portal-state` (versioning ON, NO lifecycle) → copy portal/* over → set env
GVC_PORTAL_STATE_BUCKET=gvc-portal-state (--update-env-vars; portal_store AND estimate drafts
both follow it — zero code change) → rm portal/* from the previews bucket. Fallback
reconstruction: `admin.grant.update` activity events log target+features (60d retention).
BUILT 2026-07-02 (ships next --source . deploy): missing-store TRIPWIRE — portal_store fires
MISSING_STORE_HOOK (throttled 1h/instance, GVC_PORTAL_STORE_ALERT_INTERVAL) on any fresh gen-0
read; app/service.py wires it → slack_notify.post_failure → #gvc-ops-alerts (gcs-gated; layering
preserved: hook in shared/, wiring in app/). /health += grants_backend / grants_store_ok /
grants_users — health probes arm the tripwire even with zero sign-ins.
tests/test_grants_tripwire.py 7 green (24 with store+access suites); no new deps/Dockerfile
change. STANDING RULE: ephemeral + durable objects NEVER share a lifecycle-ruled bucket; all
portal state goes in the state bucket via portal_store.

## 🔴 ROOT CAUSE — portal Slack notices NEVER posted: placeholder token in the secret (2026-07-02)
The former GM: "portal updates not writing to Slack." Channel evidence: @gvc_reporting has NEVER posted in ANY
of the 4 portal channels (#leads's last real post = the Monday automation, 2026-06-23; #change-orders /
#billing / #gvc-ops-alerts: zero bot posts ever) — while the report system's daily brief posts every
morning through the SAME bot + chat.postMessage (confirmed Jul 2 07:00). So app/scopes/membership are
fine; the fault is portal-side. ROOT CAUSE: the PORTAL's `slack-bot-token` secret (project
gvc-invoice-system, seeded 2026-06-23) contains the literal 8-byte REDACTED PLACEHOLDER `xoxb-…` —
pasted from the docs' redaction (gvc_report_system/CLAUDE.md writes the token as "xoxb-…"), never the
real token. auth.test on it → invalid_auth → post_message fail-fast RuntimeError, swallowed non-fatally
on every finalize since 06-23. The 06-23 "VERIFIED via /health" note only ever proved CONFIG PRESENCE
(third instance of this trap: #leads membership 06-26, "LIVE" 06-29, this). The report project's copy
(gvc-report-system/slack-bot-token) is the real 59-byte token, no trailing newline — the known-good source.
FIX (the former GM): `printf %s "$RTOKEN" | gcloud secrets versions add slack-bot-token --data-file=-` on
gvc-invoice-system (printf %s = byte-exact, no trailing newline), then the pending `--source .` deploy
(same one shipping the grants tripwire) rolls the revision that picks it up. SMOKE: estimate → #bids ·
live invoice → #billing · CO → #change-orders · forced 5xx → #gvc-ops-alerts.
STANDING RULES: (1) NEVER seed a secret from a doc value — docs redact; pull from the source system or
api.slack.com. (2) A Slack notice is verified ONLY by a delivered message, never by config presence.
BUILT 2026-07-02 (AFTER the former GM's token-fix deploy kicked off → needs ONE MORE `--source .` deploy):
/health live token probe — slack_notify.auth_test() + probe_token() (cached, env
GVC_SLACK_AUTH_PROBE_TTL default 300s; transport errors incl. corrupt-token header breakage never
raise, come back as "network: ..."). /health: slack_configured NOW MEANS "token WORKS" (auth.test ok),
+= slack_token_ok / slack_auth_error / slack_bot_user (expect "gvc_reporting"). Would have caught this
incident on day 1. tests/test_slack_notify.py 25 green (+8). No new deps/env/Dockerfile.
POST-DEPLOY CHECK: /health → slack_configured=true + slack_bot_user=gvc_reporting; if
slack_auth_error=invalid_auth the secret fix didn't take.

## 🔁 RENAME — "Opportunities" board is now "Bid Board" (2026-07-02, code updated)
The former GM renamed board 1918846027 in Monday; SAME board id, no column/group changes. Code renamed to
match: shared/boards.py `BID_BOARD_ID` (env `GVC_MONDAY_BID_BOARD_ID`; legacy
`GVC_OPPORTUNITIES_BOARD_ID` override STILL HONORED — no prod env change needed);
monday/estimate.py `lookup_bid` (was lookup_opportunity) + `search_bids` (was
search_opportunities); estimate.html card = "Find the Bid", JS loadBid/searchBids; user-facing
error/advice text says "Bid Board"/"bid". Client-facing email wording ("the opportunity to bid")
untouched. Dated entries BELOW this one say "Opportunities" — same board. docs/*.md design docs
left as historical record. ⚠ Root `~/Documents/GVC/CLAUDE.md` + the report system may still say
"Opportunities" — outside this repo, update on next touch.

## ✨ ESTIMATE REVISION ("Update this Estimate") — BUILT 2026-07-02, ships next --source . deploy
The former GM's ask: customer requests a change → find the sent estimate, prefill EVERYTHING, one action
updates it. DESIGN LOCKED (decided 2026-07-02): the OUTBOUND estimate number NEVER changes on revision
(client always sees the same YYYY-MMDD-NNN; once agreed it propagates through COs/invoices);
prior versions archived in the project's Estimate/ Drive folder by RENAME with `e{n}-` prefix
(e1- = original; live file = canonical name; file IDs/links preserved); Monday columns OVERWRITTEN
on revise (scope/dates/rounded total/project type — Commission Recipient stays first-attribution-
wins); Slack #bids revision notice + fresh hello@ draft (create_draft's dedup-by-identifier
updates the existing unsent draft in place).
KEY ENABLER — as-sent JSON sidecar: finalize now ALWAYS writes `<identifier>.gvc-est.json`
(exact input data) next to the PDF (mirrors the invoice .gvc.json). Revision lookup loads it →
full prefill incl. line items. Pre-sidecar estimates degrade to Monday metadata prefill +
re-enter line items (UI says so). VERSION-SAFETY INVARIANT: a previously sent PDF is never
overwritten — finalize archives any name-collision (e{n}-) before uploading, revise or not.
BUILT: NEW subsystems/estimate/revision.py (pure: sidecar_filename/estimate_pdf_filename/
next_archive_name [e-counter scoped PER filename — two estimates can share a folder]/
archive_version/merge_revision_prefill). drive.py +find_child_file +rename_file.
slack_notify: notify_estimate_drafted(+revised,+version), pure _estimate_message.
monday/estimate.py: pure build_column_updates(current,est,job,estimate_number,revise) —
fill-if-empty vs overwrite; write_back(+revise); lookup_bid += numbers18 →
`_existing_estimate`; NEW search_bids(mc,q) (name OR Estimate# contains, 2 queries
merged, cap 15). estimate_flow: process_estimate(+revise) [revise requires identifier];
finalize Drive step = archive→upload→sidecar; revision_version (v2 = first revision) threaded
to Slack; _compose_email_body(+revised) ("revised estimate … supersedes the previous version").
service.py: EstimateRunRequest +revise (both /ui + /v1 run routes pass it; activity target
"finalize+revise"); lookup route loads sidecar via find_file_anywhere→download_json→
merge_revision_prefill (best-effort, returns `revision` {estimate_number, sidecar_found,
prior_total}); NEW GET /ui/api/estimate/search?q=. estimate.html: prefill card = "Find the Bid"
(URL OR text search → result rows w/ Est#/stage badges → Load); revision banner; revise
checkbox row (shown when identifier non-empty; auto-checked on revision load); Accept
button/confirm/result wording switches in revise mode; wb.drive_archived shown.
TESTS: tests/test_estimate_revision.py 16 green; suite 253 pass in sandbox (2 fails =
known sandbox-only google.api_core; test_change_order_flow needs WeasyPrint venv);
`import app.service` OK. NO new deps/env/Dockerfile change (packages COPYed whole).
RUN FULL SUITE IN THE WEASYPRINT VENV BEFORE DEPLOY. SMOKE-TEST after deploy: finalize a NEW
estimate → sidecar JSON lands next to the PDF; search for it in the form → "Load for revision"
→ all fields incl. line items prefill; change a price → Update this Estimate → Drive shows
e1-… archive + canonical PDF+sidecar replaced, Monday total/dates overwritten (same Estimate #),
#bids shows "REVISED (v2)" wording, hello@ draft updated in place. KNOWN GAPS: draft-autosave
resume doesn't persist the revise checkbox (re-check it); pre-sidecar estimates need manual
line-item re-entry; double-finalize of the same revision creates a harmless extra e{n}- archive.

## ⚡ CURRENT STATE — Slack-first ops push + commission capture (2026-06-29)
Read this first for deploy status; the dated entries below have the detail.

**LIVE (deployed + /health-confirmed):**
- Slack notices via the @gvc_reporting bot (SLACK_BOT_TOKEN): estimate→#bids (named #leads until
  2026-07-06), CO→#change-orders,
  invoice-sent + payment-recorded→#billing, and 5xx + degraded-finalize failures→#gvc-ops-alerts.
- All 4 channels set as rename-safe IDs: estimate `C0B562L3PTR` · change-orders `C0BAY7ZQ0LD` ·
  billing `C0BDCL2V10W` · ops-alerts `C0B7BM3FBCY`. Bot is a MEMBER of all four (required — name-based
  posting failed; IDs + membership are the rule). /health reports all four + slack_configured.
- CO Drive auto-save (finds the project folder → Change Orders/, no pasted link), unicode-safe Slack body.
- Monday automation **1939926355 is fully OFF**; the portal now owns what it used to do (the #bids notice
  + the New Deals→Open Deals move).

**DEPLOYING NOW (code on disk; the Monday column already exists live):**
- Commission Recipient capture — write_back persists the salesperson to Opportunities status col
  **`color_mm4sy4eq`** (fill-if-empty); company account ("Green Valley Contractors") is in the dropdown.
- Estimate finalize promotes **New Deals → Open Deals** (`new_group__1`→`topics`).

**CONFIRM POST-DEPLOY (admin):** finalize an estimate → #bids post + Stage=Sent + moved to Open Deals +
Commission Recipient filled; a CO → #change-orders; a live invoice → #billing; force a 5xx → #gvc-ops-alerts.

**OPEN / NOT BUILT:**
- Simplified "Sales Team" admin — reps still added via /ui/admin; the company account is built-in.
- Commission **payout report** (phase 2): commission earned when the invoice is **PAID** (cash basis);
  the "Green Valley Contractors" recipient's accrual = the team bonus pool. Belongs in the report system;
  needs per-rep rates/rules from Jordan. Join: recipient on the Opportunity ↔ paid status on Invoices.
- Coverage gap: the portal posts/promotes only on portal-driven finalizes; a manual Monday stage move
  won't fire them.

---

## Active project: Internal portal + estimate system

**Portal** = portal.greenvalleycontractors.com → Cloud Run service `gvc-invoice`
(project `gvc-invoice-system`, us-central1, repo: `GVC_Portal_System/` — was
`gvc_invoice/`; see REPO STRUCTURE banner at top). Static HTML
front-end served BY the service; all code on gcloud; one Google sign-in front
door (in-app OAuth, free path — NOT IAP/LB). Vision: one hub page listing every
employee tool. MCP keeps X-API-Key on the run.app URL, untouched.

### ⚠ ARCHITECTURE PIVOT (2026-06-18) — CO subitems are OUT, read FIRST
Note (2026-06-18): Jordan does NOT want to use Monday subitems at all — a prior
attempt (before the former GM joined) went south. This REVERSES the locked "CO HOME = subitems
of Projects board" decision. The Change Order program currently writes COs as subitems
of Projects (board 1918846408) and the CO-billing writeback flips a subitem Status —
BOTH are now on hold pending an alternative org model. The former GM was to meet Jordan NEXT WEEK
to decide the replacement (candidate options NOT yet chosen — e.g. COs as top-level
items on a dedicated CO board, or columns on the parent Project item — do NOT assume).
DO NOT build more on subitems or deploy subitem-dependent CO code until that meeting
resolves the new SoT. The CO *create* + *billing-writeback* code stays on disk, but its
Monday-write layer (monday_co.py create_co_subitem / mark_billed* / list_billable_cos)
will need rework to the new model. Front-end CO billing assemblers = PAUSED until then.

### INVOICE CORRECTION / REISSUE — built 2026-06-23 (Layer 1 LIVE+verified; rest: confirm live revision)
INCIDENT: Andrea billed to a WRONG client email (Jordan gave the right one after a mailbox
bounce). Re-running to correct it repeatedly threw the scary catch-all "UNEXPECTED — invoice
MAY have been partially created, Do NOT retry." Voiding/deleting the Stripe invoice didn't help.
ROOT CAUSE (code-confirmed): editing the email tripped TWO bugs. (1) The dedupe guard
(invoice.preflight_stripe) found the existing invoice ONLY by listing invoices under the
EMAIL-matched Stripe customer → new email = no customer match → original looked invisible →
live path went to CREATE. (2) create_stripe_invoice's idempotency_key is identifier-scoped
(gvc_inv_v3_{identifier}_create); the corrected re-run reused that key with a DIFFERENT
customer → Stripe IdempotencyError, which service._friendly_error didn't classify → fell to
the UNEXPECTED catch-all. Stripe's idempotency cache (~24h) survives void/delete → same error
on every retry. NOTHING was ever partially created — the alarm was false.
FIXES (all in gvc_invoice/; sandbox tests green; design doc: docs/portal-invoice-correction-design.md):
  • Layer 1 — DEPLOYED + VERIFIED LIVE: _friendly_error maps IdempotencyError (by class name OR
    the "idempotent requests can only be used" message) → 409 IDEMPOTENCY_CONFLICT with calm
    wording ("Nothing was partially created… use Correct/Reissue"). Andrea confirmed she now
    sees this, not UNEXPECTED.
  • Layer 2 — email-proof dedupe: preflight_stripe falls back to a Stripe Invoice SEARCH on
    metadata gvc_invoice_id (invoice._find_invoice_by_identifier_metadata) so an email edit still
    finds the original → reuse, not crash; sets writeback correction_hint on email mismatch.
    ⚠ Stripe search is EVENTUALLY-CONSISTENT (lags on quick retries) → reuse may not fire
    immediately. Hardening = the Monday-ledger fallback in BACKLOG below.
  • Layer 3 — correction as a DIFF (invoice_correct.py, pure+tested): diff_payload(original,
    corrected) + route_for_changes → noop / in_place (only non-monetary safe fields:
    email,cc,contact_name,phone,billing_address,email_context) / revision (anything monetary or
    doc-level). in_place edits the EXISTING Stripe invoice (customer fields) + refreshes
    PDF/Drive/Monday/Gmail, SAME number + hosted URL. revision = Stripe NATIVE revision
    (Invoice.create from_invoice → auto-voids the original on finalize) under "… Rev N".
    ⚠ from_invoice COPIES the original's lines → create_stripe_invoice(from_invoice_id=) CLEARS
    the copied lines before attaching corrected ones (else double-bill); threaded via
    process_one(from_invoice_id=). New primitives: invoice.void_stripe_invoice (refuses to
    auto-void paid/uncollectible), gmail.delete_draft_by_invoice_id, monday.find_invoice_row_by_
    document + monday.set_invoice_void. service._run_correction orchestrates (intent auto|recipient).
  • AS-BILLED JSON PERSISTENCE (the former GM's call: Drive, NOT a GCS cache): process_one live writes
    "<identifier>.gvc.json" (the exact input `data`) NEXT TO the PDF (same Completed-Invoices
    subfolder) via DriveUploader.upload_or_replace_file (idempotent, non-fatal; writeback
    drive_json_file_id). Read back via DriveUploader.find_file_anywhere + download_json → GET
    /ui/api/invoice/original?identifier=. So a correction pulls the TRUE original from Drive.
    (Only invoices billed AFTER this deploys have a sidecar; older ones fall back to Rev N reissue.)
  • UI — corrections live INSIDE the invoice generator (decided: NOT a separate tile/interface).
    REMOVED the standalone GET /ui/correct route + hub tile. web/correct.html is now ORPHANED on
    disk (sandbox blocked the delete — delete it in the repo). web/invoice.html has a MODAL
    (openCorrectionFlow): loads the saved original from Drive → auto-diff/route/apply; if no saved
    original → offers "Fix recipient" (intent=recipient; reads OLD email straight from Stripe — no
    reconstruction) or "Reissue as Rev N" (bulletproof: bumps number, normal live run). Triggered by
    an actions-row "Correct / reissue…" button, the inline button on an already_existed result, AND
    injected onto the IDEMPOTENCY_CONFLICT error (kills the dead-end). Endpoints: POST
    /ui/api/invoice/correct (intent auto|recipient), GET /ui/api/invoice/original. Dockerfile COPY
    += invoice_correct.py.
DEPLOY STATUS: a `--source .` deploy made Layer 1 LIVE (verified) and shipped Layers 1–3 +
  sidecar + the (now-removed) standalone page/tile. The IN-GENERATOR MODAL RESTRUCTURE
  (tile/page removal + modal + error-branch button) was built AFTER that deploy → CONFIRM it's on
  the live revision; if not, redeploy from the REPO ROOT: `cd ~/Documents/GVC/gvc_invoice &&
  gcloud run deploy gvc-invoice --source . --region us-central1 --project gvc-invoice-system
  --account=hello@greenvalleycontractors.com`. NO new env vars/deps. Run the FULL suite in the
  WeasyPrint venv before deploy (sandbox: 46 new tests green, suite collects 238, 235 pass; the 3
  failures are sandbox-only missing deps — google.api_core, stubbed WeasyPrint). The live
  from_invoice revision path can only be smoke-tested against REAL Stripe.
VERIFIED IN-PRODUCT 2026-06-23: Andrea hit the block, used the in-generator correction flow, and
  corrected the invoice successfully ("Success"). Earlier she also voided the original wrong-email
  invoice in the Stripe dashboard herself.
BACKLOG (next, none scheduled): (1) MONDAY-LEDGER FALLBACK for reuse detection — look the invoice
  up by Document # on board 1931784889 → stripe_invoice_id → Invoice.retrieve (strongly
  consistent, email-independent) so the in-place path fires reliably without waiting on Stripe
  search-index lag (keeps the SAME number more often). (2) DELETE orphaned web/correct.html.
  (3) Pre-existing creation-side ORPHAN-DRAFT gap (separate 2026-06-18 finding) still open.

### SLACK NOTICES EXPANDED + #leads ROOT CAUSE FOUND (2026-06-26, built; needs deploy + channel invites)
The former GM turned off the Monday "Bid Sent Notice" automation (1939926355) believing the portal
had taken over #leads. It hadn't been posting: the portal's estimate notice was *configured*
(/health slack_configured=true, channel C0B562L3PTR) but the GVC Reporting bot (@gvc_reporting,
the SLACK_BOT_TOKEN identity) was **not a member of #leads**, so every chat.postMessage failed
`not_in_channel` and was swallowed non-fatally → zero portal-format posts ever landed. The 2026-06-23
"VERIFIED" note below only confirmed CONFIG, never a delivered post. FIX (the former GM, done): invited
@gvc_reporting to #leads. **Operating rule: this bot needs explicit channel membership for every
channel it posts to — chat:write.public is NOT carrying it.**
NEW notices built this session (adapters/slack_notify.py + orchestrator/app wiring; stdlib urllib,
NO new deps → plain `--source .` deploy):
  • **Ops failure alerts → #gvc-ops-alerts** (the keystone for Slack-as-UI: failures must be visible,
    not buried in logs). Two layers: (1) a single `@app.exception_handler(StarletteHTTPException)` in
    app/service.py fires `slack_notify.post_failure` on **5xx only** (UNEXPECTED, GMAIL_TOKEN_EXPIRED,
    STRIPE_AUTH, *_NOT_CONFIGURED, COMMIT_PARTIAL) then delegates to FastAPI's default handler so the
    response is unchanged; 4xx (INVALID_INPUT, IDEMPOTENCY_CONFLICT, …) are deliberately NOT alerted
    (user-actionable noise). (2) `notify_finalize_degraded` fires when a finalize returns 200 but a
    swallowed step failed (Gmail draft / Drive save / the Slack notice itself) — the exact invisible
    class that hid #leads. Wired into estimate_flow + invoice_flow live paths.
  • **Invoice sent → billing channel** (`notify_invoice_sent`, invoice_flow live, genuine new sends only —
    skips already_existed + hosted_url_override).
  • **Payment recorded → billing channel** (`notify_payment_recorded`, check_flow commit success).
  • post_failure is fire-and-forget (never raises, swallows SlackNotConfigured) — mirrors the report
    system's slack_notifier contract. /health now reports slack_ops_alerts_channel + slack_billing_channel.
ENV (set via hello@ `--update-env-vars`; channel IDs preferred, rename-safe):
  GVC_OPS_ALERTS_CHANNEL (=#gvc-ops-alerts, id C0B7BM3FBCY) · GVC_BILLING_SLACK_CHANNEL (=#billing, id
  C0BDCL2V10W, **PRIVATE** — @gvc_reporting confirmed member + Andrea/Jordan present, 2026-06-26).
  REMAINING (admin): **invite @gvc_reporting to #gvc-ops-alerts** — as of 2026-06-26 the bot is NOT a
  member (only the former GM and @andrea), so the portal's BOT-posted failure alerts will silently drop until it's
  added. (NB: the report system posts to that channel via an incoming WEBHOOK, which needs no membership;
  the portal posts via chat.postMessage, which does.) Then deploy + smoke-test (finalize an estimate →
  #leads post; create a live invoice → #billing post; force a 5xx → #gvc-ops-alerts post). Tests:
  tests/test_slack_notify.py 17 green (added 8). Run the full WeasyPrint-venv suite before deploy.
  Coverage note: the portal only posts on portal-driven finalizes; manual Monday stage→"Sent to Client"
  moves won't post (the old automation caught those).

### COMMISSION CAPTURE + estimate group fix (2026-06-29, built; ships next --source . deploy)
Commission tracking — CAPTURE layer (payout report = phase 2). Decisions (the former GM + Jordan): commission is
earned when the related invoice is **PAID** (cash basis); recipient == salesperson == bid contact (ONE
field — the former GM was folding Jordan's bid-contact identity into a company account to decouple him).
  • **New Monday column "Commission Recipient"** (status) on Opportunities 1918846027 = **`color_mm4sy4eq`**,
    seeded label "Green Valley Contractors". Created via the Monday API 2026-06-29.
  • **`monday/estimate.py` write_back** persists it FILL-IF-EMPTY (first attribution wins; never clobbers) =
    `prepared_by.name`, via `_set_status_create_labels` (create_labels_if_missing:true → new reps' labels
    auto-add). Const `COL_COMMISSION_RECIPIENT`.
  • **Company account** = a built-in salesperson in the dropdown (`/ui/api/estimate/salespeople`), env-
    overridable `GVC_COMPANY_SALESPERSON_NAME|EMAIL|PHONE` (defaults: "Green Valley Contractors" /
    hello@greenvalleycontractors.com / (513) 912-2235). Selecting it sets the estimate bid contact AND
    routes commission to the company label (matches the seeded Monday label) → team bonus pool.
  • **Simplified "Sales Team" admin = NOT built yet** — reps are still added via /ui/admin (grant `estimate`
    + name/phone); the company account is built-in. Proposed as the next increment.
  • **Payout report (phase 2, NOT built):** sum commissions per recipient from PAID invoices, treating the
    "Green Valley Contractors" recipient's accrual as the team bonus pool. Lives naturally in the report
    system; needs the per-rep rate/rules from Jordan. Note the join: recipient is on the Opportunity, paid
    status is on the Invoices board.

### NEW DEALS → OPEN DEALS on estimate finalize (2026-06-29) — retired-automation parity
The former GM: finalized estimates were landing in "New Deals (For Estimate)" not "Open Deals". Root cause: turning
OFF automation 1939926355 (earlier this session) also killed its "move to Open Deals" action. Portal now
owns it: `_create_item` creates in **Open Deals** (`topics`) — the portal only creates at finalize, i.e.
estimate already sent — and `_promote_to_open_deals` moves an EXISTING item from New Deals (`new_group__1`)
→ Open Deals on finalize. Only moves FROM New Deals, so Won (`duplicate_of_active_deals__1`) / Lost
(`closed`) / already-Open deals are never disturbed. Best-effort (non-fatal). Opportunities groups:
New Deals `new_group__1` · Open Deals `topics` · Won `duplicate_of_active_deals__1` · Lost `closed`.
Tests: tests/test_estimate_commission.py (5 green — promote/skip/create-in-open/label-create).

### CO Drive AUTO-SAVE + Slack-fully-wired + estimate unicode bug (2026-06-26, built; ships w/ same deploy)
Three fixes batched with the notices above:
  • **Estimate Slack UnicodeEncodeError ('latin-1' … …) — was the OLD deployed revision; current code
    is safe.** `slack_notify.post_message` json.dumps→ASCII (ensure_ascii) then utf-8-encodes the BODY and
    sends only ASCII headers — proven latin-1-safe by round-tripping …/—/•/emoji through the real urllib
    stack. The deploy that shipped the notices already resolves it. Regression-guarded:
    tests/test_slack_notify.py::test_unicode_body_is_latin1_safe asserts the wire body is ASCII bytes.
  • **CO Drive auto-save (no pasted link).** `change_order_flow` finalize step 1 now: if a Monday/pasted
    GFolder URL is present it still wins (ensures the "Change Orders" subfolder under it); ELSE it
    auto-resolves the project's own folder via the NEW `DriveUploader.ensure_change_order_folder`
    (mirrors ensure_estimate_folder/ensure_invoice_folder: Projects/<year>/<Residential|Commercial>/
    <customer>/<project_label>/**Change Orders**/). Derivation (customer=client.name, project_label=
    "<location> | <client>", type=job.project_type|monday_job_type→residential default, year=issue.year)
    matches estimate_flow EXACTLY, so a CO lands in the SAME project folder the estimate created. No more
    pasting Drive links. Tests: tests/test_co_drive_folder.py (3).
  • **CO Slack fully wired into the alerting system.** The notice was already firing (step 3, #change-orders
    via GVC_CHANGE_ORDERS_SLACK_CHANNEL); @gvc_reporting is now a member of #change-orders (C0BAY7ZQ0LD,
    confirmed). Added: records slack_error on a real post failure + a step-5 `notify_finalize_degraded`
    alert so a half-failed CO surfaces in #gvc-ops-alerts (parity with estimate/invoice). /health now also
    reports slack_change_orders_channel.
  ⚠ ENV: set GVC_CHANGE_ORDERS_SLACK_CHANNEL to the #change-orders ID **C0BAY7ZQ0LD** (rename-safe).
  Prod 2026-06-29 (env dump) had it set to the NAME '#change-orders', not an ID — the likely root cause of
  CO notices failing (chat.postMessage is reliable with channel IDs but flaky with #name values →
  channel_not_found → fail-fast). Switch it to the ID C0BAY7ZQ0LD. (SLACK_BOT_TOKEN + the other three
  channels were already correct; estimate channel is the ID C0B562L3PTR.) notify_change_order_drafted's fallback was ALSO changed: an unset env now
  defaults to "#change-orders" by NAME (bot is a member) instead of falling back to the estimates channel
  (#leads) — so a missing env can no longer misroute CO notices to #leads. Estimate notices were not posting
  for a separate operational reason (bot membership + deploy/secret timing), not a code gate — estimate_flow
  step 2 fires notify_estimate_drafted unconditionally on every finalize.

### ESTIMATE → #leads SLACK NOTICE — PORTAL NOW OWNS IT (2026-06-23, DEPLOYED + VERIFIED)
CANONICAL: the "estimate sent → #leads" Slack notice is fired by the PORTAL
(slack_notify.notify_estimate_drafted, on every estimate finalize), NOT by Monday.
Bug that triggered this: an estimate finalized on an ALREADY-"Sent to Client"
opportunity (a 2nd estimate reusing the same Opportunity — the former GM's painting bid on a deal
that already had a drywall estimate) did NOT post to #leads, while new-opportunity
estimates did. ROOT CAUSE: the notice was really posted by Monday automation 1939926355
("Bid Sent Notice", board 1918846027) whose trigger is "when Stage (deal_stage) CHANGES
to 'Sent to Client' (id 7)". The portal's monday_estimate.write_back ALWAYS sets Stage to
"Sent to Client"; for a reused item already at that stage it's a no-op → Monday's
status-CHANGE trigger never fired → no notice. The portal's OWN slack_notify was inert in
prod the whole time (no SLACK_BOT_TOKEN env), so it silently SlackNotConfigured-skipped on
every finalize (that's why NO finalize ever logged a Slack line).
FIX (decided: portal owns the notice; Monday notify retired to avoid dupes):
  • slack_notify.post_message: added bounded retry/backoff (429 honoring Retry-After capped
    at MAX_BACKOFF_SECONDS=4, 5xx, network, ok=false ratelimited-class) + fail-fast on
    config/data errors (channel_not_found/invalid_auth). tests/test_slack_notify.py (9 green).
  • estimate.py finalize step 2: records slack_notified + slack_status/slack_error in the
    writeback; now logs the "not configured" SKIP to stderr too (no more silent drop).
  • service.py: emits structured activity event `estimate.slack` (result ok|skipped|error
    +error) from BOTH /ui/api/estimate/run and /v1/estimate/from-json → a dropped notice is
    now answerable from the activity log, not just raw Cloud Logging.
  • service.py /health: added slack_configured (bool) + slack_estimate_channel.
DEPLOYED 2026-06-23 (`--source .` via hello@) with NEW prod config:
  - Secret `slack-bot-token` → env SLACK_BOT_TOKEN (valueFrom secretKeyRef).
  - env GVC_ESTIMATES_SLACK_CHANNEL=C0B562L3PTR  (#leads channel ID; ID not name = rename-safe).
  VERIFIED via /health (invoice_health): slack_configured=true, slack_estimate_channel=C0B562L3PTR.
REMAINING (admin, Monday UI): on automation 1939926355 remove ONLY the "Notify in channel"
  action (KEEP its "move to Open Deals" + "set Estimate Date") so new opportunities don't
  double-post (portal + Monday). Then smoke-test: finalize an estimate on an EXISTING
  already-"Sent to Client" opportunity → expect a #leads post + activity `estimate.slack
  result=ok` (the case that used to drop). NOTE wording changed: portal posts "📄 Estimate
  {id} drafted in hello@ — ready to review & send" (client/project/total/prepared-by) vs
  Monday's old "Estimate sent to client." (#change-orders has GVC_CHANGE_ORDERS_SLACK_CHANNEL
  set but CO notices ALSO need this same SLACK_BOT_TOKEN — now present — to fire.)

### ACTIVITY TRACKER UI — DEPLOYED + LIVE 2026-06-18 (503 RESOLVED)
✅ RESOLVED 2026-06-18: roles/logging.viewer granted to the portal SA
gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com (confirmed in the project IAM
policy). Root cause of the earlier failed attempts: the grant was run against the wrong
principal ([former-GM acct] the human user, which also needs a "user:" prefix, not "serviceAccount:").
KEY FACT confirmed: gvc-invoice-bot@ is BOTH the Cloud Run runtime SA AND the SA JSON
identity (Secret Manager secret `google-service-account`) — same identity. To re-derive the
SA email without dumping the key: `gcloud secrets versions access latest
--secret=google-service-account --project=gvc-invoice-system --account=hello@... | python3
-c "import sys,json;print(json.load(sys.stdin)['client_email'])"`. /ui/activity now loads
events. (Code was correct all along — it was purely the IAM target.)
Admin-gated in-app audit view (design doc §9 "in-app audit view: later" — now built).
Reads portal activity events BACK from Cloud Logging (the §9 SoT — NO GCS dual-write).
Files: NEW activity_read.py (Cloud Logging query layer: pure helpers build_filter /
range_start / normalize_payload / to_csv / clamp_page_size, all unit-tested; network
isolated in fetch_events() which lazy-imports google-cloud-logging + reuses the portal
SA JSON creds). service.py: +import activity_read, +Response import, +3 admin routes
GET /ui/activity (page), GET /ui/api/activity/events (actor/action/result/range filters
+ page_token pagination), GET /ui/api/activity/export.csv. web/activity.html (filters,
table, Load-more pagination, CSV export, manual Refresh + auto-refresh toggle OFF by
default — 30s). hub.html: +Activity tile (data-feature=admin). requirements.txt:
+google-cloud-logging>=3.8. Dockerfile COPY += activity_read.py. tests/test_activity_read.py
(14 green). `import service` OK (all 3 routes register). NO new env vars (GVC_SERVICE_NAME
defaults to "gvc-invoice"; query uses the existing SA JSON, not the runtime SA).
REMAINING (admin, manual):
  1. IAM GRANT (one-time) — the portal SA (client_email in .google-service-account.json /
     GVC_DRIVE_CREDENTIALS) needs roles/logging.viewer on gvc-invoice-system, else the
     events endpoint returns a clean 503 ACTIVITY_NOT_CONFIGURED ("grant roles/logging.viewer").
     Find SA email: `gcloud iam service-accounts list --project gvc-invoice-system
     --account=hello@greenvalleycontractors.com`. Then:
     `gcloud projects add-iam-policy-binding gvc-invoice-system
       --member="serviceAccount:<SA_EMAIL>" --role="roles/logging.viewer"
       --account=hello@greenvalleycontractors.com`
  2. DEPLOY via hello@: `cd .../gvc_invoice && gcloud run deploy gvc-invoice --source .
     --region us-central1 --project gvc-invoice-system
     --account=hello@greenvalleycontractors.com`.
  ⚠ This --source . deploy ALSO ships the on-disk pending CO code (normalize_base_number
     + CO billing writeback). The CO billing writeback is INERT unless an invoice payload
     carries billed_change_orders (no front-end triggers it — the assemblers aren't built),
     so shipping it is low-risk; it does NOT undo the subitem pivot decision. If strict
     isolation is wanted, stash the CO changes first (not worth it given inertness).
  3. Smoke-test: open /ui/activity as an admin → events load; try actor/action/result
     filters + range; Load more; CSV export; toggle auto-refresh.

### ACTIVITY MONTHLY BACKUP + 60d RETENTION — DEPLOYED + VERIFIED 2026-06-18
✅ LIVE + END-TO-END VERIFIED 2026-06-18. Deployed via `gcloud run deploy --source .`
from the repo root (first attempt 404'd because the former GM deployed from the wrong dir — the
home dir, not ~/Documents/GVC/gvc_invoice; ALWAYS deploy from the repo root). Seed run
`POST /v1/activity/export-month {"month":"2026-06"}` returned ok=246 events, wrote BOTH
"Portal Activity Log 062026.json" (file 1V0NMIhLhYJf02Ejq0gxFiDsYK1vkRF4G) + ".csv"
(1HOZgTFfm9ZN-1qerM1sBDYj4HAhSrAZc) into the Activity Logs folder. Write succeeded →
gvc-invoice-bot@ IS already a member of the Office shared drive (no extra sharing needed).
GVC_ACTIVITY_BACKUP_FOLDER_ID=1p9IoS1HiXuu9k_KVhgwTFg2LQ8BDlm21 env is SET (rev 00033+).
SERVICE_URL = https://gvc-invoice-633452847397.us-central1.run.app.
REMAINING (admin, optional/automation): (a) `gcloud logging buckets update _Default
--location=global --retention-days=60` if not yet done; (b) create the Cloud Scheduler
monthly job `gvc-activity-monthly-backup` (`0 6 1 * *` America/New_York → POST export-month
with X-API-Key; defaults to previous month). Cloud Scheduler API already enabled
(cloudscheduler.serviceAgent present in IAM). If the gvc-service-api-key secret is ever
rotated, update the scheduler job's X-API-Key header. Idempotent: re-running a month shows
action="updated" (same file IDs, no dupes).
--- original build notes ---
Rolling monthly export of all portal activity → Google Drive, plus doubling Cloud
Logging retention 30→60 days. Ships with a plain `--source .` redeploy (NO new
modules/deps/Dockerfile change). Architecture: new X-API-Key endpoint
POST /v1/activity/export-month on the existing service (NOT a separate Cloud Run
Job) + Cloud Scheduler monthly trigger. Writes "Portal Activity Log MMYYYY.json"
(lossless) + ".csv" to the Activity Logs Drive folder (1p9IoS1HiXuu9k_KVhgwTFg2LQ8BDlm21,
in the Office SHARED DRIVE root 0AALXIfL7G3CSUk9PVA — NOT necessarily the same
shared drive as GVC_DRIVE_SHARED_DRIVE_ID). Idempotent per (month,format): re-run
overwrites in place (file ID preserved) → Scheduler retries + backfills are safe.
Cloud Logging stays SoT; Drive files are exports, not a 2nd SoT.
Files: activity_read.py (+build_filter end-bound; +month_bounds/previous_month_key/
month_file_stub/to_json pure helpers; +fetch_all_in_range pager, cap MAX_EXPORT_EVENTS
=100k, flags truncated). drive.py (+DriveUploader.upload_or_replace_file +
_find_child_any_drive, corpora="allDrives" so it writes to ANY shared drive the SA
is a member of). service.py (+ActivityExportRequest +POST /v1/activity/export-month;
reads GVC_ACTIVITY_BACKUP_FOLDER_ID env). tests/test_activity_read.py 22 green (14+8).
py_compile clean on all 3; symbols resolve. Full 143+ suite NOT re-run here (needs
WeasyPrint venv) — run before deploy.
REMAINING (admin, manual — full runbook: docs/portal-activity-backup-design.md):
  1. `gcloud logging buckets update _Default --location=global --retention-days=60`.
  2. Confirm SA has roles/logging.viewer (SAME grant as the /ui/activity 503 fix —
     if that's done, skip).
  3. Ensure the portal SA (gvc-invoice-bot@) is a MEMBER (Content manager) of the
     Office shared drive — Drive UI op, not gcloud. If invoices already file into
     that same shared drive, already done.
  4. Set env GVC_ACTIVITY_BACKUP_FOLDER_ID=1p9IoS1HiXuu9k_KVhgwTFg2LQ8BDlm21, then
     `gcloud run deploy --source .`.
  5. Manual seed June (curl POST month=2026-06) + create Cloud Scheduler job
     `0 6 1 * *` America/New_York (defaults to previous month). On 2026-07-01 it
     auto-exports June (partial, June 18→30) — manual seed just makes it visible now.

### END-OF-DAY STATE (2026-06-16) — read this first

A `gcloud run deploy --source .` (via hello@) SUCCEEDED today (after one fix: co_number.py
was missing from the Dockerfile COPY → added). Because `--source .` ships everything
then on disk, that deploy made LIVE: the **Change Order CREATE program**, the **estimate-
draft autosave**, the **hello@ Gmail fix**, and the **check-image confirm modal** (all
previously "on disk, not deployed"). The former GM ran a real CO end-to-end (subitem + hello@ draft
worked).

LIVE REVISION (checked 2026-06-18) = gvc-invoice-00030-nph (built 06-16 20:53Z), the
latest of four 06-16 revisions (00027 14:41 / 00028 20:04 / 00029 20:07 / 00030 20:53).
The revisions list alone CANNOT tell whether 00030 is the pending --source . code redeploy
or a --update-env-vars revision (both look identical). To disambiguate: `gcloud run
revisions describe gvc-invoice-00030-nph ... --format="yaml(spec.containers[0].image,
spec.containers[0].env)"` — image build time = fresh code build?; env block = were the 2
env vars set? MOOT for now: the pending payload (CO numbering fix + CO billing writeback)
is all subitem-dependent CO behavior, which is PAUSED per the 06-18 pivot above. Next
--source . deploy ships current disk regardless once the CO model is reworked.

ONE REDEPLOY STILL PENDING (same one command ships both) — built + tested AFTER today's
deploy, so NOT yet live:
  1. CO numbering BUGFIX — normalize_base_number() (kills the doubled-prefix
     CO.1-CO.3-… seen in the former GM's test run).
  2. CO BILLING writeback core — invoice.billed_change_orders → CO subitem Status=Billed
     + "CO Billed Invoice" link (link_mm4cb2wm). invoice_type-agnostic.
Redeploy cmd: `cd .../gvc_invoice && gcloud run deploy gvc-invoice --source . --region
us-central1 --project gvc-invoice-system --account=hello@greenvalleycontractors.com`.
Full suite 143 green; `import service` OK.

ENV STILL TO SET (NOT shipped by a code deploy — need --update-env-vars via hello@):
  - GVC_TIMEOFF_FORM_URL (Time Off page shows a graceful notice until set) — value below.
  - GVC_CHANGE_ORDERS_SLACK_CHANNEL=#change-orders (optional; until set, CO Slack notices
    fall back to #estimates / show "not configured"). Also: create #change-orders + invite bot.

NEXT (CO billing): build the two FRONT-END assemblers — CO-tool "Bill this CO" (independent,
prefill from subitem, invoice_type/AIA selectable) + invoice-tool CO picker (list_billable_cos,
bundled). Confirm AIA-standalone UX w/ Jordan first. (Details in item 7 + docs/portal-change-
order-billing-design.md.)

### DONE this session (2026-06-16) — estimate drafts + hello@ Gmail fix + Change Order program

ESTIMATE DRAFT AUTOSAVE — DEPLOYED 2026-06-16 (rode the CO deploy). Resumable in-progress
estimates so a session timeout / dropped connection never loses a half-filled form.
Two layers:
- Browser localStorage = the working copy. Autosaves on every edit (800ms debounce);
  survives timeout, reload, tab close, offline. Key `gvc_estimate_drafts_v1`.
- Shared server copy in GCS (`portal/estimate-drafts.json`), gated by the `estimate`
  grant → the whole estimate team sees each other's drafts and can resume/delete from
  any device. Server is authoritative when reachable (propagates cross-device deletes);
  local wins while offline. Last-writer-wins by client `updated_at`; stale writes ignored.
  Drafts auto-delete on a successful finalize (estimate is then real in Monday = SoT).
Files: NEW estimate_drafts.py (pure upsert/remove/cap/stale helpers + generation-guarded
  GCS IO, reuses portal_store._blob/bucket/creds; caps MAX_DRAFTS=200, 256KB/payload).
  service.py: +EstimateDraftUpsertRequest model, +3 routes GET/PUT/DELETE
  /ui/api/estimate/drafts[/{id}] (require_feature "estimate", logged via activity).
  web/estimate.html: localStorage autosave + "Resume a draft" panel (synced/this-device
  badges, Resume/Delete), "Start new" button, save-state indicator, applyData() restore,
  server-authoritative merge on load. Dockerfile COPY += estimate_drafts.py.
  tests/test_estimate_drafts.py (12 green — pure helpers). NO new deps; no new env (store
  reuses GVC_GCS_PREVIEW_BUCKET + the SA JSON, both already set; works regardless of
  GVC_GRANTS_BACKEND). DEPLOYED 2026-06-16 (rode the CO deploy). Optional smoke-test:
  start estimate → reload mid-fill → resume; check a draft is visible on a 2nd login;
  finalize clears it.

HELLO@ GMAIL DRAFT — FIXED IN CODE (no OAuth needed). KEY FACT (decided 2026-06-16):
hello@ and billing@ are the SAME Google account — billing@ was renamed/aliased to
hello@. So there is ONE authorized Gmail token (the existing gmail-token secret,
already working for invoices). The original code wrongly assumed two separate
accounts and hunted for a second token (.gmail-token-hello.json) that never existed
→ that's why finalize reported "⚠️ SKIPPED ... /app/.gmail-token-hello.json". (PDF +
Drive + Monday still ran; only the draft was skipped.) Re-downloading the desktop
OAuth client JSON is impossible (file lost, not re-downloadable) — and unnecessary.
FIX shipped this session: gmail.py HELLO_TOKEN_PATH now falls back to the shared
billing TOKEN_PATH when no dedicated hello token/env is set (resolution: env
GMAIL_HELLO_TOKEN_PATH → /gmail-hello-token/token.json if present → TOKEN_PATH). In
prod TOKEN_PATH = /app/.gmail-token.json (existing gmail-token secret), so the
estimate draft uses the existing token, From: hello@ (valid send-as on the same
account). NO browser OAuth, NO new client JSON, NO new secret/env. DEPLOYED 2026-06-16
(rode the CO deploy; shipped gmail.py fix + estimate-draft autosave + check-image modal).
Confirmed working: the former GM's real CO run produced a hello@ draft. Runbook: docs/gmail-hello-setup.md.

### Status (end of session 2026-06-15)

PORTAL IS LIVE at https://portal.greenvalleycontractors.com. Estimate flow tested
end-to-end by Andrea (2026-06-15) — all green; Andrea + Jordan happy.

DEPLOY STATUS (read before assuming what's live):
- SUPERSEDED 2026-06-16: a newer deploy shipped today — see the END-OF-DAY STATE
  banner at the top for current live state + the one pending redeploy. The 06-15
  notes below are historical.
- LAST DEPLOYED revision (as of 06-15) = gvc-invoice-00022-8mm (access control v1 +
  GCS grants flipped on; Andrea + Jordan = admins). Estimate + Invoice + Admin live.
- DEPLOYED since 00022-8mm (confirmed via the former GM's screenshot 2026-06-15): Paid-by-
  Check READ-ONLY path is LIVE at /ui/check (Time Off page + estimate "John Smith"
  fixes rode the same deploy). The former GM tested the NorthSide cashier's check — extract
  worked; payer parsed as "AND TRUST COMPANY" (rotated cashier's check OCR is
  messy) but it's editable, so fine.
- DEPLOYED 2026-06-16 (rode the CO deploy): check confirm modal now shows the uploaded
  check IMAGE at the top for reference (web/check.html, client-side object URL).
- ENV TO SET (via hello@, --update-env-vars): GVC_TIMEOFF_FORM_URL=
  https://docs.google.com/forms/d/e/1FAIpQLSe9rCYhfDgdB96pXlS9AMmpFWGR3cIx5RETyue1T9epFMSgtg/viewform?embedded=true
  (Time Off page shows a graceful notice until set). GVC_GRANTS_BACKEND=gcs already set.

STANDING RULE: run ALL gcloud / cloud ops through hello@greenvalleycontractors.com
(company-owned, non-individual account) so resources stay company-side, never tied
to a personal login. Pass `--account=hello@greenvalleycontractors.com` on gcloud.

DONE this session (2026-06-15):
- Hub landing page shipped (queue #2): web/hub.html — green-themed tiles: Live
  (Estimate, Invoice) + Coming-soon (Takeoff, Time Off, Paid by Check), with
  signed-in email + Sign out. `/` route in service.py now serves it (auth-gated
  via require_ui_access; email injected + HTML-escaped) instead of redirecting to
  /ui/estimate. No Dockerfile change — web/ is COPYed as a directory.
- Domain mapping created — THIS was the 404: the mapping never existed, so Google's
  frontend was answering 404. `domain-mappings create` now done; conditions Ready /
  CertificateProvisioned / DomainRoutable all True; CNAME portal→ghs.googlehosted.com
  resolves; root-domain ownership verified (via hello@).
- Estimate form example bid-contact made consistent: "Jordan"/"jordan@" →
  "John Smith"/"john@greenvalleycontractors.com" (web/estimate.html + example_
  estimate.json). Confirmed: prepared_by.email is display-only on the PDF "Your
  Bid Contact" block; never the email sender/reply-to (that's hello@).
- ACCESS CONTROL v1 — DEPLOYED + LIVE (rev gvc-invoice-00022-8mm; flipped to
  GVC_GRANTS_BACKEND=gcs). Grants seeded via /ui/admin: Andrea `*`, Jordan `*`
  (both admins). [former-GM acct] = break-glass superadmin via allowlist. portal/grants.json
  created in the preview bucket. STILL TO GRANT: Jake estimate+takeoff. Details:
  - New modules: access.py (grants resolver: features estimate/invoice/takeoff/
    timeoff/admin, `*`=all, timeoff baseline; backend env|gcs), portal_store.py
    (GCS JSON store portal/grants.json, optimistic ifGenerationMatch concurrency,
    pure helpers + HR-private skeleton object), activity.py (structured JSON audit
    events → Cloud Logging). Added to Dockerfile COPY line.
  - auth.py: verify_session + exchange_code now delegate the membership check to
    access.is_provisioned (env backend == old allowlist, so no behavior change
    until flipped). allowed_emails() kept as superadmin source.
  - service.py: require_feature()/require_admin() guards on /ui/estimate, /ui/
    invoice + their /ui/api run endpoints; / hub injects {{FEATURES_JSON}} and
    filters tiles + adds an Admin tile; new /ui/admin page + /ui/api/admin/users
    (GET list, POST upsert, POST remove). All actions log via activity.log_event.
  - web/admin.html: list users, add-by-email, feature checkboxes (+ All `*`),
    remove. Superadmins (env) shown locked; read-only banner when backend≠gcs.
  - web/hub.html: tiles now carry data-feature + client-side filter by grants.
  - Tests: +tests/test_access.py, tests/test_portal_store.py (16 new). 25 auth/
    access/store tests green locally; full 54+ suite needs the WeasyPrint stack
    (run in venv before deploy).
  - DESIGN doc: docs/portal-access-and-people-architecture.md (SoT-per-concern:
    Google Workspace=identity, GCS store=grants+HR, Monday=PM data only; Slack UI
    deferred; live Directory read flagged to revisit).

DONE earlier (revision gvc-invoice-00019-pvn):
- Estimate system: dry-run→finalize, WeasyPrint PDF, GCS preview, hello@ Gmail
  draft, Slack #estimates, auto-numbering, Drive save, Monday write-back.
- Auth: `auth.py` + /auth/* routes, signed-cookie sessions (1h TTL, silent
  re-auth), hd-claim check + `GVC_PORTAL_ALLOWED_EMAILS` allowlist re-checked
  per request. Currently allowlisted: [former-GM acct] only.
- /health all green in prod: Stripe, Drive, Monday, GCS bucket, Gmail.
- Tests: 54 passing (24 new: tests/test_auth.py, tests/test_estimate_number.py).
- Dockerfile gotcha fixed: COPY lists modules EXPLICITLY — new .py files must be
  added to the COPY line (build smoke-test only catches eager imports).

OAuth/secrets (unchanged, working): client `gvc-portal` in gvc-invoice-system
(Internal); Secret Manager: gvc-oauth-client-id/-secret, gvc-session-secret.
Redirect URI registered = portal.greenvalleycontractors.com/auth/callback (so
signing in via the run.app host throws redirect_uri_mismatch — expected).

STILL PENDING (admin, manual):
- OAuth consent branding: "Potato App" no longer appears at sign-in (2026-06-15) —
  treated as stale/resolved.
- Extra project `gvcportal` (created by mistake) → an admin to delete it.
- Only [former-GM acct] currently allowlisted (GVC_PORTAL_ALLOWED_EMAILS); add others as part
  of the grants work (queue #1 below).

### Locked decisions (estimate system)
- Numbering: `YYYY-MMDD-NNN`, service-assigned at finalize, daily counter,
  revisions keep number + " Rev N". No prefixes. (estimate_number.py)
- No client-facing interface ever: client gets PDF by email; alternates are
  priced lines, accepted by reply. No e-sign, no PandaDoc (retirement target).
- Monday updates AT DRAFT CREATION (not send); office sends same-day/next-AM.
- Stage label: existing "Sent to Client" (id 7). Backfill = fill-if-empty only.
- No Opportunities match → create item in "New Deals" group.
- PDFs: Jinja2/WeasyPrint (templates/estimate.html.j2), NOT Sheets. Terms
  boilerplate lives in estimate.py STANDARD_BOILERPLATE.
- Drive: Projects/<year>/<Res|Comm>/<client>/<Address | Client>/Estimate/.

### Key boards/IDs
- Bid Board 1918846027 (RENAMED from "Opportunities" 2026-07-02 — same board id;
  dated entries below use the old name). Estimate # col: numbers18,
  PDF col: file_mkvk7hyz, stage: deal_stage. Directory board 1919766765.
- Invoices Sent ledger 1931784889. Projects 1918846405. CRM workspace 1102536.
- Board hygiene debt: 2 formulas reference deleted columns; services dropdown
  has 79 labels w/ dupes; PandaDoc columns to retire after cutover.

### Next session queue (in order)
FUTURE BACKLOG (logged 2026-06-17, not yet scheduled): docs/portal-feature-backlog.md
  — estimate edit/revision from Drive (+ARCHIVE- prefix, updates BOTH Monday + Slack);
  non-MCP Claude form fill (Est/Inv/CO); customer search + Monday-board CSV backup;
  invoice + CO draft autosave; GCP Cloud-audit-log admin viewer. Promote items into
  this queue when picked up. Several ★ OPEN architecture Qs to confirm w/ Jordan first.
DONE ~~Confirm portal live + smoke-test~~ (live; hub confirmed by the former GM).
DONE ~~Hub landing page at `/`~~ (web/hub.html shipped; tiles currently STATIC,
  become grant-driven in #1 below).

1. (IN PROGRESS — built, needs deploy + flip) Access control + admin + people.
   Code shipped this session (see status block). REMAINING for an admin:
   a. Run full test suite in venv, then redeploy via hello@ (Dockerfile COPY now
      includes the 3 new modules; estimate-form name fixes ride along).
   b. Service account already has objectAdmin on the preview bucket; the store
      reuses GVC_GCS_PREVIEW_BUCKET unless GVC_PORTAL_STATE_BUCKET is set.
   c. Flip on: set GVC_GRANTS_BACKEND=gcs ([former-GM acct] stays superadmin via
      GVC_PORTAL_ALLOWED_EMAILS → never locked out). Missing store object is fine
      (first admin upsert creates it).
   d. In /ui/admin, grant: Andrea `*`; Jake estimate+takeoff; others as needed.
      Decided 2026-06-15: Andrea = full admin (was estimate+invoice).
   LATER on this track: live Google Workspace Directory read for the roster +
   salesperson autofill (retires "John Smith" placeholder); HR-private fields
   (hr_private.json skeleton exists, no UI yet); group/org-unit-based grants.
2. Time-off page — BUILT (web/timeoff.html + /ui/timeoff, gated by timeoff
   baseline; hub tile flipped Live). Iframes the Google Form via env
   GVC_TIMEOFF_FORM_URL (graceful notice if unset). Form URL RECEIVED 2026-06-15
   (forms/d/e/1FAIpQLSe9rCYhfDgdB96pXlS9AMmpFWGR3cIx5RETyue1T9epFMSgtg). REMAINING:
   set the env var via hello@ + deploy (ships the page + hub tile).
3. "Paid by Check" tool — DESIGNED (docs/portal-check-deposit-design.md). Locked:
   Google Vision OCR; one check = one invoice in full; writes Stripe + Monday +
   Drive (no QBO). 3-stage gate (extract/match → confirm → commit), idempotent
   per step, matched against board 1931784889 (amount + memo-invoice# + payer).
   CORE BUILT + TESTED: check_deposit.py — parse_check_ocr() hardened on a REAL
   check (Danis Builders $110,610, check# from MICR not the 73-27/421 fraction,
   textual date, payer via company-suffix skipping the watermark strip, payee,
   routing/account, payment reference) + match_invoice() (memo/ref invoice# →
   exact amount → payer disambiguation; excludes Paid/Void). vision.py = Vision
   DOCUMENT_TEXT_DETECTION wrapper + CLI harness (`python vision.py check.jpg`).
   tests/test_check_deposit.py (13 green). Vision API ENABLED + parser LOCKED to
   the REAL Vision output of the Danis check (fixed a MICR-split-across-2-lines +
   E-13B-glyph account bug; perfect extraction of all 8 fields). google-cloud-
   vision added to requirements.txt. vision.py falls back to ADC when no SA key
   (local ADC set up via hello@). Editable confirm MODAL = Stage B. ALREADY-
   DEPOSITED guard = post-confirm Stripe status check → returns "already
   deposited" page, no writes (Danis check hits this, its invoice is paid). Drive
   target = invoice's existing folder (link_mm3q8r5x). Feature gate = reuse
   `invoice`. READ-ONLY PATH BUILT (needs deploy): monday.py fetch_invoice_rows
   (open rows from board 1931784889); service.py /ui/check page + /ui/api/check/
   extract (vision→parse→match, no writes) + /ui/api/check/commit STUB (501 until
   write path ships); web/check.html (upload → editable confirm modal + manual
   invoice pick); hub "Paid by Check" tile flipped Live. Dockerfile COPY += vision.py
   check_deposit.py; requirements += google-cloud-vision, python-multipart. AST
   clean; 15 check tests green. TEST SET received (test-check-image/, 6 imgs):
   surfaced MULTI-CHECK photos (2-3 checks/img), remittance stubs (carry invoice#/
   job ref — great for matching), cashier's checks (REMITTER payer), handwritten
   checks, rotated images, same-bank/same-routing pairs. Added: count_checks()
   (counts "PAY TO THE ORDER OF" — reliable across same-bank; stubs don't inflate)
   + _find_remitter() payer fallback. Multi-check = FAIL SAFE for v1: extract
   returns multi_check=N, UI warns "upload one at a time" (no mashing). Per-check
   auto-segmentation = later (needs real multi-check OCR via vision.py harness to
   design). Scenarios catalogued in design doc. REMAINING: deploy + verify in-
   product (Danis). Real multi-check OCR CAPTURED (16 check tests green): Vision
   groups each check's text in doc order (no interleave); each has 1 pay-to + 1
   MICR line → auto-segment feasible, but cut at MIDPOINT between MICR lines (amount
   can trail MICR) + stub attaches to preceding check.
   ✅ COMMIT/WRITE PATH + SEARCHABLE MATCH — BUILT 2026-06-18, NOT YET DEPLOYED.
   Use case confirmed (decided 2026-06-18): "check stub" = the COUNTERFOIL retained when a
   check is written — carries the SAME single-payment fields as the check face, used when
   the physical check went to the bank. So it's just an alternate single-document input;
   the 1:1 "one check/stub = one invoice" model HOLDS (no multi-invoice work). Existing
   parser handles stubs fine (the former GM tested one on the portal).
   SHIPPED: (a) check_deposit.py +deposit_plan(stripe_paid,monday_paid) [already-deposited
   guard w/ EXACT message ALREADY_DEPOSITED_MESSAGE; idempotent partial-retry: runs only
   missing steps] +drive_folder_id(url) [pure, /folders/<id> regex]. (b) monday.py
   +get_invoice_row(item_id) [single row by id, +note col long_text_mm3qpsay] +set_invoice_paid
   (item_id, check_no, date_str) [Status->Paid by label on board 1931784889 + appends
   "Paid by check #N on DATE" to long_text_mm3qpsay as {"text":...}; idempotent—won't dup
   the note]. (c) service.py: /ui/api/check/commit is now MULTIPART (file + monday_item_id
   +payer/amount/check_no/date/reference); re-fetches invoice by id (never trusts client),
   reads Stripe status, deposit_plan guard, Stripe.Invoice.pay(paid_out_of_band=True),
   set_invoice_paid, files the image via DriveUploader.upload_or_replace_file (the helper
   added for the activity backup—reused here) into the invoice's Drive folder
   (drive_folder_id of row.drive_folder_url); order guard->Stripe->Monday->Drive, per-step
   report, 502 COMMIT_PARTIAL on soft failure (safe to retry). Logs check.commit /
   check.already_deposited. (d) web/check.html: flat <select> REPLACED with a SEARCHABLE
   filterable open-invoice list (filter by #/customer/amount; auto-match pre-selects);
   confirm() resends the File as multipart + renders per-step results / already-deposited.
   TESTS: tests/test_check_deposit.py 21 green (16+5: deposit_plan x3, drive_folder_id x2);
   check.html JS node --check clean; py_compile clean on service/monday/check_deposit;
   symbols resolve. Full suite NOT re-run here (needs WeasyPrint venv)—run before deploy.
   NO new env vars / deps / Dockerfile change → ships with `cd ~/Documents/GVC/gvc_invoice
   && gcloud run deploy gvc-invoice --source . ...` (from the REPO ROOT). SMOKE-TEST after
   deploy: read a check/stub → confirm searchable pick → record → verify Stripe shows paid
   (out of band), Monday row=Paid + note, image filed in the invoice's Drive folder; re-run
   the SAME check → expect the already-deposited message (no writes). ⚠ VERIFY the long_text
   note actually lands (the {"text":...} format) on the first real run.
   🐞 BUGFIX 2026-06-18 (root cause of the former GM's "search shows no options"): the open-invoice
   filter used an allowlist {"draft","sent","overdue"} but the Invoices board 1931784889
   status labels are actually "Invoice Sent" (the active/open label), "Paid", "Void". So
   open_only filtered out EVERY row → empty picker AND auto-match never had candidates.
   FIXED to a CLOSED-set: monday.CLOSED_INVOICE_STATUSES={"paid","void"} +
   check_deposit._CLOSED_STATUSES={"paid","void"} (open = anything NOT paid/void, incl.
   blank/future labels). +2 regression tests (23 green). Ships with the same redeploy.
   CANONICAL: board 1931784889 status labels = "Invoice Sent" | "Paid" | "Void".
   ✅ VERIFIED LIVE 2026-06-18: a real Cintas STUB recorded end-to-end against "150 W
   Dorothy Lane | Terraces Senior Apartments" — stripe=marked paid (out of band),
   monday=Paid (+note) [confirms long_text {"text":...} writes], drive=image filed. The
   searchable picker + matching fix + commit path + stub parsing all work in prod.
   FINALIZE SAFETY NET added (service.py commit, the former GM chose "commit-only" scope 2026-06-18):
   if the matched invoice's Stripe status is "draft", commit now finalizes it BEFORE
   pay(paid_out_of_band) (which requires an open invoice) → recording never breaks on a
   stray/legacy draft. Result string shows "finalized draft + marked paid (out of band)".
   py_compile clean; ships with the same redeploy.

### INVOICE FINALIZE FINDING (2026-06-18) — creation DOES finalize; orphan-draft gap logged
Checked invoice.py per the former GM's "do we create drafts or finalize?" Q. ANSWER: the flow
FINALIZES and does NOT send from Stripe. create_stripe_invoice() = create draft → attach
InvoiceItems → stripe.Invoice.finalize_invoice() (status->open); deliberately NO
send_invoice (Gmail sends the GVC PDF; Stripe only gives hosted_invoice_url).
collection_method="send_invoice"; finalize=True default on ALL entry points (portal
/ui/api/invoice/run via FromJSONRequest.finalize=True, /v1/*, batch). So normal invoices
are OPEN and payable via paid_out_of_band.
"Funky" drafts come from: (1) ORPHANS — if finalize_invoice errors after create, a draft
is left, AND the live reuse/idempotency check (invoice.py ~816) only treats status in
("open","paid") as "already exists" → it IGNORES drafts and creates a DUPLICATE next run,
orphaning the draft; (2) CLI --no-finalize; (3) legacy voided v2 invoices.
NOT YET FIXED (the former GM deferred — chose commit-safety-net only): the creation-side gap (B) —
make the reuse check also recognize a "draft" and finalize-and-reuse it instead of
duplicating. Also deferred: a read-only Stripe DRAFT audit to find/clean existing orphans
(can't query Stripe from the cowork sandbox — an admin checks Stripe dashboard: Invoices→Draft).

### CLAUDE PORTAL CAPABILITIES — Phase 2 (estimate API path) BUILT 2026-06-18, NOT DEPLOYED
Track = docs/portal-claude-access-and-automation-design.md (remote MCP fronted by per-user
tokens so Jake/Jordan/Andrea drive estimates+invoices via Claude — Cowork desktop + web
claude.ai — no local .mcpb; grant-checked + audited). The former GM picked phase 2 first (2026-06-18).
SHIPPED: service.py +POST /v1/estimate/from-json (reuses EstimateRunRequest {data, mode:
dry-run|finalize}; X-API-Key gated like /v1/invoice/from-json; wraps process_estimate with
the SAME dry-run→finalize gate + _friendly_error envelope as /ui/api/estimate/run; no Stripe;
finalize leaves a hello@ draft, never auto-sends). py_compile clean; route registers; full
suite needs WeasyPrint venv (run before deploy). Ships with `--source .`.
NEXT phases (design §6): 3 = per-user token layer (portal/claude-tokens.json store, admin
mint/revoke UI, per-request validate→email→grants→audit) — SECURITY-SENSITIVE, review the
doc first; 4 = remote MCP endpoint (/mcp on the FastAPI app) exposing estimate_dry_run/
estimate_finalize over this endpoint, token-gated, connect Jordan as test user; 5 = canonical
bid-sheet tab + GVC estimate skill; 6 = grant Jake `estimate`. OPEN §7 decisions for Jordan:
salespeople list scope, canonical bid tab vs adapter, claude.ai connector auth, token expiry.
Phase-1 quick win — DONE 2026-06-18 (NOT yet deployed): salesperson dropdown + `phone`.
portal_store.PERSON_FIELDS +"phone"; admin.html +Phone input (clear/edit/save person);
service.py +GET /ui/api/estimate/salespeople (require_feature "estimate" → store users whose
effective_features include `estimate`, incl. `*` admins → [{name,email,phone}] sorted).
estimate.html: "Choose salesperson" <select> above the Bid Contact fields, loaded on init,
autofills pb_name/email/phone (still editable). Existing 20 store/access tests green; JS +
py_compile clean. Salespeople list scope DECIDED (the former GM deferred §7): = anyone with effective
`estimate` (covers admins). NOTE: env-only superadmins not in the GCS store won't appear (no
person record) — add them via /ui/admin with a name+phone if they should be pickable.

### ESTIMATE PREFILL FROM MONDAY OPPORTUNITY — BUILT 2026-06-18, NOT DEPLOYED
The former GM's ask: paste a Monday Opportunity item link → autofill the estimate form (Monday→SoT).
Mirrors the CO "paste Monday URL → autofill" pattern. SHIPPED:
- monday_estimate.py: +lookup_opportunity(mc, item_id) + PURE build_prefill(item_id, name, cols)
  + _read_columns_full (resolves text / mirror display_value / board_relation linked names)
  + _project_type_from_label. Maps Opportunities board 1918846027 → estimate-form shape:
  client.name = connect_boards5 (Customer relation) linked name [fallback: item name after "|"];
  client.contact_name = mirror6, client.email = mirror34, client.phone = dup__of_mirror
  (all mirror display_value); job.name = item name; job.location = location5; job.scope_summary
  = details; job.project_type = status (Residential/Commercial only); prepared_by.name =
  deal_owner (Sales Lead, first person). KEY: sets job.monday_item_id so finalize write_back
  hits THAT exact item (no name re-match) — the SoT win. NOT filled (not stored on the deal):
  line items/pricing + estimate dates (left to the estimator / form defaults).
- service.py: +GET /ui/api/estimate/lookup?monday_url= (require_feature "estimate", reuses
  _parse_monday_item_id, returns {prefill}); +import monday_estimate.
- web/estimate.html: "Prefill from Monday" card (URL input + Look up & fill) → applyData(prefill);
  +hidden job_monday_item_id carried through collectData; applyData/setField set it.
CANONICAL Opportunities cols (board 1918846027): deal_stage(Stage), status(Project Type),
details(scope), location5(Job Location), connect_boards5(Customer Name→board 1919766765),
mirror34(Email)/mirror6(Contact Person)/dup__of_mirror(Phone) [mirrors of Customers],
deal_owner(Sales Lead), date5(Est Date)/date1(Expiry), numbers18(Estimate #), file_mkvk7hyz(PDF).
TESTS: tests/test_monday_estimate_lookup.py 4 green (pure mapping); py_compile clean; estimate.html
node --check clean. Full suite needs WeasyPrint venv. Ships with `--source .`.
SMOKE-TEST after deploy: open /ui/estimate → paste an Opportunity URL → Look up & fill → client/
job/scope/type/salesperson populate, line items still blank → add lines → finalize → confirm the
write-back landed on the SAME Opportunity item (job.monday_item_id path, not a new "New Deals" item).
   AUTO-SEGMENTATION of multi-check photos still deferred (v1 still FAIL-SAFE: multi_check
   warning, one at a time). --- original build spec (now implemented) below ---
   >>> NEXT (decided 2026-06-15): BUILD THE COMMIT / WRITE PATH (single check;
       auto-segmentation comes AFTER). Read-only path stays as-is. Replace the 501
       stub at /ui/api/check/commit. Build spec (APIs already mapped):
       • Make commit MULTIPART: re-send the image file + confirmed fields (payer,
         amount, check_no, date, reference, monday_item_id). Image bytes are NOT
         persisted at extract, so the modal must resend the File on confirm.
       • Re-fetch the chosen invoice by id (DON'T trust client for stripe id/folder):
         add monday.py get_invoice_row(item_id) (same column set as fetch_invoice_rows).
       • GUARD/PLAN (make it a PURE, tested helper in check_deposit.py, e.g.
         deposit_plan(stripe_paid, monday_paid)): if stripe_paid AND monday_paid →
         already-deposited (no writes), return EXACT message: "This is a check that
         has already been deposited. The invoice tied to this check is marked as
         paid. Please confirm and return to run a new check." Else do only the
         MISSING steps (handles partial-failure retry safely).
       • Stripe: `import stripe; stripe.api_key = os.environ["STRIPE_API_KEY"]`;
         `stripe.Invoice.retrieve(id)` → status; pay via
         `stripe.Invoice.pay(id, paid_out_of_band=True)` only if not already paid.
       • Monday: add set_invoice_paid(item_id) — change_multiple_column_values on
         board 1931784889 with {"status": {"label": "Paid"}} (write by NAME) + append
         "Paid by check #<no> on <date>" to long_text_mm3qpsay. (writeback() shows the
         mutation pattern but hardcodes PROJECTS_BOARD_ID — pass board id explicitly.)
       • Drive: add DriveUploader.upload_file_to_folder(local_path, folder_id,
         filename, mimetype) — generalize upload_pdf_to_folder (it's PDF-only).
         Folder id via a pure check_deposit.drive_folder_id(url) regex on the row's
         drive_folder_url (link_mm3q8r5x → /folders/<id>); write temp file from the
         uploaded bytes; filename check_<no>__<identifier>.jpg (idempotent). If no
         folder url, record payment + note Drive skipped (Stripe+Monday are the truth).
       • Order: guard → Stripe → Monday → Drive; each idempotent; report per-step.
       • Log check.commit / check.already_deposited via activity.log_event.
       • check.html confirm(): send multipart; render already-deposited message vs
         success per-step. Add commit tests for deposit_plan + drive_folder_id (pure).
4. Programmatic Invoices-board logging in invoice live step (replace skill).
5. Pending from Jordan: logo assets (vector/PNG) for estimate/about branding.
6. TIME OFF → Monday + Calendar + reports — DESIGNED (docs/portal-timeoff-monday-
   design.md). Locked (decided 2026-06-15): portal-NATIVE form replaces the Google Form
   + writes to a NEW Monday "Time Off" board (SoT); calendar in TWO places — a Monday
   Calendar view (to create) AND a portal /ui/calendar section open to all (time off
   + holidays + company events); weekly brief shows who's off + how long. Approval
   in Monday for v1. Build phases: create board → monday.create_timeoff_request
   (GVC_MONDAY_TIMEOFF_BOARD_ID env) → native /ui/timeoff + submit → weekly-brief
   reader (gvc_report_system/daily-exec-brief) → /ui/calendar page. Retires
   GVC_TIMEOFF_FORM_URL.
7. CHANGE ORDER program — ★ BUILT + DEPLOYED 2026-06-16. Live in prod (deploy
   was blocked once by co_number.py missing from the Dockerfile COPY — that file
   was created a prior session but nothing imported it until the CO flow did;
   added to COPY, redeployed green). Full standalone create flow shipped. Files: NEW change_order_flow.py
   (process_change_order: dry-run renders CO PDF + GCS preview; finalize = Drive
   file into job's "Change Orders/" subfolder → hello@ draft → #change-orders
   Slack → Monday CO subitem Status=Drafted; graceful per-step, no Stripe), NEW
   monday_co.py (get_project_context autofill + existing-CO ids, find_project_by_
   folder backup match by GFolder Link, create_co_subitem, graceful write_back),
   NEW web/change-order.html (Monday-URL lookup primary + Drive-folder backup;
   hub tile added, data-feature=estimate), NEW example_change_order.json + tests/
   test_change_order_flow.py (19 green). CHANGED change_order.py + change_order.
   html.j2 (standalone flag: approval-by-reply note + Project/Estimate # instead
   of the invoice "not an invoice" disclaimer; invoice-embedded path unchanged),
   drive.py (folder_id_from_url + list_child_names), slack_notify.py (notify_
   change_order_drafted), service.py (ChangeOrderRunRequest + GET /ui/change-order,
   GET /ui/api/change-order/lookup, POST /ui/api/change-order/run; gated by
   `estimate`), Dockerfile (COPY += change_order_flow.py monday_co.py).
   DECISIONS CONFIRMED (decided 2026-06-16, supersede the 06-15 locks below):
   (a) BASE = the ESTIMATE NUMBER (it's becoming the sole project# for all docs),
       so CO.{n}-{estimate#}. (b) Linking is MONDAY-PRIMARY: paste/look-up the
       Monday Project URL → autofill + that item is the subitem parent; Drive
       folder URL is the BACKUP (filing dest + parent match by GFolder Link, skip
       Monday gracefully if unmatched). (c) Slack = NEW #change-orders (env
       GVC_CHANGE_ORDERS_SLACK_CHANNEL, falls back to #estimates).
   TESTS: full suite 131 green in clean venv; `import service` OK; standalone vs
   embedded template branches verified.
   REMAINING (admin, manual): deploy via hello@ (cmd in docs/portal-change-order-
   design.md "## Deploy"): `gcloud run deploy gvc-invoice --source . --region
   us-central1 --project gvc-invoice-system --account=hello@greenvalleycontractors.com`.
   Then smoke-test (lookup → preview → accept; check Drive subfolder + Monday
   subitem + hello@ draft). OPTIONAL: create #change-orders + add bot, then set
   GVC_CHANGE_ORDERS_SLACK_CHANNEL=#change-orders. Jake already has `estimate`.
   PHASE 2 — CO BILLING: design docs/portal-change-order-billing-design.md.
   DECISIONS (decided 2026-06-16): billing a CO IS invoice generation (no separate
   engine) — the invoice engine already bills a CO as a line via kind:"co"
   (segregated subtotal_co, retainage-excluded, distinct PDF section). So
   "independent" = an invoice whose identifier is the CO id (CO.{n}-{estimate#})
   with kind:"co" line(s); "bundled" = a normal invoice + kind:"co" line(s).
   Build BOTH entry points; CO subitem auto-flips Status→Billed on a LIVE invoice;
   record the billing invoice in a NEW subitems-board column. Independent CO
   invoices CAN be AIA (not always standard) → invoice_type carries through.
   CORE BUILT + TESTED 2026-06-16 (NOT YET DEPLOYED): NEW Monday col "CO Billed
   Invoice" = link_mm4cb2wm on Subitems-of-Projects 1918846408 (created via
   integration). monday_co.py +mark_billed/+mark_billed_batch (graceful, idempotent,
   continues past per-CO failures)/+list_billable_cos/+_set_subitem_columns.
   service._run: on LIVE invoice, reads invoice.billed_change_orders
   ([{monday_subitem_id, co_number}]) → mark_billed_batch(identifier, hosted_url)
   after Stripe success → merges co_billed/co_billing_errors into writeback
   (covers /v1 + /ui/invoice; invoice_type-agnostic). tests/test_co_billing.py
   (7 green); full suite 138 green; import service OK. REMAINING: deploy via
   hello@ (no new modules/env), then build the two FRONT-END assemblers — CO-tool
   "Bill this CO" (prefill from subitem; AIA selectable) + invoice-tool CO picker
   (list_billable_cos) — confirm AIA-standalone UX w/ Jordan first. Also deferred:
   auto-read estimate PDF on the CO-create backup path; appendix field tickets.
   BUGFIX 2026-06-16 (on disk, ships w/ same redeploy): doubled CO prefix —
   a real run produced CO.1-CO.3-2026-0616-B2 because the base/estimate-number
   field held a CO id. change_order_flow.normalize_base_number() now strips any
   CO.{n}- wrapper off the base (used in assign_co_number + _build_co_payload),
   so a CO-id-as-base yields CO.1-2026-0616-B2. +5 tests (143 green). NOTE: that
   test run left a stray subitem "CO.1-CO.3-2026-0616-B2" under Projects pulse
   2548570589 + a hello@ draft — an admin can delete (test data, "B2").
   --- original 06-15 design notes (kept for reference) ---
   CHANGE ORDER program — DESIGNED (docs/portal-change-order-design.md). Locked
   (decided 2026-06-15): reuse estimate PROJECT NUMBER as spine; CO id =
   "CO.{n}-{project_number}" (n increments per job, service-assigned); user LINKS
   existing estimate/job → CO inherits client/job/project#/Drive dir; PDF filed in
   the original estimate's Drive folder. CO HOME (decided 2026-06-15, CORRECTED): NOT a
   new board — COs are SUBITEMS of existing $Project items on the Projects board
   1918846405 (that board already tracks the project; subitem IS the SoT, no dual
   SoT). Subitem cols: Amount, Status (Drafted/Sent/Approved/Billed/Void), Issue
   date, Approved date, Drive link, Gmail draft, Notes — inspect "Subitems of
   Projects" board for existing col IDs + add missing. Create via API
   create_subitem(parent_item_id, name, column_values). Billing INDEPENDENT (own
   invoice id CO.{n}-{proj}, occas. a line item) — NO contract-total auto-mutation.
   Reuse `estimate` grant. BUILD FIRST (before Time Off).
   change_order.py PDF renderer already exists. Build phases: co_number.py (pure) →
   creation core (link→pull→PDF→Drive) → hello@ draft + Slack → Monday CO board
   write + status → /ui/change-order form → billing integration.
   OPEN: Time Off (#6) needs a NEW Monday board created. CO (#7) needs the
   "Subitems of Projects" columns inspected + any missing ones added (the former GM's domain).
   Confirm where project_number + estimate Drive folder id are read from
   (Opportunities/Projects/estimate writeback). CO BUILD PROGRESS: co_number.py
   DONE + tested (7 green; pure CO.{n}-{base}, format-agnostic base, increments per
   job).
   INSPECTED 2026-06-15: Projects subitems board = "Subitems of Projects" 1918846408
   (crew work-tracking board). Reusable: long_text_mm0w7pdx "Scope", board_relation_
   mm0w3tn8 "link to Estimates". LACKS Amount + CO status → NEEDS columns added: CO
   Amount (numbers), CO Status (Drafted/Sent/Approved/Billed/Void), CO Issue Date, CO
   Approved Date, CO PDF Link, CO Gmail Draft. >>> ADDED 2026-06-15 via integration —
   col IDs: CO Amount numeric_mm4cmamb, CO Status color_mm4cva36 (labels Drafted/Sent/
   Approved/Billed/Void, write by name), CO Issue Date date_mm4cs2sf, CO Approved Date
   date_mm4c92pe, CO PDF Link link_mm4c8rys, CO Gmail Draft link_mm4cwhs8. Reuse
   long_text_mm0w7pdx (Scope) + board_relation_mm0w3tn8 (link to Estimates).
   COs id'd by "CO." name prefix. (Projects board has
   link_mkwr6ef9 GFolder Link + board_relation_mm40rg52 Linked Opportunity +
   lookup_mm40txvs Contract Value for future Monday verification.)
   LINKING source DECIDED (decided 2026-06-15): user pastes a GOOGLE DRIVE FOLDER URL
   holding the original estimate PDF; read that PDF to pull project number/client/job;
   file the CO PDF back into the same folder; Monday verify later.
   NEXT CO BUILD (once columns added): creation core (link→read estimate PDF→pull
   data→change_order.py PDF→Drive same folder) → hello@ draft + Slack → create_subitem
   write (Status=Drafted) → /ui/change-order form (reuse `estimate` grant) → billing.

### Docs (in gvc_invoice/docs/ unless noted)
- portal-deploy-plan.md — deploy phases + roadmap (kept current).
- portal-timeoff-monday-design.md — Time Off → Monday + portal calendar + reports.
- portal-change-order-design.md — Change Order program (estimate-linked).
- portal-check-deposit-design.md — Paid by Check tool.
- portal-access-and-people-architecture.md — access control + people SoT.
- portal-feature-backlog.md — future features logged 2026-06-17 (not yet scheduled).
- portal-ui-header-standard.md — REQUIRED header for every portal tool page
  (left "GVC Portal" → / home link, centered app title, right keeps #health/.who).
  Any NEW tool page must use it; hub.html is the only exception (it is home).
- GVC_Estimate_System_Confirmed_Design.md + GVC_Estimate_System_and_Portal_
  Architecture.md — in Drive folder "Estimate and Invoice System"
  (14kVWIKU6HOmn_TeZWj_wYqKOuNv97D7u). The xlsx template there is reference
  only (superseded by WeasyPrint).

### People
- Jordan Faulkner (owner). Andrea (office mgr — billing/estimates, Windows PC),
  Jake (sales), Melvin (estimator, Philippines). Andrea's PC = why the portal +
  future remote MCP matter (kills .mcpb installs).

### 2026-07-26 — Canonical + git + takeoff integration (written from the takeoff project)
- The former GM TERMINATED (Jordan, Jul 26). Pending: audit portal-side grants he
  held (access.py entries, Monday/Stripe/GCP IAM). Takeoff app already blocklists him.
- This Windows copy (`C:\Claude\GVC Invoice portal\portal-current`) is CANONICAL
  per Jordan; Mac-era paths in docs above are historical.
- Repo git-initialized (commit 5769329). Commit every change; local-only, no remote yet.
- Takeoff app integration contract: docs/INTEGRATION-CONTRACT-TAKEOFF.md
  (mirrored in the takeoff repo as docs/PORTAL-INTEGRATION-BRIEF.md — change both together).
  Seam 1: takeoff exports estimate JSON in example_estimate.json's EXACT shape —
  that file is now a cross-repo contract, don't reshape it casually.
  Seam 2: takeoff reads win/loss + invoiced $ from Monday (Monday stays the bus).
- Cross-project rule: sessions in the takeoff Claude Code project may read this
  repo; any write from over there appends a dated note here (like this one).

### 2026-07-26 — r2: GVC rebrand + version count + former-GM purge (from the takeoff project)
- VERSION COUNT now lives in web/hub.html's footer ("Portal rN · date"). RULE:
  bump rN + date on every user-visible portal change, same commit.  r1 = the
  pre-rebrand snapshot (commit 5769329); r2 = this batch.
- REBRAND (Jordan-approved mockup): all ten web/*.html pages moved to the GVC
  brand kit — forest #235339 / deep #1A3E2B, gold #C9A24B/#96763B, warm stone
  neutrals (#1C1917/#78716C/#E7E0CE/#FAFAF8), gold header rule, serif h1 with
  CSS "G" monogram (::before — h1 emojis removed), gold Live badges, #B9954E
  card top-edges, gold .btn.gold on commitment CTAs (Generate Preview /
  Confirm & record / Create drafts). Skin only — no structural/JS changes;
  python compileall clean. Customer-facing PDF templates (templates/*.j2)
  deliberately NOT touched — awaiting brand SVG sources.
- FORMER-GM PURGE (Jordan's directive): every reference to the former GM
  removed from all tracked text files (~195 replacements, 31 files). Do not
  reintroduce the name in code, comments, docs, or UI. His account identifiers
  live ONLY with Jordan and in the takeoff app's auth blocklist.
- ⚠️ ACCESS REVOCATION STILL PENDING (needs Jordan / gcloud auth):
  1. Cloud Run env GVC_PORTAL_ALLOWED_EMAILS currently lists ONLY the former
     GM's account as break-glass superadmin — swap to jordan@ (+ andrea@ if
     wanted) and redeploy/update the service.
  2. Remove the former GM's row from portal/grants.json via /ui/admin.
  3. Audit Monday, Stripe, and GCP IAM for grants he held.
  4. Suspend his Google Workspace account (kills portal OAuth sign-in at the root).
  Delete this block once all four are done.

### 2026-07-26 — Lien-rights tracker designed (from the takeoff project)
- Jordan flagged lien-rights protection (NOF / pre-lien / retainage timing) as a
  near-zero, all-manual gap: "Need to fix this for sure." Design at
  docs/portal-lien-rights-tracker-design.md — phases P0 attorney gate, P1
  deadline watch + Slack reminders (BUILD FIRST), P2 draft docs (hello@ drafts,
  never auto-send), P3 mail-service leg. Rule source: validated tri-state legal
  pack (10/10 statute spot-checks Jul 26; validation report in the takeoff repo).
  Monday stays the bus; notice status writes back as Projects-board columns.

### 2026-07-26 — ✨ LIEN WATCH (P1) BUILT — tracker LIVE-ready, alerts BUILT DARK (from the takeoff project)
P1 of the lien-rights tracker, per the design + the build-dark amendment (LAW: alerts exist but
are OFF until Jordan — and only Jordan — sets GVC_LIEN_ALERTS_ENABLED to exactly "true").
- shared/lien_rules.json — 15 deadline-relevant lien/retainage entries filtered from the validated
  Codex tri-state pack (wage/licensing/insurance/dispute dropped), version "2026-07-26", per-entry
  attorney_reviewed:false + source citations, plus GVC-authored `computed` blocks (kind / anchor /
  per-project-type day counts) that counsel must bless in P0 before this is anyone's only safeguard.
- subsystems/lien_watch/deadlines.py — PURE math: compute_deadlines(state, project_type,
  first_furnishing) → deadline rows w/ severity (ok >14d / warn 4–14 / critical ≤3 / missed <0 /
  unknown). Unknown project_type ⇒ BOTH private variants, marked ambiguous (identical windows
  collapse to variant "either"); public is never guessed. KEY HONESTY RULE: P1 only knows FIRST
  furnishing, so last-furnishing/event-anchored deadlines (lien filings, KY notice, retainage)
  surface with their statutory window but NO fabricated due date (severity "unknown") — computing
  them from the start date would manufacture false "missed" alarms. parse_state() reads OH/IN/KY
  from Job Location then item name (bare "IN" matched case-sensitively so the English word can't
  classify a job); normalize_project_type maps the board's labels (Residential/Commercial map,
  Repair/Standard/Specialty ⇒ unknown).
- adapters/monday/lien.py — READ-ONLY Projects-board (1918846405) fetch, paged 200. Columns
  (inspected live via get_board_info 2026-07-26): `date` "Start Date" EXISTS but is sparsely
  filled ⇒ first-furnishing = Start Date (basis "start_date") else item created_at (basis
  "assumed"), EXCEPT rows still in "New Projects (Not Started)" (new_group25317__1) with no Start
  Date get NO clock (nothing furnished yet). Active = every group except `closed` (Completed and
  Paid) — completed-but-UNPAID groups deliberately stay in; skips top-level "CO." rows + deal_stage
  "Project Lost/canceled". FUN FACT: the board's Project Status labels already include "Send Notice
  of Furnishing" — the team was tracking this by hand.
- orchestrators/lien_flow.py — build_tracker() (jobs sorted most-urgent-first + severity counts +
  per-job notes incl. assumed-basis and state-unknown nudges) and send_lien_alerts() — T-14/7/3/1
  Slack pings via slack_notify.post_message, channel ONLY from GVC_LIEN_SLACK_CHANNEL (COI
  pattern, no named fallback), GATE AT TOP of the function: env not exactly "true" ⇒ logs "lien
  alerts disabled" and returns. NO scheduler, NO route, NO wiring into any loop — deliberately
  unreachable in prod. ⚠ Before anyone enables: there's no sent-marker state, so >1 run/day would
  re-ping the same marks — build dedup (or the Monday writeback phase) first.
- app/service.py — GET /ui/lien (page) + GET /ui/api/lien/status (tracker JSON), both gated by the
  NEW `lien` feature (added to access.FEATURES; admins/superadmins get it via *; grant others in
  /ui/admin). web/lien.html — header standard + r2 brand tokens, counsel-unverified banner
  ("Deadline math is unverified by counsel — attorney review pending (P0). This page informs; it
  does not replace legal advice."), summary chips, per-job cards w/ severity chips, days-remaining,
  statute links, first-furnishing basis chip. hub.html: Lien Watch tile (Live, ⏳,
  "Notice-of-furnishing and lien deadlines per job — tracker only, alerts off.") + footer r2 → r3
  same commit.
- TESTS: tests/test_lien_watch.py — 22 pure/env-gated tests (deadline math per state, severity
  bands, ambiguous variants, month-end clamping for KY's 6-month filing, first-furnishing basis
  rules, Monday row normalization, the dark gate incl. near-true values like "1"/"yes"/"TRUE"
  staying dark). First file of the REBUILT tests/ tree (pre-recovery suite is still lost). No
  pytest on this box — file runs standalone: `python tests/test_lien_watch.py` (all pass).
  python -m compileall clean; stubbed `import app.service` OK (60 routes, was 58); lien.html JS
  node --check clean; pipeline smoke-tested against 7 real sampled board rows (CO row skipped,
  sorting + severities correct).
- NOT DEPLOYED (gcloud auth dead post-Joe). DEPLOY (admin): --source . deploy → grant `lien` in
  /ui/admin → open /ui/lien → expect every active job listed w/ the banner. OPEN: P0 attorney
  review of shared/lien_rules.json's computed blocks; a real "First Furnishing"/stocking-date
  column (Start Date is mostly empty — takeoff's Send-to-Office stock date is the natural feed);
  "public work" isn't representable on the board (no Project Type label) so public rules never
  fire yet; alert dedup state before Jordan ever flips GVC_LIEN_ALERTS_ENABLED; Slack channel for
  lien pings undecided (env GVC_LIEN_SLACK_CHANNEL unset).

### 2026-07-27 — Job Check designed (from the takeoff project; Jordan priority "right away")
- Field crew picks an active Monday project in the portal, checks boxes /
  fills columns, one Save writes back via change_item_column_values. The
  portal's FIRST Monday write surface. Design: docs/portal-job-check-design.md.
  Column allowlist in shared/boards.py (money columns excluded); saves are
  explicit-tap only, audit-logged to the activity store; grant key `jobcheck`.

### 2026-07-27 — ✨ JOB CHECK (v1) BUILT — the portal's first Monday WRITE surface (from the takeoff project)
Per the design above. Column ids/types verified LIVE against Projects board 1918846405
(433 items) via get_board_info 2026-07-27 before shipping the default allowlist.
- shared/boards.py — JOBCHECK_COLUMNS (the editable allowlist, display order = config order)
  + the NON-config hard exclusions: JOBCHECK_HARD_EXCLUDED_TYPES (board_relation/mirror/link/
  file/button/formula/people/tags/progress/timeline/location/…) and JOBCHECK_HARD_EXCLUDED_IDS
  (board_counts = per-board billing basis, numeric_mm3fcjmn Pay App #, numeric_mm5ahj91 CO
  Amount). Money/contract/link columns can NEVER be written even if someone edits the config —
  allowlisted_columns() re-filters on every save. Default allowlist = the drywall trade
  sequence: Framing Status (color_mkza9z7c), Hanging Status (status_19), Scrapping Status
  (dup__of_hung_status1), Taped Status (dup__of_scrapped_status), 2nd Bed Coat
  (dup__of_taped_status), 3rd Coat (dup__of_2nd_bed_coat), Sanded (dup__of_3rd_coat),
  Text/Skim (dup__of_sanded), Finishing Stage (color_mkza855s), Cleaned Out (color8),
  Completion Date (date1), Notes (notes7). deal_stage "Project Status" deliberately NOT
  editable (office-owned workflow driver — it's read-only context on the page); Start Date
  left out too (it's the Lien Watch furnishing clock — don't let a stray tap move it).
- adapters/monday/jobcheck.py — fetch_active_jobs() (paged 200, same filters as lien.py:
  skip `closed` group / "CO." rows / Lost-canceled), get_board_columns() (status labels+hex
  in board display order; parse_status_labels handles both settings_str shapes),
  get_item_values() (one item, allowlist + context columns), and set_item_columns() — THE
  write: change_multiple_column_values on ONE existing item, batch first, per-column retry
  on batch failure so failures are named per column. Never creates/deletes items.
- orchestrators/jobcheck_flow.py — list_active_jobs(), get_job_detail() (context header +
  per-column current value + status label/color sets for the chips), save_job_check(): read
  BEFORE snapshot → validate_values() against the effective allowlist (status labels checked
  against the board's real set; dates YYYY-MM-DD; text capped 4000) → adapter write → RE-READ
  → activity.log_event("jobcheck.save", who/item/columns + "Label: old → new; …" changes
  string, result ok|partial|error) → returns {ok, written, failures, confirmed}. No silent
  partial writes. Empty/None value = clear (status/date/checkbox → null, text/number → "").
- app/service.py — GET /ui/jobcheck (page) + GET /ui/api/jobcheck/jobs + GET/POST
  /ui/api/jobcheck/job/{item_id}, all require_feature("jobcheck") (NEW feature in
  access.FEATURES, hub order after `lien`). POST is the ONLY write path — user-tap only.
- web/jobcheck.html — mobile-first (crew on phones): searchable job list (48px+ rows),
  read-only context card (name/group/Project #/location/builder/supervisor/Project Status/
  Project Type + Monday link), checklist form — status columns are TAP-TO-CYCLE chips in the
  board's label order with the label's real hex color (cycle includes "(not set)" so a chip
  can be cleared), 48px date/text/number inputs, textarea notes, dirty-dot per field, sticky
  gold "Save to Monday" bar with change count, saving state, confirmed-values re-render from
  the post-save re-read, explicit per-column failure list on partial saves. Only CHANGED
  fields are submitted. hub.html: Job Check tile (✅ Live, "Pick an active job, check the
  boxes, one save updates Monday — quality checks made quick.") + footer r3 → r4.
- TESTS: tests/test_jobcheck.py — 17 pure tests (self-running like test_lien_watch.py; both
  suites pass): config sanity, hard-exclusion gate vs config edits, value shaping incl.
  clears + garbage rejection + 4000-char cap, allowlist/label validation, settings_str
  parsing (dict + list shapes + deactivated labels), job normalization, stubbed write-path
  batch→per-column fallback with per-column errors, audit change strings. python -m
  compileall clean; stubbed `import app.service` OK (64 routes, was 60); jobcheck.html JS
  node --check clean; allowlist ids/types cross-checked against the live board snapshot
  (12/12 match).
- NOT DEPLOYED (gcloud auth still dead). DEPLOY (admin): --source . deploy → grant `jobcheck`
  in /ui/admin (crew members get it WITHOUT billing tools — that's the point) → open
  /ui/jobcheck on a phone. OPEN for Jordan/Andrea to confirm: (1) the default column list —
  esp. whether Ceiling Finish / Garage Finish (spec columns) or any supplies-status columns
  belong on the crew pass; (2) whether "Assigned …"/"Paid …" status columns should ever be
  crew-editable (left out — they look office/payroll-adjacent); (3) there is NO checkbox-type
  column on the Projects board today (the form supports the type if one is added); (4) the
  deal_stage labels literally include "Complete Job Check Form" — flipping deal_stage after a
  completed check pass could be a v2 automation, but v1 leaves deal_stage read-only.

### 2026-07-27 — DEPLOYED: r4 live + superadmin swapped (from the takeoff project)
- Cloud Run revisions 00065 (r4 code: rebrand + Lien Watch + Job Check) and
  00066 (env: GVC_PORTAL_ALLOWED_EMAILS = jordan@ ONLY — the former GM's
  break-glass superadmin is revoked at the service level). Deployed via
  Jordan's fresh hello@ gcloud auth, Jul 27. Remaining revocation items:
  grants.json row via /ui/admin, Monday/Stripe/GCP IAM audit, Workspace
  suspension.

### 2026-07-27 — r6: Monday token probe on /health ("present" -> "works")
- The dead-Monday-token incident: health said monday_configured TRUE for weeks
  because the env var existed; the token (a departing employee's) had been
  401ing. Job Check surfaced it only when a crew page finally called Monday.
- Fix mirrors the 2026-07-02 Slack correction: adapters/monday/client.py gains
  cached probe_token() (`me { name }`, TTL GVC_MONDAY_AUTH_PROBE_TTL default
  300s). /health now reports monday_configured = works, plus
  monday_token_present / monday_auth_error / monday_account_user.
- Rotation runbook (all 3 token locations incl. the Apps Script property named
  MONDAY_TOKEN, not MONDAY_API_TOKEN) lives in the takeoff repo:
  docs/MONDAY-TOKEN-ROTATION.md.

### MULTI-WRITER PROTOCOL (Jul 28, 2026) — read before editing
Three parties now touch this repo: Claude Code sessions (takeoff project +
portal project), a design agent connected via GitHub, and Jordan directly.
`master` is the deploy source, so:

1. **Design/UI proposals arrive on a BRANCH, never master.** Branch name
   `design/<topic>`. Someone reviews, then merges. A design agent pushing
   straight to master can silently change what the next deploy ships.
2. **Pull before you edit, push when you're done.** `git status -sb` first —
   "ahead N" means unpushed work someone else can't see; "behind N" means you
   are about to edit a stale file.
3. **NEVER force-push and never rewrite shared history.** If master and local
   diverge, merge — do not resolve it by overwriting.
4. **The deploy is a separate act from the merge.** Merging to master does NOT
   change production; production changes only when someone runs
   `gcloud run deploy`. Check `gcloud run services describe` for the live
   revision before assuming what users see.
5. **Bump the hub footer rN + this file's dated note in the same commit** as
   any user-visible change (existing rule, matters more with several writers).

STYLING NOTE for whoever restyles this app: there is **no stylesheet**. Every
page in `web/` carries its own inline `<style>` with a duplicated `:root`
token block — twelve copies. That is why the Jul 2026 rebrand needed an
eleven-file sweep and why one page kept its blues after the others changed.
Extracting a single shared stylesheet is a wanted change, not a side quest.

### 2026-07-29 — 📘 FIELD MANUAL (`/ui/fieldguide`) — BUILT on branch `design/field-manual`, NOT merged, NOT deployed
Jordan's ask: written procedures the crews can actually use, readable by a beginner but carrying
the expert detail a lead needs. Built as a static portal page — no new deps, no new env, no
Dockerfile change (`COPY web ./web` already takes the directory).
CONTENT — 11 procedures + 3 reference sections, all one page: Metal Stud Framing (layout-first,
batch-by-operation), Stocking Any Material, Stocking Drywall, Hanging, **Scraping**, Finishing,
Acoustical Ceilings (CT/ACT), **Installing Cabinets**, Drywall Patch, **Drywall Touch-Up**,
**Paint Touch-Up**, plus a Component Index (A–Z, tap a term →
jumps to the detail), a Glossary, and Sources & Method. The drywall docs are deliberately named
and ordered to match the **Job Check** stage columns (Hanging Status → Scrapping Status → Taped →
… ) so a crew member moves between the two tools without translating.
TWO REGISTERS — every doc opens with an "In plain words" summary, and 17 `.expert` blocks carry
the deep detail (deflection track: slotted vs double/slip vs clips vs 2D drift; bridging +
anchorage; cold-rolled channel; flat strap / strap-and-block / diagonal bracing; blocking's two
meanings; Z-furring; tall-wall levers + L/240 vs L/360 + stacked walls and stud splicing; board
types; rated assemblies; control joints; GA-214 levels; compound chemistry; corner bead; auto
tools; ACT grid profiles, tile edges, hanger wire, seismic). A **Plain / Full detail** toggle in
the control bar hides or shows every expert block; the choice and all 165 checkbox states persist
in `localStorage` — nothing is written server-side.
⚠ CABINETS IS THE ONE OUT-OF-SCOPE DOC. Casework install is NOT in GVC's trade list (drywall,
metal framing, ACT, insulation, paint, demo, patch) — Jordan asked for it 2026-07-29. It is written
from standard cabinet-install practice, not from our own hard experience, and its provenance block
says so plainly. It deliberately leans on the parts we DO own: the casework **backing** is already
on our framing checklist, and the ledger-hole patch + wall touch-up at the end are our trades. The
real risk it flags: anchoring loaded upper cabinets into STEEL studs with no backing — drywall
anchors are not an answer, and drywall screws (brittle, hardened) are a genuine sudden-failure mode
vs. ductile washer-head cabinet screws. If GVC starts quoting casework, that doc needs a review by
someone who installs cabinets for a living before it's anyone's only reference.
SOURCING — every section ends with a "Where this came from" note, and Sources & Method separates
**Standard** (GA-214/GA-216, AISI S100/S211, manufacturer instructions, the listed assembly) from
**Benchmark** (vendor/forum production claims, flagged as ranges not guarantees) from **GVC
practice**. DELIBERATE OMISSION: no production rates. Published figures disagree by 2×+, and
publishing one would hand estimators a number that looks authoritative and isn't — the note says
so and points at Job Check stage data as the real source once we have history.
ACCESS — NEW `fieldguide` feature in `shared/access.py`, added to **BASELINE** alongside `timeoff`
(Jordan's call): every provisioned user gets it with no admin action. Verified via `_expand`:
`[]` → `{fieldguide, timeoff}`. It holds no customer or financial data and the point is one-tap
reach from a phone.
FILES: NEW `web/fieldguide.html` (~140KB, self-contained, portal header standard + sticky control
bar, mobile-first 48px targets, light+dark themed, print stylesheet). `app/service.py`: GET
/ui/fieldguide (require_feature "fieldguide", logs `tool.open`, same UI_MISSING guard as
/ui/timeoff). `shared/access.py`: FEATURES += fieldguide, BASELINE += fieldguide.
`web/hub.html`: Field Manual tile + footer r7 → **r9**.
⚠ THREE THINGS FOR WHOEVER MERGES:
1. **r9, not r8, on purpose.** The uncommitted Job Start work in the main tree also bumps the
   footer (r7 → r8) and also edits `access.py` FEATURES, `service.py`, and `hub.html`. This branch
   was cut from committed `master` via a git worktree so that in-flight work was never touched —
   which means all three files will conflict on merge. The conflicts are small and additive (a
   tuple entry, a route, a tile, the version span). If Job Start does NOT land first, change r9
   to r8.
2. **This is the 13th inline `:root` block — and `web/gvc.css` landed mid-build.** Master gained
   `4be3520 "Add web/gvc.css — the approved GVC portal design system"` while this page was being
   written; that commit is merged into this branch (`da1feaf`) so the branch is current, but the
   PAGE has not been migrated onto it. Two honest reasons, both worth a decision rather than a
   silent choice:
   (a) **gvc.css is light-only** — zero `prefers-color-scheme` / `data-theme` hooks. This page is
       themed for both. Migrating means either dropping dark mode (fine, it matches the rest of
       the portal) or layering dark overrides on top of gvc.css (better, and it's the change the
       whole portal will eventually want — a crew member reading this on a phone at 6am in an
       unlit building is the actual use case).
   (b) **gvc.css was still moving** — 38 further uncommitted lines in the main tree at the time
       of writing. Migrating onto a file mid-edit invites a pointless conflict.
   The mapping is mostly mechanical: `.tile`→`.gvc-tile`, `.note`→`.gvc-banner--*`,
   `.table-wrap`→`.gvc-tablewrap`, `label.step`→`.gvc-check`, `.seg`/buttons→`.gvc-btn--*`,
   plus the `--gvc-*` tokens. Do it as its own commit so the diff is reviewable.
3. **Not deployed.** Merge, then `gcloud run deploy` separately. Smoke: sign in as a non-admin
   with no grants → Field Manual tile shows on the hub → opens → Plain/Full toggle flips the
   expert blocks → tap a Component Index term → lands on that section in Full detail.
NEXT (not built): deep-link each Job Check stage chip to its procedure; insulation, paint and demo
procedures; photos/diagrams for deflection track types, grid profiles, tile edges, butterfly patch.

### 2026-07-29 — Field Manual r2: touch-up SPLIT into drywall vs. paint (Jordan's call)
One "Touch-Up" doc became two, because they are different trades with different economics and the
split is the point:
  • **Drywall Touch-Up** (`#touchup-drywall`) — fixing the WALL. Owns the raking-light walk (bright
    light held near-parallel to the surface; mark OUTSIDE the defect with low-tack tape or pencil,
    never marker — marker bleeds through primer) and the walk-and-mark/sort-into-three-buckets step
    that used to sit in the paint doc. Expert block `#tud-defects` is a cause→fix table for nine
    defects, and its real value is the two entries that are NOT touch-ups: **a crack** (movement —
    filling it brings it back; find the control joint / framing cause) and **a wall covered in
    defects** (the finish never hit the specified level — that's a Finishing skim, and chasing a
    hundred spots costs more than doing the wall). Ends by REQUIRING primer on every repair.
  • **Paint Touch-Up** (`#touchup-paint`, was `#touchup`) — unchanged physics content (flashing =
    sheen + porosity, not colour; same product/batch/tool; recoat the plane above flat sheen). Its
    punch-list section was rewritten to be paint-only and now cross-references the drywall pass
    rather than duplicating it.
  • The economic argument that justifies the split, stated in both: a defect found BEFORE the
    painter costs a knife, some mud and a dab of primer; the SAME defect found after paint costs
    that repair plus prime plus repainting the whole plane corner-to-corner.
⚠ ID RENAME: `#touchup` → `#touchup-paint`. Anything linking to the old anchor needs updating; the
in-page Component Index was updated (26 jumps, all verified to resolve).

### 2026-07-29 — Field Manual r3: +Reading Drawings, +Firestopping, +Spotting a Change Order
Jordan picked the next batch and added one of his own (blueprints). Now **14 procedures**.
  • **Reading Drawings** (`#drawings`) — Jordan's addition, and it's placed FIRST in a new "Start
    here" group because it unlocks every other doc for a new hire. Discipline/sheet-type/sequence
    numbering, title block, scale, grid bubbles, dimension strings, detail callouts, revision
    clouds, and a walkthrough of chasing a wall tag through the partition schedule → life safety →
    finish schedule → door schedule. Two expert blocks: `#dwg-precedence` (document hierarchy, and
    the rule that **a conflict between contract documents is an RFI, not a field decision** — plus
    how to write an RFI that gets answered in a day instead of a week) and `#dwg-res-comm` (the
    residential↔commercial comparison Jordan asked for; the real trap is the DIMENSIONING
    CONVENTION — face of stud vs. face of finish vs. centerline — and the note that our change-order
    documentation matters MORE on residential because changes arrive verbally there).
    ⚠ Deliberately did NOT answer the spec-vs-drawings precedence question: it's decided by each
    project's own precedence clause, so the doc tells you to go read that clause.
  • **Firestopping** (`#firestop`) — placed with Framing since the head-of-wall joint is a wall we
    built. Splits joint systems (ours) from through-penetrations (usually whoever made the hole).
    Expert block `#fs-numbers` decodes the UL number, which is the actual teachable skill:
    `HW-D-1000` = head-of-wall / **D**ynamic / joint width band >2"–6" (bands: 0000-0999 ≤2",
    1000-1999 >2–6", 2000-2999 >6–12"). Plus F rating (stops flame) vs T rating (unexposed side
    under 325°F above ambient), and when specs demand T=F. THE point of the doc: our head-of-wall
    moves, so it needs a **dynamic** system — a static system in a moving joint passes inspection
    day and tears the first time the structure loads. `#fs-install` covers annular space min/max,
    packing depth, fill depth, and intumescent vs elastomeric.
    ⚠ Includes a STOP note: "who firestops the penetrations" is a classic scope gap that **lands on
    the drywall contractor by default** when nobody claims it. Get it in writing at job start.
  • **Spotting a Change Order** (`#changeorder`) — the highest-dollar page in the manual and the
    only one that makes money rather than saving it. The three-question test, the five ways extra
    work arrives (verbal add / un-issued revision / another trade's mistake / forced remobilization
    / conditions not matching the drawings), the two nobody ever bills (**standing-by time** and
    stacked-trade inefficiency), the four-step response (stop, photograph, write it down, call —
    then WAIT for written authorization), who can actually authorize (never another sub), and T&M
    ticket discipline. Deliberately includes a **"when it is NOT a change order"** section — our own
    rework, our own damage, work we missed at bid — because claiming bad ones spends the credibility
    needed for the real ones. Provenance flags that subcontracts often carry a **written-notice
    deadline** in days, which is the real reason to report same-day.
Component Index now 34 entries (added annular space, dynamic vs static, F/T rating, grid lines,
partition schedule, RFI, standing-by time, T&M ticket). All 34 jumps verified to resolve; no
duplicate ids; 249 checkboxes; 26 expert blocks; 14 provenance blocks.
STILL QUEUED (Jordan picked these too, not yet written): Insulation, Painting, Demolition, and the
three estimate-catalog stubs FRP / Doors & Hardware / Tectum — writing those three doubles as the
scope text those catalog entries are still waiting for.
ALSO AGREED, NOT BUILT — **live resumable checklists** (Jordan, 2026-07-29): start a checklist for a
Monday job, save, resume later. Decisions locked: **no Monday writeback for v1** (Job Check keeps
sole ownership of the stage columns — two features writing the same column is how a stale run
regresses a status the office set) and **runs are SHARED across the crew** (estimate-drafts model, so
Mark can start a hang checklist and Robert can finish it). Build it on the existing patterns, do not
invent: `subsystems/estimate/drafts.py` for the localStorage-working-copy + shared-GCS-object shape
with last-writer-wins on `updated_at`, and `adapters/monday/jobcheck.py fetch_active_jobs()` for the
job picker. **Offline-first is mandatory, not a nice-to-have** — jobsite signal is bad and a
checklist that loses a half-finished pass in a stairwell is worse than paper.

### 2026-07-29 — Field Manual r4: the last six. **20 procedures, scope complete.**
Jordan: "proceed with all these." Added Demolition, Insulation, Painting, FRP, Doors & Hardware,
Tectum. Every trade GVC lists now has a procedure, plus the three estimate-catalog stubs.
  • **Demolition** (`#demo`) — the only doc here whose FIRST step is a legal question, and it is
    deliberately conservative. Hard-stop callout up top: **pre-1981** construction (OSHA presumes
    thermal system insulation + surfacing material are asbestos-containing — PACM — and absent a
    survey you must presume suspect material IS ACM); **pre-1978** housing/child-occupied (EPA lead
    RRP, certified renovator); anything structural; any untraceable conduit. Expert block
    `#demo-regs` separates **OSHA 1926.1101 (protects the worker)** from **EPA NESHAP (protects the
    air)** — they are not alternatives — and notes state/local rules are frequently stricter with
    their own pre-demo notification windows. Also carries **silica / 1926.1153 Table 1** (controls
    for handheld grinders = continuous water OR vacuum collection; implementing Table 1 fully =
    compliance), flagged as applying to our EVERYDAY drywall sanding and cutting, not just demo.
    THE FRAMING OF THE WHOLE PAGE: the field's job is not to decide whether something is asbestos —
    it's to recognise that a decision is required and STOP.
    ⚠ Its provenance block states plainly this is orientation, NOT compliance advice, and calls out
    that the written programs behind it (hazcom, respiratory protection w/ fit testing, silica
    exposure control plan) **need to exist before GVC takes on more demo, not after.** Open item.
  • **Insulation** (`#insulation`) — one physical principle (traps still air ⇒ gaps and compression
    are the only two failure modes, both invisible an hour later). Split-the-batt-around-pipe rather
    than compress; full height INCLUDING above the ceiling on to-deck walls. Expert `#ins-sound`:
    STC belongs to the whole assembly not the batt — seal the perimeter and penetrations, offset
    back-to-back boxes, and **if the drawings show STC but the wall stops at a suspended ceiling with
    an open plenum, ask the question** because no amount of batt fixes flanking over the top. Also
    separates **fire safing** (mineral wool, a rated component in a listed assembly) from insulation,
    since they look identical in the wall.
  • **Painting** (`#painting`) — "paint reveals, it doesn't hide." Protect → prep → prime → coats.
    Primer on new board is non-optional (same porosity mechanism as touch-up flashing, at whole-wall
    scale). Expert `#paint-sheen` is a forgiveness table (flat→gloss) whose payoff is the Level-4-
    under-semi-gloss trap already flagged in Finishing, plus spray/backroll/roll trade-offs and the
    warning that a sprayed wall beside a rolled wall reads as two different colours.
  • **FRP** (`#frp`) — two permanent failures: substrate not flat (furring + substrate over CMU,
    never FRP straight to masonry) and no expansion allowance. Expert `#frp-install`: 100% cross-hatch
    adhesive coverage, laminate-roller the air out, **1/4" top+bottom and 1/8" between panels on a
    4×8**, 1/8" between panel edge and molding stem, silicone in the molding channel and to
    floor/ceiling in washdown areas.
  • **Doors & Hardware** (`#doors`) — the frame decides everything; ties back to our framing pass
    (jamb studs, RO, anchor locations). Expert `#doors-rated`: a labeled opening is ONE assembly
    (door+frame+hardware+gasketing), labels stay legible, and **no field modification of a labeled
    door or frame** — a wrong prep is a supplier problem, not a field fix. Accessibility items are
    listed but **deliberately WITHOUT numbers**, because they depend on the standard the project is
    built to and the approach geometry per door.
  • **Tectum** (`#tectum`) — aspen fiber + cementitious binder. Expert `#tectum-attach`: screw-head
    pull-through is adequate alone (no washers, no adhesive), min 24" o.c. from panel edge, 12" o.c.
    on furring, fasteners must account for total system weight, standard 1" thick in 2×4/2×8/4×8.
    **Do not countersink** — heads flush; countersinking crushes the fiber and no filler hides it,
    because the texture IS the finish. Heavy field paint clogs the texture and kills the acoustic
    performance the panel exists for.
⚠ THE THREE STUB TRADES CARRY AN EXPLICIT SCOPE QUESTION FOR JORDAN, in their provenance blocks:
FRP, Doors & Hardware and Tectum are all **title-only** in the estimate scope catalog
(portal/estimate/scope-catalog.json), meaning GVC's actual inclusions were never written down. Each
asks the same three: furnish-or-install-only, who preps the substrate, and who owns the
sealant/keying/field-painting tail. Those answers change the price materially — **do not let these
docs be used for pricing until they're answered.** Answering them also fills the catalog scope text
that's been outstanding since 2026-07-14.
TOTALS: 24 sections (20 procedures + home/index/glossary/sources), 329 checkboxes, 32 expert blocks,
20 provenance blocks, 41 Component Index jumps. Verified: JS node --check clean, CSS braces balanced,
every tile target resolves, every index jump resolves to a real anchor, no duplicate ids, all tag
pairs balanced, py compileall clean.

### 2026-08-04 — Seam 1: Takeoff → portal estimate draft
- Added a draft-only Takeoff import path: pure normalization/validation in
  `subsystems/estimate/takeoff_import.py`, one-store-write orchestration in
  `orchestrators/takeoff_import_flow.py`, and session/API-key routes at
  `/ui/api/estimate/from-takeoff` and `/v1/estimate/from-takeoff`.
- Import accepts raw canonical estimate JSON or `{data: ...}`. Every supplied
  identifier (including legacy `EST-*`) is cleared so finalize assigns a fresh
  `YYYY-MMDD-NNN`; imports never
  finalize, create Gmail/Slack/Monday side effects, or send anything.
- Estimate Generator now uploads/pastes Takeoff JSON, resumes the returned
  shared draft, and supports `/ui/estimate?takeoff=1`. Hub footer r21 → r22.
- Phase 2 Firebase `gvc_portal_outbox/{draftId}` pickup remains deferred.
