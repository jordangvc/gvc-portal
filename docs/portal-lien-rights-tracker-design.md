# Notice & Lien Deadline Tracker — design (Jul 26, 2026)

Jordan, Jul 26: lien-rights protection (Notice of Furnishing, pre-lien
notices, retainage follow-through) is "something we basically do zero of
inside of Green Valley right now... all manually sent. Need to fix this
for sure."

## Why the portal

This is office/money workflow with hard legal deadlines — the portal
already owns estimates → invoices → COs → AIA, reads Monday as the source
of truth, generates branded PDFs (WeasyPrint), and stages hello@ Gmail
drafts. A lien tracker is the same skeleton: facts in from Monday,
deadline math in the middle, human-approved documents out.

## The rule source

`GVC-Inbox`-adjacent validated pack: ChatGPT's tri-state legal-ops JSON
(`legal_ops_clause_map.json`, Codex 2026-07-26), validated 10/10 statute
spot-checks against primary sources on Jul 26 (see the takeoff repo's
docs/LEGAL-PACK-VALIDATION-JUL2026.md). Key deadlines it encodes:

- **Ohio** — Notice of Furnishing within **21 days** of first labor/
  materials (ORC 1311); lien affidavit 60/75 days by project type;
  retainage pass-through rules.
- **Indiana** — residential pre-lien notice **30/60 days**; lien filing
  90/60 days; public-work retainage steps.
- **Kentucky** — notice **75/120 days** by owner-occupancy; lien filing
  6 months + 7-day mailing; retainage release timing.

HARD RULE: the JSON is the machine source, but **an attorney blesses the
deadline table and every notice template before first live use** (the
pack itself carries attorney-verify flags). The tracker may never be the
only safeguard — it surfaces deadlines, humans own them.

## Shape (phases)

- **P0 — attorney gate.** Deadline table + notice templates (NOF, IN
  pre-lien, KY notice) reviewed by counsel. No code depends on this; do
  it in parallel.
- **P1 — tracker + reminders (build first).** New portal subsystem
  `lien_watch`: for every active job on the Monday Projects board with a
  state + first-furnishing date (new column or derived from stocking
  date), compute the deadline set from the rules JSON. Surface: (a) a
  portal page listing every job's notice status + days remaining,
  (b) Slack pings to Jordan/Andrea at T-14/T-7/T-3/T-1 via the existing
  slack_notify adapter, (c) a line in Quinn's morning brief. No documents
  yet — just never-miss-a-date.
- **P2 — draft generation.** One-click NOF/pre-lien PDF from the blessed
  template (WeasyPrint, same pattern as estimates), staged as a hello@
  Gmail draft + certified-mail cover sheet. DRAFT ONLY — Jordan/Andrea
  approve and send, per the standing guardrail (external messages are
  draft → approve → send, no exceptions).
- **P3 — service-of-notice options.** Evaluate certified-mail API or a
  lien-service provider for the physical-mail leg; also retainage-release
  countdown per state rules on closed jobs.

## Data notes

- Monday stays the bus: job facts read from the Projects board; notice
  status written back as columns so the whole company sees it (portal
  store keeps the audit trail).
- Rules JSON lives in this repo once P1 starts (`shared/lien_rules.json`,
  copied from the validated pack, version-stamped, attorney-review field
  per entry).
- Takeoff-app tie-in (later): "Send to Office" already carries the job's
  address/state/stock date — first furnishing usually equals the stocking
  date, so the takeoff seam can seed the clock automatically.
