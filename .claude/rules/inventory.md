# Inventory code rules (path-scoped)

Applies to: subsystems/inventory/**, orchestrators/inventory_flow.py,
web/inventory*.html, web/gvc-inventory.js, tests/test_inventory_*.py.

1. **The ledger is append-only.** Never edit or delete a posted event;
   corrections are `reverse()` compensations. Never write `balances`
   outside `ledger.post/reverse` — the projection and the events commit
   in the same `store.mutate` or not at all.
2. **Decimal-as-string everywhere.** Quantities go through
   `model.dec_str`; floats never touch a balance. New line kinds must
   record `deltas` (what they did to balances) or `rebuild_balances`
   breaks.
3. **Every mutation route**: `require_feature` server-side, client_uuid
   idempotency where stock moves, `activity.log_event`, errors via
   `InventoryError` → the `{code, detail, advice}` envelope.
4. **Domain stays pure.** subsystems/inventory/* takes docs and returns
   docs; ALL I/O lives in inventory_flow.py. No adapter imports in the
   domain layer.
5. **UI**: gvc-ui.css only, no `<select>`, 16px inputs on phones, ≥44px
   taps, honest sync states (pending never looks posted), manual
   fallback beside every scan affordance.
6. **Tests first-class**: a new invariant gets a test in
   test_inventory_ledger.py; a new route gets an authz case in
   test_inventory_api.py. Run `python scripts/inventory_verify.py`
   before pushing.
7. Schema changes: additive via `ensure_shape`; breaking → bump `schema`
   + migration + fixture test (see .claude/skills/inventory-migration).
