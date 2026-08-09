"""
Inventory subsystem — what GVC owns, how much, where, who has it.
=========================================================================
Contract for this package (see docs/inventory/ARCHITECTURE.md):

- `model`     constants, ids/tokens, validation error type
- `units`     unit registry, precision rules, conversion snapshots
- `store`     generation-guarded GCS JSON docs under portal/inventory/
              (the morning/store.py pattern — atomic compare-and-swap)
- `catalog`   items / aliases / barcodes / low-stock rules  (PURE doc ops)
- `locations` location tree / QR tokens / custodians        (PURE doc ops)
- `ledger`    THE core: append-only events + balance projection + assets +
              kits + idempotency, all in one doc so a single guarded write
              commits them together                          (PURE doc ops)
- `counts`    quick counts + blind audit sessions            (PURE doc ops)
- `attention` needs-review queue: unknown items, damage, overrides, low stock

Everything here is pure Python over plain dicts; ALL I/O goes through
`store` and is orchestrated by orchestrators/inventory_flow.py. No module
in this package may import adapters or app.
"""
