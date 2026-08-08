# Portal performance audit (repeatable)

How we measure and keep Monday-backed screens fast. Re-run when hub / billing /
jobcheck / morning feel slow, or before blaming “we need a database.”

## Architecture facts (do not re-discover)

- Pages are **static HTML** + inline JS. There is **no SPA bundle**, no React
  hydration, and no webpack. “Bundle size” is not the bottleneck.
- HTML routes return immediately (`_cached_web_html`). **Live paint waits on
  `/ui/api/*` JSON**, and those APIs wait on Monday GraphQL.
- Every GraphQL POST goes through `MondayClient._query`
  (`adapters/monday/client.py`). Bounded retries on 429 / ComplexityException /
  5xx / transport (MAX_RETRIES=2, Retry-After capped at 4s).

## Measure (required before large changes)

```bash
# Static cold-path budget (no token)
.venv/bin/python scripts/measure_monday_paths.py

# Live counts + wall ms (needs MONDAY_API_TOKEN)
GVC_MONDAY_TRACE=1 MONDAY_API_TOKEN=… \
  .venv/bin/python scripts/measure_monday_paths.py --path hub --email you@…
GVC_MONDAY_TRACE=1 MONDAY_API_TOKEN=… \
  .venv/bin/python scripts/measure_monday_paths.py --path billing
```

In the browser (dev / staging with `GVC_MONDAY_TRACE=1` on Cloud Run):

- Hub / billing JSON may include `monday_trace` → `console.info` on hub.
- Compare `count`, `total_ms`, `max_ms`, `rate_limited`.

## Critical-path checklist

| Screen | First useful JSON | Monday shape (cold) |
|--------|-------------------|---------------------|
| Hub | HTML boot → `GET /ui/api/hub` → `GET /ui/api/hub/activity` | Rail/CTA from grants (no Monday). Live needs = morning ∥ billing (+ owner/gm). Hub skips GFolder + weather. Activity deferred. |
| Billing | `GET /ui/api/billing/hub` | 3 parallel board walks |
| Morning | `GET /ui/api/morning/brief` | Ops walk + authors + **parallel** GFolder × unique cards |
| Job Check | `GET /ui/api/jobcheck/jobs` | Reshape of Morning Ops (0 extra GraphQL when warm) |
| Invoice search | `GET /ui/api/invoice/search` | 5–7 parallel contains_text legs |

**Anti-patterns we already fixed once — do not reintroduce:**

1. Hub boot calling `/hub/refresh` **then** `/hub` (2× Monday).
2. Hub paying per-card Ops→Projects→GFolder (2 GraphQL × N) when hub UI never
   shows Open Drive.
3. Warm POSTing **concurrent** with the page’s first data fetch (stampede).
4. Warming `list:billing:accepted_bids` **and** `list:jobstart:bids` in parallel
   (same Bid Board walk twice). Derive accepted bids from jobstart L1.
5. Warming `list:jobcheck:active_jobs` **and** `list:morning:ops_items` in
   parallel (same Ops board twice). Derive Job Check from Morning L1.
6. Billing rich search running projects then bids **serially**.

## Duplicate / cache map

| Cache key | Source | Notes |
|-----------|--------|-------|
| `list:morning:ops_items` | Morning Ops walk | **Source** for Job Check picker when boards match |
| `list:jobcheck:active_jobs` | Reshape of morning Ops | Must not force a second Ops walk on warm |
| `list:jobstart:bids` | Bid Board | Source of truth for accepted-bids reshape |
| `list:billing:accepted_bids` | Reshape of jobstart | Must not force a second Bid walk on warm |
| `list:billing:ready_to_invoice` | Ops Ready group | |
| `list:billing:projects_billing:75` | Projects | |

Ops writes in Job Check invalidate **both** `list:jobcheck:active_jobs` and
`list:morning:ops_items` (membership changes).

L1 = process memory; L2 = GCS snapshots (`adapters/monday/snapshot.py`). Cloud
Run scale-to-zero empties L1 — warm + L2 matter.

## When something is slow

1. Run `measure_monday_paths.py` (budget + live if token).
2. Confirm you are not double-fetching the same JSON on boot.
3. Check `monday_trace.count` for unexpected N (GFolder, search legs, pagination).
4. Prefer: skip unused work → parallelize → share caches → defer warm →
   **bounded Monday retry** → only then off-request snapshots/DB.
5. Do **not** replace Monday wholesale without live trace numbers.

`MondayClient._query` retries 429 / ComplexityException / 5xx / transport
(MAX_RETRIES=2, Retry-After capped at 4s) — same class as Slack `post_message`.

## Regression tests to keep green

- `tests/test_monday_trace.py` — trace helpers + measure script budget
- `tests/test_hub_home.py` — single first-paint `/hub`, warm-after-paint, hub
  skips GFolder, footer rN
- `tests/test_billing_hub.py` — parallel queues + parallel rich search + warm
  derive accepted_bids / jobcheck
- `tests/test_jobcheck.py` — Morning→Job Check reshape + dual invalidate
- `tests/test_morning_full.py` — `attach_gfolder` flag + soft-fail GFolder
