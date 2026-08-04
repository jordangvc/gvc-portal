# Hub Daily Automation

Small, read-only-first tools replace repeatable checklist work without changing
the portal's approval gates. They do not use PandaDoc.

## What agents can automate

| Check | Automation |
|---|---|
| Takeoff → estimate | Import/stage takeoff JSON when that endpoint is present; validate `example_estimate.json`; reject or normalize legacy `EST-*` identifiers before portal numbering. |
| Portal route readiness | Import `app.service` and assert Job Start, Morning, Job Check, Estimate, photo-ready, and suggest-links routes. |
| Morning photos | Call the authenticated `photo-ready` check before asking a crew member to upload; report a missing Projects link or empty GFolder instead of letting the upload fail late. |
| Link repair | Run `suggest-links` and the GFolder/Operations backfill in dry-run; show confidence and ambiguity, never guess a write. |
| Grants | Print the recommended role matrix and diff it against a `grants.json` export. The planner never writes. |
| Job Start | With `MONDAY_API_TOKEN`, list a small accepted-bid sample and count missing Projects links / empty GFolder links. Without the token, CI skips cleanly. |

Useful commands from the repository root:

```bash
PYTHONPATH=. .venv/bin/python scripts/portal_grants_plan.py --self-test
PYTHONPATH=. .venv/bin/python scripts/portal_grants_plan.py --print
PYTHONPATH=. .venv/bin/python scripts/portal_grants_plan.py --diff path/to/grants.json
PYTHONPATH=. .venv/bin/python scripts/smoke_hub_daily.py --self-check
PYTHONPATH=. .venv/bin/python scripts/smoke_hub_daily.py --contract
PYTHONPATH=. .venv/bin/python scripts/smoke_jobstart_live.py --limit 5
```

When `scripts/backfill_projects_gfolder.py` is present after its feature branch
lands, agents may run its read-only modes:

```bash
PYTHONPATH=. .venv/bin/python scripts/backfill_projects_gfolder.py gfolder --dry-run --limit 20
PYTHONPATH=. .venv/bin/python scripts/backfill_projects_gfolder.py ops-link --dry-run --limit 20
```

`smoke_hub_daily.py --self-check` intentionally fails while required
`photo-ready` or `suggest-links` branches are unmerged. The takeoff route is
optional; when no takeoff normalizer is installed, `--contract` reports the
known legacy `EST-*` identifier as a failure instead of silently accepting an
estimate number the portal rejects.

## What humans must do

1. Deploy Cloud Run from the reviewed repository as
   `hello@greenvalleycontractors.com`. An agent may prepare and verify code but
   must not substitute another account.
2. Confirm employee Workspace emails and apply approved grants in `/ui/admin`.
   `portal_grants_plan.py` only plans/diffs. Its printed API commands still
   require an authenticated admin and are deliberately not executed.
3. Share inaccessible Drive folders with
   `gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com`.
4. Perform the first real Job Start acceptance smoke: Sales sends, a different
   Ops user accepts, and a human verifies Projects + Operations items, bid
   stamps/links, Drive packet, and Slack delivery.
5. Inspect Monday's current automation list, then retire the obsolete/bad Job
   Start and pre-portal form automations. Do not disable unrelated Site Measure
   workflows, and do not assume an automation is active from an old ID alone.
6. Approve any backfill `--apply` separately after reviewing the dry-run.

These are human gates because they involve identity, production deployment,
permissions, a first irreversible business handoff, or Monday workflow
ownership. AI can collect evidence and prepare exact commands; it cannot supply
the business approval.

## Paste-to-agent runbook

```text
Work in the GVC portal repository. Do not use PandaDoc and do not write Monday
unless I separately approve a reviewed dry-run.

1. Fetch the target branch and confirm git status is clean.
2. Wait for the repo venv setup, then run:
   PYTHONPATH=. .venv/bin/python scripts/portal_grants_plan.py --self-test
   PYTHONPATH=. .venv/bin/python scripts/smoke_hub_daily.py --self-check
   PYTHONPATH=. .venv/bin/python scripts/smoke_hub_daily.py --contract
   PYTHONPATH=. .venv/bin/python scripts/smoke_jobstart_live.py --limit 5
3. If I provide a grants export, run:
   PYTHONPATH=. .venv/bin/python scripts/portal_grants_plan.py --diff <path>
   Report missing/changed/unmanaged rows. Do not apply them.
4. If scripts/backfill_projects_gfolder.py exists, run both gfolder and ops-link
   with --dry-run --limit 20. Report ambiguous/no-match rows; do not use --apply.
5. Report every PASS, FAIL, and SKIPPED item. Treat missing required routes and
   an unnormalized EST-* estimate id as failures, not warnings.
6. Give me the exact remaining human actions: deploy as hello@, /ui/admin
   grants, Drive sharing, first Job Start accept smoke, and obsolete Monday
   automation retirement. Stop before every production write.
```
