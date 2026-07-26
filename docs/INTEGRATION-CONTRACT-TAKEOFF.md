# Integration Contract — GVC Portal ↔ Takeoff App (Jul 26, 2026)

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

Phase 2 (later): takeoff writes the same JSON to Firebase
`gvc_portal_outbox/{draftId}`; an agent or portal endpoint stages a portal
estimate DRAFT from it. Per this portal's standing rule (confirm before
assuming): staged as draft, never auto-sent.

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
