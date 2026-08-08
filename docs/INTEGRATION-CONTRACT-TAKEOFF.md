# Integration Contract — GVC Portal ↔ Takeoff App (updated Aug 4, 2026)

Mirror of `docs/PORTAL-INTEGRATION-BRIEF.md` in the takeoff repo
(`C:\GVC\Takeoff\gvc-takeoff-deploy_16`). When this contract changes,
BOTH copies change in the same sitting.

## The counterpart system

GVC Takeoff app: single-file React PWA (Firebase RTDB + Netlify,
gvctakeoff.netlify.app), Jordan/Melvin's field tool — measure → estimate →
stocking/office outputs. Its repo, project memory, and Claude Code project
are separate from this one. Both repos live on the same PC; sessions in
either project may READ the other freely, but any WRITE to this repo from
the takeoff project (or vice versa) must append a dated note to this
CLAUDE.md.

## Seam 1 — inbound estimates (takeoff → portal)

The takeoff app will emit estimate JSON in EXACTLY the shape of
`example_estimate.json` (this repo). That file is the contract — do not
change its schema without updating the takeoff-side exporter
("Export for Portal" on the Review screen, Phase 1).

Field sources on the takeoff side: `prepared_by` = signed-in user;
`client` = draft's builder/customer; `job` = address + trade scope summary;
`line_items` = one per confirmed trade output (unit_price = trade total,
quantity 1, `optional: true` for alternates); `special_notes` = board-size /
MR / Type-X callouts; `notes` = lead-time text.

### Phase 1 — manual export/import (live contract)

1. In Takeoff, use **Export for Portal** on the Review screen.
2. Open `/ui/estimate?takeoff=1`.
3. In **Import from Takeoff**, upload the `.json` file or paste its contents,
   then choose **Import & save draft**.
4. The portal normalizes and validates the export, saves it to the shared
   estimate-draft store, and fills the Estimate Generator form. The estimator
   reviews every field before previewing or finalizing.

The portal accepts either the raw `example_estimate.json` object or
`{"data": <that object>}`:

- `POST /ui/api/estimate/from-takeoff` — browser session + `estimate` grant.
- `POST /v1/estimate/from-takeoff` — `X-API-Key`, for Takeoff automation or an
  agent. This endpoint stages the same shared draft and has no finalize mode.
- `GET /ui/api/estimate/takeoff-contract` — browser session + `estimate` grant;
  returns machine-readable body-shape, required-field, endpoint, and identifier
  hints.

Both POST routes return `{ok, draft, warnings}`. The returned `draft.payload`
is the normalized canonical estimate. A missing draft store returns
`STORE_NOT_CONFIGURED`; invalid exports return `TAKEOFF_PAYLOAD_INVALID` with
field-level errors.

Every supplied identifier is cleared during import, including legacy values
such as `EST-2026-1103` and even a syntactically valid portal number. The staged
draft keeps `estimate.identifier` blank; the portal assigns a fresh canonical
`YYYY-MMDD-NNN` only when a person later finalizes the estimate.

**Safety boundary:** these routes only upsert `portal/estimate-drafts.json`.
They do not render/finalize, create a Gmail draft, write Monday, post Slack,
call PandaDoc, or send anything to a client.

### Phase 2 — Firebase outbox (later)

Takeoff may later write the same JSON to Firebase
`gvc_portal_outbox/{draftId}` for automated pickup. The outbox reader/poller is
not part of Phase 1 and is not built. When added, it must call the same
draft-only staging flow; it must never auto-finalize or auto-send.

## Seam 2 — outbound results (portal/Monday → takeoff history)

The takeoff corpus needs profit-truth: {job, bid $, won/lost, invoiced $,
CO-adjusted final $} per closed job. Monday is this portal's source of
truth (one-way Monday → all), so **Monday is the bus** — the takeoff side
reads Monday board columns this portal already maintains; no direct
portal↔takeoff API coupling. If invoice/status column IDs change on the
Projects board, that's a contract change → update both copies.

