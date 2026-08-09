# Inventory — Decisions and Assumptions

Consequential decisions, each with the reason. Format: ADR-style, short.

## D1 — Storage: GCS generation-guarded ledger, not PostgreSQL
The spec's Postgres/Drizzle defaults apply "if this is a TypeScript web app
without contrary conventions." This is a Python/FastAPI portal whose every
piece of state lives in generation-guarded GCS JSON (grants, drafts, morning
docs, checklist runs). Adding Cloud SQL means new infra, cost, secrets, and a
second persistence idiom — the exact thing spec §1.2 forbids when the repo has
a suitable pattern. **Design:** one `portal/inventory/ledger.json` object owns
`events[]` (append-only) + `balances{}` (projection) + `idempotency{}` +
`counters{}`; every post is a single compare-and-swap via `ifGenerationMatch`,
so events and balances commit atomically (invariants 6, 7, 17) and a lost
update is impossible — a concurrent writer gets a 412 and retries on fresh
state. Catalog/locations/assets/counts live in sibling objects with the same
guard. Scale ceiling ≈1 write/sec on the ledger object — far above a drywall
company's movement rate; documented in OPERATIONS.md with the Postgres
migration path if GVC ever multi-tenants this as a product.

## D2 — Roles map to the existing grants system
`inventory` = field user · `inventory_manage` = inventory manager (implies
`inventory`) · `inventory_view` = auditor/read-only · admins hold `*`.
No parallel role system. Server-side enforcement via `require_feature`.
Field-visibility restriction (spec §4) defaults to open company visibility —
GVC is one crew, not a marketplace.

## D3 — Tracking modes exactly as specified
`quantity` | `asset` (serialized) | `kit`. Kit v1 = template + instance with
component custody (components move with the kit; disassembly returns them to
loose stock at the kit's location). Assets carry condition + custodian.

## D4 — Custodians are locations
"Employee custody", trucks, job sites, storage, repair, disposal are all rows
in one location tree (`kind` field distinguishes them), so every movement is
location→location and invariant 4 ("an asset is in exactly one place") holds
by construction. Employee-custody locations are auto-created from the grants
store roster on first use.

## D5 — Units with snapshotted conversions
Item has one base unit + optional conversions (e.g. box→5000 each). The
entered quantity/unit AND the normalized base quantity are both stored on the
line (invariant 15). Precision per unit; no silent rounding — violations are
422s naming the field.

## D6 — Offline = localStorage outbox + idempotent posts (v1)
Drafts and the outbox live in localStorage keyed per user (the estimate-drafts
and hub-stash precedent), surviving restart; every post carries a client UUID
the server dedups forever (invariant 16). Sync states: offline / pending /
posted / needs-attention, honestly labeled (the Fireflies-staleness lesson —
stale must never look live). A service worker + IndexedDB was **descoped for
v1** because the portal has no SW and introducing one is a portal-wide blast
radius; the localStorage outbox delivers the required behavior (restart-safe
queueing, exactly-once posting, conflict surfacing) for the field reality of
"weak signal", if not full airplane-mode shell loading. Recorded as the one
deliberate deviation from spec §11; upgrade path documented in
OFFLINE_SYNC.md.

## D7 — Scanning via BarcodeDetector + manual fallback
In-browser camera scan (`BarcodeDetector` where available, manual code entry
everywhere always). QR tokens are opaque (`L-xxxxxxxx`, `A-xxxxxxxx`,
`K-xxxxxxxx`, `I-xxxxxxxx`), revocable, resolved server-side only for
authorized users. Labels: WeasyPrint PDF sheets using the `qrcode` dep already
in requirements. No third-party JS scanning library in v1 — manual fallback is
mandatory on every flow anyway (spec §17), so absence of BarcodeDetector
degrades to typing the printed human-readable code.

## D8 — Search in-process
Alias/typo-tolerant scoring modeled on `fieldguide/search.py` (exact code >
alias > name > fuzzy-token), plus availability-at-location boost inside
transactions. Catalog is hundreds of items; sub-ms in-process beats any
database. `pg_trgm` is moot without Postgres.

## D9 — CLAUDE.md convention kept
Repo's long working-memory CLAUDE.md is a locked house rule and stays.
Inventory gets `.claude/rules/inventory.md` (path-scoped) and this docs tree.
Spec's <200-line root CLAUDE.md is overridden by the repo convention.

## D10 — Footer/versioning
Backend slices don't bump the hub footer (not user-visible). The UI slice
bumps rN and the pinned assertions, aware that an unrelated in-flight feature
already claims r109 in the main checkout.

## D11 — Org scoping deferred
Single-org today (assessment). All records live under `portal/inventory/*` in
the org's own bucket; if the paid multi-tenant product happens, scoping is a
prefix + grants change, noted in ARCHITECTURE.md. Cross-org isolation tests
are N/A in v1 (no second org can exist — the bucket IS the org).

## D12 — Timezone
Timestamps stored UTC ISO-8601; displayed America/New_York (the portal's
existing `_ET` convention).

## D13 — v1 scope boundaries (explicit)
IN: quantity/asset/kit model, ledger + reversal + idempotency + concurrency,
drop-off/pick-up/transfer carts, quick count + blind audit, unknown item,
damage report, search, QR resolve + labels PDF, low-stock rules, attention
queue, CSV import (dry-run + commit as INITIAL_LOAD) and exports, reports,
seed, admin pages, tests, docs, screenshot/e2e evidence.
DESCOPED v1 (each with a stated path, none blocking rollout §22/§27):
service-worker shell caching (D6), Monday/Slack notification digests (in-app
attention only; Slack wire-up is config once channels are chosen), label
reprint history (tokens are revocable/replaceable which covers the security
need), asynchronous export jobs (exports are synchronous CSV at this volume),
maintenance schedules.
