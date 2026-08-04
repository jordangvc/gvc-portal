# Job Start — the next ten steps

Ordered by dependency first, value second. Written 2026-07-29, after the feature
went live as revision `gvc-invoice-00075-tqg`.

Effort figures are rough and assume a session that has read
`docs/HANDOFF-continue-job-start.md` and isn't re-deriving the design.

---

### 1. Run the first smoke test — the four answers
**Blocks everything below. ~5 min, no code.**

Bryant / Jent bid `2776470967` (§5 of the handoff doc). Report:
banner green or grey · "Project created" or "updated" · did the PDF land in Drive ·
did the Operations task appear.

This single test settles three separate unknowns at once: whether the service account
can read Jake's Drive, whether WeasyPrint renders the packet (never once proven), and
whether the Operations item — the thing that has never been created in the history of
this process — actually gets created.

### 2. Share Jake's plans folder with the service account
**Conditional on a grey banner in step 1. ~2 min, no code.**

Folder `1X1vuutnTuCN0hxTZSANmm3QC6SQ41Gc0` → share with
`gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com` as Viewer.

If this is needed and skipped, the entire ingest premise collapses and Job Start
degrades to manual entry — which Jordan has explicitly rejected. That's why it sits
this high despite being a two-minute click.

### 3. Open bids + accept the bid in place
**Jordan's top feature request. ~half a session.**

Spec in §6 of the handoff doc. Job Start currently can't see a job until someone has
gone to Monday and flipped the stage, which is backwards for the tool Sales is meant
to live in.

⚠ Before writing `deal_stage`, check which automations key off it. The estimate flow
deliberately does NOT auto-advance stage for exactly this reason. Decide with Jordan
whether marking-accepted is an explicit tap or implicit on send.

### 4. Make the GC confirmation date truthful
**✅ SHIPPED on master.** `email_scope_to_gc` no longer stamps `gc_confirmed_on` at
draft time — only `gc_drafted_at`. The sent-watcher (`orchestrators/sent_watch_flow.py`)
sweeps Job Start GC confirmations the same way it does invoices/estimates: searches
Gmail `in:sent` for the drafted subject, then `stamp_gc_confirmed()` writes the real
send date (fill-if-empty). Cloud Scheduler `gvc-sent-watch` already runs every 10 min.

Related follow-up in the same spirit (truthful Bid Board dates): stamp Bid Board
`date6` Accepted Date when Sales marks a bid Accepted from Job Start — the legacy
automation never wrote it, and ops-accept day is the wrong moment.

### 5. Settle which Bid Board column links Operations
**~2 min decision, then one env var.**

`connect_boards1` ("Team Tasks") vs `board_relation_mm44jdnw` ("Operations"). Both are
empty on 100% of accepted bids, so live data cannot break the tie — this needs a human
who knows the board's history. Currently writes `connect_boards1`; env
`GVC_MONDAY_BID_OPS_LINK_COL` flips it with no deploy.

Cheap, but it's a correctness question sitting under every handoff, so don't let it
drift.

### 6. First-pass acceptance report — the metric is already free
**~2 hours. Higher value than it looks.**

Jordan's own handoff standard named four numbers to track and then chose no
instrumentation, because in a Drive-based process nothing could be measured. That
changed: the state machine now logs `jobstart.sent_to_ops`, `jobstart.sent_back` and
`jobstart.accepted` to the activity store with actor and timestamp.

So **first-pass acceptance rate** and **packet completion time** are already sitting in
the data — they just need reading. `shared/activity_read.py` + the existing
`/ui/activity` view are the place to hang it.

The send-back notes are the real prize: that list, over time, *is* the defect list for
the sales process. Jordan's own doc says so.

### 7. Rebuild `tests/` for the Job Start surface
**~a session. Increasingly urgent, not optional.**

441 tests were lost with Joe's Mac. Everything built since has been verified by
compile + stubbed import + hand-rolled assertion scripts that live nowhere. Job Start
added ~12 modules and the assertions written during the build were thrown away.

Priority order for coverage — the pure, high-consequence logic first:
`naming` (cross-convention matching, the duplicate safeguard) · `scope_review` (the two
real-doc parsing bugs already found) · `ingest` (precedence: typed must never be
overwritten) · `gc_confirm` (the SF / 1-side scrubber) · `jobstart_flow` (the gate).

Also settle the WeasyPrint venv while here, or the PDF stays untestable forever.

### 8. Roll out to Jake, Mark and Robert
**~1 hour, mostly conversation.**

Grant `jobstart`, walk them through `docs/JOB-START-MANUAL.html` once, then run the
next three real won jobs through it together. Expect friction — that's the point,
it surfaces the gaps that were already there.

Watch specifically for: does Ops actually accept, or does it sit? A packet nobody
accepts is the failure mode this design is most exposed to, since "silence is
acceptance" was deliberately removed.

### 9. Retire the legacy automations
**~15 min. Do NOT do this before steps 1 and 8.**

Turn off the create node in Bid Board workflow `1939926355` (or retire it). Until then
every Accepted bid still gets an automation-created Projects item — harmless, because
adopt-or-create absorbs it — but the misleading *"and Operations Dashboard"* Slack line
keeps posting for an item it never made.

Also retire the automations still pushing people at the old PandaDoc / wkf.ms Job Start
forms, so there's one path and not three.

Sequencing matters: if the portal path has a problem, killing the old automation means
won bids get nothing at all. Prove it in the field first.

### 10. Board `item_terminology` cleanup
**Deferred by Jordan. Spare-budget day only.**

All three boards still describe the old dash/underscore naming in their own
`item_terminology` strings. Nothing reads them and matching is token-based, so this is
purely cosmetic — but it teaches a new hire the wrong format.

**Do not re-open the pipe decision itself.** Pipe won.

---

## Deliberately NOT in the top ten

- **Backfilling the historical breakage** (~8 accepted bids with no Projects item, the
  cross-linked Tedesco job, Accepted bids in the Lost Deals group). Jordan: "leave
  history alone."
- **Photo attach on the packet**, per-customer packet templates, and an ops-side
  "this handoff was incomplete" signal. All real, all v2 — see the design doc's Later
  section.
