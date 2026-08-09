# Inventory — Offline and Synchronization

Field reality: weak or absent signal on job sites. The contract below is
what v1 guarantees, what it deliberately does not, and how to recover.

## What is guaranteed

- **Drafts survive anything.** The cart and every queued submission live
  in `localStorage` (keys scoped per signed-in email), so refresh, tab
  close, phone restart, or a crash never lose entered work.
- **Exactly-once posting.** Every transaction carries a client UUID minted
  when the cart is created. The ledger's idempotency map dedups the UUID
  forever, so a retry after a dropped response can never double-move stock
  (`tests/test_inventory_api.py::test_field_transfer_idempotency…`).
- **Honest states.** Outbox entries are `pending` → `syncing` → `posted`
  or `needs_attention`. A queued transaction is NEVER displayed as
  server-confirmed; pending items say "waiting to sync". (The
  stale-data-must-never-look-live rule — the portal's Fireflies lesson.)
- **Conflicts surface, not corrupt.** If stock changed before sync (someone
  else took the last boxes), the server rejects with
  `INSUFFICIENT_STOCK` and the entry parks as `needs_attention` with the
  server's detail + advice. The user edits the line (or discards) — their
  original intent is preserved in the entry. Genuine rejections are
  never auto-retried (see the transient-failure rule below for what IS).
- **Retry discipline.** Retries fire on `online`, on app open, and on
  "Sync now", with a tries counter for backoff. The UUID never changes.
- **Transient failures stay retryable.** 5xx, network drops, 401
  (session lapse — the most common jobsite failure), 408 and 429 keep
  the entry `pending` (a 401 additionally surfaces a sign-in prompt);
  only genuine rejections (409/422…) park as needs-attention. A failed
  ONLINE submit with a 5xx also queues rather than making the user
  re-tap.
- **Full storage is loud.** If the phone's localStorage cannot persist
  a queued transaction, the cart is KEPT and the user is told the
  device is out of storage — a movement is never confirmed unless it
  is on the server or durably queued.

## What v1 deliberately does not do (DECISIONS.md D6)

- No service worker: a fully cold app-shell load with zero connectivity
  will not boot the page. In practice the crew opens the tool on cellular
  or at the shop; once open, everything above holds through signal loss.
  The upgrade path (SW scoped to `/ui/inventory` + IndexedDB outbox) is
  additive and does not change the server contract.
- Reference data (item list, locations) is cached per session in
  localStorage with an "as of" stamp; search against the cache is marked
  as cached.

## Administrator visibility

Sync failures a user parks as needs-attention can be raised into the
attention queue from the field UI; every posting failure also lands in
Cloud Logging via the 5xx handler. No device-identifying data beyond a
short client label is stored (SECURITY.md).

## Recovery playbook

- User says "my drop-off vanished": it's in their outbox (Home → Pending
  sync). If parked, the server's reason is shown on the entry.
- Duplicate suspicion: search History for the txn; identical UUID retries
  return the ORIGINAL txn_no (`already: true`) — there is nothing to
  clean up.
- Cleared browser storage before sync: that work is gone (localStorage is
  the draft store); re-enter. This is the accepted v1 trade-off vs. paper.
