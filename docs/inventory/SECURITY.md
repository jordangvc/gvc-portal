# Inventory — Security

Inherits the portal's security spine; this file covers what inventory adds.

## Authentication and authorization

- Identity: existing Google OAuth (Workspace-internal) → signed session
  cookie. No new auth surface, no tokens in the browser beyond the cookie.
- Authorization is enforced **server-side on every route** via
  `require_feature`: `inventory_view` (read) < `inventory` (movements) <
  `inventory_manage` (catalog/locations/counts-approval/reversals/imports/
  labels/attention); `/ui/api/inventory/reconcile` is admin-only. Hidden
  buttons are UX, never security — `tests/test_inventory_api.py::
  test_role_tiers_enforced_server_side` calls manager endpoints as a field
  user and asserts 403 (spec e2e #10).
- Client-supplied org/actor/balance values are never trusted: the actor is
  re-derived from the session; balances and custody come from the ledger.

## Data integrity

- All mutations idempotent by client UUID; concurrency by GCS
  generation preconditions (no lost updates — DATA_MODEL invariant 17).
- Posted history immutable; corrections are linked reversals with reasons.
- Negative-stock override: manager grant + mandatory reason + attention
  record — an audited exception, not a quiet edit.

## Injection and input surfaces

- No SQL exists. GCS object names are constants (`store.py`), never
  user-derived — no path traversal surface.
- All rendered user text goes through the pages' `esc()` helper /
  Jinja autoescape (labels template).
- CSV import: parsed with the stdlib csv module, validated row-by-row,
  size-capped (2,000 rows), all-or-nothing commit; balances only ever
  enter via INITIAL_LOAD transactions.
- Photos: client-downscaled data URLs, size-capped server-side (~400KB),
  stored only inside attention/event payloads in the private state
  bucket; no public URLs, no separate object storage keys to guess.
- Scan tokens: opaque (no sequential ids, no embedded meaning), resolved
  server-side only for authenticated `inventory_view`+ users, revocable
  per location (`rotate-token`) — a photographed label can be killed.
- Camera: `BarcodeDetector`/getUserMedia run entirely on-device; no video
  or frames are transmitted.

## Audit

Every mutation logs a structured activity event (Cloud Logging, 60-day
retention + monthly Drive export): `inventory.post` / `.reverse` / `.item`
/ `.item.merge` / `.location*` / `.asset.create` / `.count.*` / `.import`
/ `.labels` / `.attention` — actor, target, result. Administrative changes
are therefore reconstructible independently of the ledger.

## Secrets and configuration

No new secrets. The store rides `GVC_PORTAL_STATE_BUCKET` + the existing
service-account JSON. Nothing sensitive is seeded; the seed script refuses
to run without `--yes` and never touches users or credentials.

## Threat notes (reviewed)

- Stolen phone with an open session: session TTL is 1h (portal auth);
  grants can be revoked in /ui/admin, effective on the next request
  (`verify_session` re-checks provisioning per request).
- Malicious label ("scan my token"): resolution requires an authenticated
  session; an unknown token dead-ends at "not found" with no side effect.
- A hostile field user can still physically misreport counts — that is
  what blind audits, variance review, and the append-only history exist
  to bound. The system records who said what, when, immutably.
