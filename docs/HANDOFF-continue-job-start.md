# Continuing Job Start in a fresh Claude Code session

Written 2026-07-29 to hand this work to another Claude Code session (Jordan's team
plan). Everything durable is already committed — this file exists so the new
session doesn't re-derive any of it.

---

## 1. Paste this as your first message

> I'm continuing the **Job Start** feature (Sales → Operations handoff) in the GVC
> portal. Working dir is `C:\Claude\GVC Invoice portal\portal-current`.
>
> Before doing anything, read these two files — they are the full design record and
> I don't want the design re-derived or re-litigated:
> 1. `CLAUDE.md` — read the **top entry, `🤝 JOB START`**, including the
>    `SESSION PART 2` block. That's the current state, every locked decision, the
>    bugs already found and fixed, and the remaining admin steps.
> 2. `docs/portal-job-start-design.md` — the design doc (two-party gate, ingest
>    precedence, adopt-or-create idempotency).
>
> Status: **deployed and live** as Cloud Run revision `gvc-invoice-00075-tqg`,
> serving 100% of traffic. Health is green (Monday, Slack, Drive, Gmail all ✔).
>
> Three things are outstanding, in this order:
> 1. Grant the `jobstart` feature in `/ui/admin` to Jordan, Jake, Mark, Robert —
>    nobody holds it yet, so the tile is invisible to everyone.
> 2. Field-test on the **Bryant / Jent** bid specifically. It already has a Projects
>    item, so it exercises the duplicate safeguard. Confirm (a) the green
>    "Prefilled from Jent-Bryant Res - Scope Review.pdf" banner appears, and (b)
>    accepting **updates** the existing project rather than creating a second one.
> 3. The packet PDF has **never rendered as a binary** — WeasyPrint isn't installed
>    on this PC. First live send is that test.
>
> Read the two files, then tell me what you'd do first. Don't start editing yet.

---

## 2. Things that will waste your time if you don't know them

**`gcloud` is not on the Git Bash PATH.** The Bash tool returns "command not found".
Use the PowerShell tool with the full path, and pass `--source` explicitly rather
than relying on cwd:

```
C:\Users\jorda\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd
```

Signed in as `hello@greenvalleycontractors.com` (active account).

**WeasyPrint is not installed locally.** So `import weasyprint` fails and the packet
PDF cannot be render-tested on this machine. The repo's standing rule says "run the
full suite in the WeasyPrint venv before deploy" — that venv is **gone** (lost with
Joe's Mac, along with the 441-test suite). `tests/` currently holds only 3 files.

The working verification pattern on this machine is:
```bash
python -m compileall -q shared adapters orchestrators subsystems app
python -c "import sys,types; m=types.ModuleType('weasyprint'); m.HTML=object; sys.modules['weasyprint']=m; import app.service as s; print(len(s.app.routes))"
node --check <extracted <script> block>
```
Expect **73 routes**, 8 of them `jobstart`.

**Another Claude Code session is working in this same repo** on a Field Manual
feature (`/ui/fieldguide`, `subsystems/fieldguide/`, `web/fieldguide.html`). At time
of writing it has uncommitted changes in `app/service.py` and `web/fieldguide.html`.

- **Never `git add -A`.** Stage explicit paths only.
- A merge from `design/field-manual` already happened (`ff58b80`) and left conflict
  markers in `shared/access.py` and `web/hub.html` at one point. Both are resolved and
  both features survived — `FEATURES` contains **both** `jobstart` and `fieldguide`.
  If you see markers again, keep both.

---

## 3. What's already decided — do not re-open

| Decision | Value |
|---|---|
| Where it lives | The portal, not Drive discipline, not paper |
| The gate | **Two-party.** Field completeness gates the *send*; **ops acceptance gates the job**. Monday items are created ONLY in `accept()`. |
| Self-acceptance | Refused — the sender can't accept their own packet (admins excepted, and logged) |
| "Silence is acceptance" | **Deleted.** It contradicted "no signature, no crew". |
| Job naming | **Pipe:** `[Street Number Name] | [Builder]`. Not dashes. |
| Output | Generated PDF filed to Drive. **No handwriting anywhere** — this is a hard requirement from Jordan. |
| History | Not backfilled. "Leave history alone." |

