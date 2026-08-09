# Inventory — Operations

## Deployment

Nothing special: the feature rides the portal's normal path — merge to
master → GitHub Actions deploys Cloud Run `gvc-invoice`. No new env vars,
no new secrets, no Dockerfile change (packages COPYed whole). Rollback =
Cloud Run → Revisions → route 100% to the previous revision (state is in
GCS, unaffected by rollbacks; the ledger is append-only so old code reads
new docs safely — additive schema rule, DATA_MODEL.md).

Config validation: `/health` gains `inventory_store_ok` (a REAL read of
the ledger object, not env presence) + `inventory_events`. If it reports
false, check `GVC_PORTAL_STATE_BUCKET` and the service-account secret —
the same store as grants, so grants problems and inventory problems will
travel together.

## Backups and restore

The state bucket (`gs://gvc-portal-state`) has object VERSIONING ON and no
lifecycle rules (the 2026-07-02 grants-wipe rule). Every ledger write is a
new version — point-in-time restore is `gsutil cp` of a prior generation.
Full-org export: `GET /ui/api/inventory/export.csv?kind=balances|history`
(auditor grant suffices) — attach both to any monthly records ritual.

## Reconciliation and consistency

- `GET /ui/api/inventory/reconcile` (admin): diffs stored balances against
  a pure replay of every event's recorded deltas. `consistent: true` is
  the expected steady state; any diff means the projection was corrupted
  outside the posting path — restore the prior object version and
  investigate before writing more.
- Orphan checks are inherent: assets/kits reference locations by id and
  the admin UI's location-contents view surfaces anything sitting at a
  deactivated location (attention kind `item_at_inactive_location` can be
  raised manually from there).

## Routine tasks

| Task | How |
|---|---|
| Add users | /ui/admin → grant `inventory` (crew), `inventory_manage` (managers), `inventory_view` (bookkeeper/auditor) |
| New truck/job/zone | Inventory Admin → Locations → add (job sites can carry a Monday item id for future enrichment) |
| Print labels | Inventory Admin → Labels → basket → PDF (Avery 5160) |
| Annual count | Counts → start blind per storage location → crew counts → review variances → approve |
| Compromised label | Locations → Rotate token (old label dead instantly) |
| Stuck sync complaint | OFFLINE_SYNC.md recovery playbook |

## Scale ceiling and the Postgres path

The single-ledger-object design serializes writes (~1/sec sustained).
GVC's real volume is tens of movements/day. If this becomes a multi-crew
paid product: (1) split the ledger per org, (2) move events+balances to
Cloud SQL Postgres with the same pure domain layer (post/reverse are
already pure functions — the store swap is contained in
`inventory_flow.py` + `store.py`), (3) keep GCS docs for catalog/
locations. The invariants and tests carry over unchanged — that is why
the domain layer was built pure.

## Monitoring

- 5xx alerts already flow to the ops Slack channel via the portal's
  exception handler.
- Watch for `[inventory] attention hook failed` in logs (best-effort
  side-effects) and `INVENTORY_NOT_CONFIGURED` 503s (store misconfig).
- The activity log answers "who did what": filter actions `inventory.*`
  in /ui/activity.
