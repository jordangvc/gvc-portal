# GVC Inventory

What we own, how many, where, who has it, what changed — recorded by the
crew in the moment it happens, on a phone, in the portal.

- **Field tool**: `/ui/inventory` — Drop off · Pick up · Transfer · Count ·
  Scan · Not listed · Damaged. Grant: `inventory`.
- **Admin console**: `/ui/inventory/admin` — items, assets, kits,
  locations, counts, attention queue, CSV import/export, QR labels,
  reports, reversals. Grant: `inventory_manage`.
- **Read-only**: `inventory_view` (bookkeeper/auditor).

## Documents in this folder

| Doc | What's in it |
|---|---|
| CODEBASE_ASSESSMENT.md | what the repo already had; why decisions fell out the way they did |
| DECISIONS.md | the ADRs (storage, roles, offline scope, scanning, v1 boundaries) |
| ARCHITECTURE.md | layers, trust boundaries, sequences |
| DATA_MODEL.md | documents, transaction lifecycle, the 18 invariants + their tests |
| TASK_GRAPH.md | build execution record |
| FIELD_GUIDE.md | phone-length instructions for the crew |
| ADMIN_GUIDE.md | office setup: items, imports, labels, counts, corrections |
| OFFLINE_SYNC.md | outbox contract, conflict recovery, v1 boundaries |
| SECURITY.md | authz model, input surfaces, audit |
| OPERATIONS.md | deploy/rollback, backups, reconciliation, scale path |
| TEST_PLAN.md | automated coverage map + manual checks |
| PERFORMANCE.md | measured behavior + budgets |
| RELEASE_EVIDENCE.md | commands, outputs, screenshots, rollout state |

## First-day rollout (§27 sequence, condensed)

1. Deploy (rides the normal pipeline) → `/health` shows
   `inventory_store_ok: true`.
2. Seed or create locations: `python scripts/seed_inventory.py --yes`
   for the starter world, or build them in Admin → Locations.
3. Print location labels (Admin → Labels), tape them up.
4. Import the item list (Admin → Import, template provided) — balances
   land as INITIAL_LOAD transactions.
5. Label ladders/vacs/specialty tools as assets; assemble scaffold kits.
6. Grant roles in /ui/admin (crew: `inventory`; managers:
   `inventory_manage`).
7. Run one blind count of Main Storage to true-up.
8. Crew starts recording every movement; manager reviews the attention
   queue (unknown items + variances) daily during the pilot.

The system deliberately does NOT require a complete catalog to start —
unknown-item submissions and counts build it while the crew works.