**Deferred on purpose** (Jordan: *"Dont spend those tokens to fix the pipe. DO it one
day we have extra before a reset"*): the three Monday boards' own `item_terminology`
strings still describe the old dash/underscore formats. Cosmetic — nothing reads them.
Low priority. **Do not re-open the pipe decision itself.**

---

## 4. The shape of the code

```
shared/boards.py            JOBSTART_FIELDS  ← the handoff contract, as editable config.
                            One spec drives the form, the gate AND the Monday writes.
                            24 fields, 8 required. Hard-excluded ids/types can't be
                            opened by a config edit (regression-tested).
subsystems/jobstart/
  scope_review.py           PURE parser for Jake's scope review (the primary source)
  ingest.py                 PURE merge: typed > scope review > board updates > bid
  naming.py                 pipe standard + the cross-convention duplicate safeguard
  drafts.py                 per-bid packet store + the 4-state machine
  packet.py                 PDF context builder + render
  gc_confirm.py             GC scope email + Jake's client-facing scope rules
adapters/monday/jobstart.py Bid reads, item updates, adopt-or-create writes
adapters/drive.py           find_job_documents() / ensure_handoff_folder()
orchestrators/jobstart_flow.py   send_to_ops / send_back / accept
app/service.py              8 routes under /ui/jobstart
web/jobstart.html           mobile-first, autosave, sales-fill + ops-review modes
templates/job_handoff.html.j2     the packet PDF
docs/JOB-START-MANUAL.html  the crew-facing manual
```

**Key IDs.** Bid Board `1918846027` · Projects `1918846405` · Operations `1920364853` ·
Customers `1919766765`. Jake's plans tree: Drive folder
`1X1vuutnTuCN0hxTZSANmm3QC6SQ41Gc0` = "01 - Completed Plans", per-job subfolders named
`{seq} - {GC} - {project} - {status}`, each holding a `… - Scope Review.pdf` plus a
`… - Takeoff Totals.xlsx`. Env override `GVC_JAKE_PLANS_FOLDER_ID`.

---

## 5. Open items, roughly by value

1. **The GC scope-confirmation email is the highest-ROI thing left.** It exists and
   drafts to hello@, but `gc_confirmed_on` records when it was **drafted**, not sent —
   nobody can tell the portal when you hit send. Every correction a GC returns is a
   change order not eaten, so making that signal truthful matters. The sent-watcher
   (`orchestrators/sent_watch_flow.py`) already solves exactly this problem for
   invoices and estimates by searching Gmail `in:sent` — extend that pattern.
2. **Turn OFF the create node in Monday workflow `1939926355`,** or retire it. Until
   then every Accepted bid still gets an automation-created Projects item (harmless —
   adopt-or-create absorbs it) but the misleading *"and Operations Dashboard"* Slack
   line keeps posting for an item it never made.
3. **Confirm which Bid Board column links Operations.** Two exist —
   `connect_boards1` ("Team Tasks") and `board_relation_mm44jdnw` ("Operations") — both
   empty on 100% of accepted bids, so live data can't break the tie. Currently writes
   `connect_boards1`; env `GVC_MONDAY_BID_OPS_LINK_COL` flips it with no deploy.
4. **Rebuild `tests/`.** 441 tests were lost with Joe's Mac. Everything since has been
   verified by compile + stubbed import + hand-rolled assertion scripts.
5. Deferred cosmetics: the board `item_terminology` strings (see §3).

---

## 6. The historical defect this feature exists to fix

Worth knowing, because it's the reason for several design choices. A Bid Board
automation (`1939926355`) already fired on **Stage → Accepted**, created a Projects
item, and posted to Slack claiming *"a new item was created in the Projects Dashboard
**and Operations Dashboard**"*. There was no second create node — **it never touched
the Operations board**, and had been announcing otherwise on every won job since May.

Verified across the first 30 accepted bids: both Operations link columns empty on
100%; `date6` Accepted Date **null on every won deal**; ~8 accepted bids with no
Projects item at all; one bid linked to two projects (one an unrelated job); two bids
marked Accepted while sitting in the Lost Deals group.

That's why every write is adopt-or-create, why the Slack notices only claim what
actually happened, and why the packet is config rather than code.
