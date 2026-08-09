# Inventory — Performance

## Measured (2026-08-09, dev box, in-process; GCS adds one object read +
## one guarded write per mutation, ~50–150 ms intra-region on Cloud Run)

Bench: 500-item catalog, 30 locations, 200 posted transactions
(2,000 lines) — several years of GVC volume.

| Operation | Local compute | Expected live (=+1 GCS round-trip) |
|---|---|---|
| Post 10-line transaction | 14 ms | ~100–250 ms |
| Search (typo-tolerant, availability-boosted) | 12 ms | ~60–160 ms (1 read) |
| Overview payload | 8 ms | ~150–300 ms (4 reads, could parallelize later) |
| History (50 events) | 8 ms | ~60–160 ms |

Ledger doc at this scale: ~720 KB (integer-qty lines dominate). Growth is
linear in events; the re-baseline procedure exists in OPERATIONS.md long
before this matters. The spec's 500 ms p95 search budget is met with an
order of magnitude of headroom at representative volume.

## Page budgets

- Pages are static HTML + one shared stylesheet (`gvc-ui.css`, cached
  portal-wide) + page JS — no framework, no build, no CDN fonts. The
  field page's critical path is one HTML fetch + two cached assets;
  admin-console code never loads on the field page (separate documents —
  the no-build-step equivalent of code splitting).
- Images: item thumbnails are optional URLs; unknown-item photos are
  client-downscaled (≤1000 px, JPEG, ≤~380 KB) BEFORE upload, so weak
  uplinks aren't fighting 12 MP originals.
- 4G evidence: the screenshot harness `--throttle` run covers
  /ui/inventory (150 ms RTT / 1.6 Mbps) — results recorded in
  RELEASE_EVIDENCE.md with the rest of the harness output.

## Query/index notes

No database, so "indexes" are shapes: balances are pre-projected per
item×location (reads are O(1) dict hops); search is an in-process scan of
≤ a few hundred items (sub-ms per item set); history filters scan events
newest-first with an early break at the limit. The first structure to
revisit at 10× scale is history filtering (add a per-item event index in
the doc) — noted, not needed now.