## Standing facts (Jul 26, 2026)

- The former GM TERMINATED. His access to the takeoff app is blocklisted;
  audit any portal-side credentials/grants he held (access.py, Monday,
  Stripe, GCP IAM) — pending sweep.
- This Windows copy is the CANONICAL portal codebase (Jordan's call);
  Mac-era paths in older docs are historical.
- This repo is now git-versioned (initial commit 5769329, Jul 26).
  Commit every change. No remote yet — local history only.
- hello@ = billing@ = ONE Gmail token, shared with takeoff dispatch drafts.
- GVC Workspace revokes cloud-scoped OAuth tokens within hours (May 2026
  finding) — expect gcloud re-auth at deploy time.

## 2026-08-04 — Seam 1 portal staging implemented

The portal now owns normalization, validation, and shared-draft staging through
the two `from-takeoff` POST routes above. The Estimate Generator gained the
upload/paste card and `?takeoff=1` focus link. Firebase outbox pickup remains a
separate Phase 2 change.

## 2026-08-04 — Phase 2 outbox consumer implemented

The Firebase outbox poller now exists on the portal side.
`POST /v1/tasks/poll-takeoff-outbox` (X-API-Key; Cloud Scheduler every 10
minutes) reads `gvc_portal_outbox` entries with `status == "queued"` from
`https://gvc-takeoff-default-rtdb.firebaseio.com` (override with
`GVC_TAKEOFF_RTDB_URL`; credentials are the Cloud Run service account by
default, or a service-account file at `GVC_TAKEOFF_RTDB_CREDENTIALS`), then
runs the SAME normalize → validate → draft-only staging as the `from-takeoff`
routes and acks each entry in place.

Ack protocol — the portal owns every status after `queued`; takeoff writes
ONLY `queued`:

| status | written by | extra fields set        | meaning                                    |
|--------|------------|-------------------------|--------------------------------------------|
| queued | takeoff    | queuedAt, queuedBy, bidTotal | waiting for the portal sweep          |
| staged | portal     | stagedAt, portalDraftId | shared draft staged for office review      |
| error  | portal     | error, processedAt      | payload failed validation; fix and re-queue |

The portal draft id is deterministic — `takeoff-{draftId}`, sanitized to the
draft store's `^[A-Za-z0-9._-]{8,64}$` with a hash suffix when needed — so
re-runs are idempotent, and a finalized draft never resurrects because its
outbox entry is no longer `queued`. The RTDB rules must include
`".indexOn": "status"` under `/gvc_portal_outbox`. The Seam 1 safety boundary
is unchanged: the poller only upserts `portal/estimate-drafts.json` — it never
finalizes, never creates a Gmail draft, never sends. Optional Slack notice per
staged draft via `GVC_TAKEOFF_OUTBOX_SLACK=1` (default off).

## 2026-08-07 — Portal nav: `/ui/takeoff` launcher + money-spine Path strip

Hub **Takeoff** now points at **`/ui/takeoff`** (portal page, `external: false`),
not straight at Netlify. That page keeps GVC chrome (brand → hub), a **Path**
strip (Hub › Takeoff › Estimate › Job Start › Job Check › Billing › Invoice), and CTAs:
Open Takeoff app (new tab, with `?return=<portal>/&from=portal` for a future
Takeoff-side back link) · Import into Estimate (`/ui/estimate?takeoff=1`) ·
Back to hub. The same Path strip mounts on Estimate / Job Start / Job Check /
Billing so the money spine is one click between tools. Shared helpers:
`shared/flow_nav.py`, `web/gvc-flow.js`. Takeoff app still owns measure/export;
portal still owns draft staging. When Takeoff adds an in-app "Back to Portal"
control, honor the `return` query param (default `https://portal.greenvalleycontractors.com/`).
