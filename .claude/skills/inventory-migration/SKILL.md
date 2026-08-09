---
name: inventory-migration
description: Safely change an inventory GCS document schema (additive or breaking)
---

Additive fields: default them in the module's `ensure_shape` — no other
work; old docs upgrade lazily on read. Ship it.

Breaking change:
1. Bump `schema` in `ensure_shape`; write the in-place migration there
   (detect old shape → transform → return new). It must be idempotent.
2. Add a fixture test: paste a REAL old-shape doc into the test, run
   `ensure_shape`, assert the new shape and that a second pass is a
   no-op.
3. Back up first in prod: the state bucket is versioned — record the
   current generation of the object in the PR description
   (`gsutil ls -a gs://<bucket>/portal/inventory/<doc>.json`).
4. Deploy; the first request migrates. Verify via the relevant read API,
   then `GET /ui/api/inventory/reconcile` if the ledger was touched.
5. Rollback = route traffic to the prior revision AND restore the prior
   object generation (old code + new schema may not mix — that is why
   breaking changes need this skill).
Never write a migration script that edits balances directly; if data
must move, post INITIAL_LOAD / MANUAL_ADJUSTMENT transactions.
