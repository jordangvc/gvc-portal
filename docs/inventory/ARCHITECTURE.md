# Inventory — Architecture

```
phone / desktop (web/inventory.html · web/inventory-admin.html · gvc-inventory.js)
      │  session cookie (Google OAuth → signed cookie, existing portal auth)
      ▼
app/service.py  /ui/inventory* routes ── require_feature() per route
      │            inventory_view < inventory < inventory_manage  (shared/access.py)
      ▼
orchestrators/inventory_flow.py   ← ALL store I/O, activity audit events,
      │                              attention side-effects, CSV import/export
      ▼
subsystems/inventory/*            ← PURE domain (docs in → docs out)
      │   model · units · catalog · locations · ledger · counts · search · attention
      ▼
subsystems/inventory/store.py     ← generation-guarded GCS JSON (CAS + 1 retry)
      ▼
gs://gvc-portal-state/portal/inventory/*.json   (versioned bucket, no lifecycle)
```

## Trust boundaries

- **Browser → app**: untrusted. Every route re-derives the actor from the
  session cookie and re-checks the grant server-side; no client-supplied
  email/role/balance is believed. Payloads are validated in the domain
  layer (types, precision, label sets) — errors return the portal's
  `{code, detail, advice}` envelope.
- **app → GCS**: the service account (same identity as grants/drafts).
  Generation preconditions make concurrent writers safe.
- **QR labels**: opaque revocable tokens; resolution happens server-side
  for authenticated users only. Camera frames never leave the device.

## Sequences

**Post a movement** (drop-off/pick-up/transfer):
cart (localStorage, client_uuid minted once) → POST /txn → flow loads
catalog+locations (reads) → `store.mutate(LEDGER, post)` — inside the CAS:
duplicate-UUID check, stock check, balance bump, custody move, event
append → activity.log_event → attention hooks (best-effort) → receipt.
On GCS 412 the mutate reloads and re-runs the pure post — the duplicate
and stock checks re-fire on fresh state.

**Offline**: failed POST → outbox (localStorage) → retried on
reconnect/app-open with the SAME client_uuid → server dedups. 4xx/409
responses park the entry as needs-attention for the user to fix or
discard — never silently retried. Full contract: OFFLINE_SYNC.md.

**Count → adjustment**: session doc snapshot (expected per item) →
assignee records lines (blind view strips expected) → submit → manager
approve → flow posts COUNT_ADJUSTMENT txns via the same ledger path →
attention record `count_discrepancy`.

## Multi-tenant path (future paid product)

Single org today; the bucket is the org (DECISIONS.md D11). The migration
is mechanical: prefix objects `orgs/{org_id}/inventory/*`, add org to the
session claims, scope `store.*` by claim — no domain-layer change. A real
multi-tenant product would also be the trigger to move the ledger onto
Postgres (OPERATIONS.md → "Scale ceiling").
