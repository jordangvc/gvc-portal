# Inventory — Data Model and Invariants

Storage: GCS JSON documents under `portal/inventory/` in the state bucket,
every write generation-guarded (compare-and-swap). No relational DB — see
DECISIONS.md D1. All quantities are Decimal-as-string (`model.dec_str`);
floats never touch a balance. All timestamps UTC ISO-8601; display is
America/New_York.

## Documents

| Object | Owns | Module |
|---|---|---|
| `catalog.json` | items (aliases, barcodes, conversions, min-qty rules, kit templates), org units, categories | `catalog.py`, `units.py` |
| `locations.json` | location tree (kinds incl. trucks/jobs/employee custody), scan tokens | `locations.py` |
| `ledger.json` | **events[] + balances{} + assets{} + kits{} + idempotency{}** — one doc so a posting is ONE atomic write | `ledger.py` |
| `counts.json` | quick/blind count sessions | `counts.py` |
| `attention.json` | needs-review queue | `attention.py` |
| `imports.json` | import job history | `inventory_flow.py` |

## Transaction lifecycle

Client cart/outbox (localStorage) is the *draft* state. The server ledger
holds only **posted** and **reversed** events. `POST /ui/api/inventory/txn`
carries a client-generated UUID; the ledger's `idempotency` map dedups it
forever. Reversals are compensating `REVERSAL` events linked both ways
(`reverses` / `reversed_by`); each quantity line records the exact balance
`deltas` it applied, so a reversal replays them negated and a rebuild
replays them verbatim.

Types: RECEIVE · ISSUE · TRANSFER · COUNT_ADJUSTMENT · MANUAL_ADJUSTMENT ·
ASSET_ASSIGNMENT · CONDITION_CHANGE · KIT_ASSEMBLY · KIT_DISASSEMBLY ·
REVERSAL · INITIAL_LOAD.

## The 18 invariants (tests in parentheses)

1. Posted events are immutable — only linkage fields change on reversal
   (`test_posted_events_immutable_…`).
2. Reversal = equal-and-opposite compensating txn, balances restored
   exactly (same test).
3. Idempotent retries can't duplicate movement
   (`test_idempotent_duplicate_…`, HTTP: `test_field_transfer_…`).
4. An active asset has exactly one location/custodian
   (`test_asset_single_location_…`).
5. An asset can't move from where it isn't — `ASSET_NOT_AT_SOURCE`
   (same test; also blocks stale reversals after later moves).
6. Balances equal the event replay (`rebuild_balances` asserted after
   every mutating test; `/ui/api/inventory/reconcile` in production).
7. Events + balances + custody + idempotency commit in ONE guarded write
   (single-doc design; `store.mutate`).
8. Negative stock rejected — `INSUFFICIENT_STOCK` with advice
   (`test_negative_stock_…`).
9. Manager override requires a reason and flags the event; an attention
   record is raised (same test + `_after_post_hooks`).
10. Archived items blocked from new txns, visible in history
    (`test_archived_item_…`).
11. Counts never overwrite — they produce COUNT_ADJUSTMENT txns
    (`counts.approve`, `test_count_flow_over_http_…`).
12. A completed count stores expected/counted/variance/counter/time and
    the posted adjustment txn ids (session + `adjustment_txns`).
13. Cross-organization references impossible — single-org store; the
    bucket IS the org (DECISIONS.md D11; N/A until multi-tenant).
14. Kit components are never counted both in-kit and loose
    (`test_kit_assembly_no_double_count_…` conserves the total).
15. Unit conversions snapshot onto lines (`entered_qty/unit`, `factor`,
    `base_qty`) — later config edits never alter history
    (`test_unit_conversion_snapshot_…`).
16. Offline retries reuse the client UUID and stay idempotent (same as 3;
    outbox tests in `test_inventory_ui.py`).
17. Concurrent movements can't lose updates: CAS 412 → re-run on fresh
    state → stock check re-fires (`test_concurrent_last_unit_…`).
18. Timestamps stored UTC (`posted_at` asserted `+00:00`).

## Schema evolution

Docs carry `schema: 1`. Additive fields need no migration
(`ensure_shape`). A breaking change bumps `schema` and migrates inside
`ensure_shape` on first read — write the migration + a fixture test first
(`.claude/skills/inventory-migration/SKILL.md`).

## Growth

`ledger.json` grows append-only (~1–2 KB/event). At GVC volume this is a
few MB/year. The re-baseline procedure (export, INITIAL_LOAD snapshot,
archive old object — versioned bucket keeps history) is in OPERATIONS.md;
not needed before multi-year scale.
