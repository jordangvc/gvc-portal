# P1 DEPLOY RUNBOOK - Takeoff -> Portal Estimate Bridge

**State (Aug 6, 2026): ACTIVATED**

- Takeoff writer: LIVE (`gvc_portal_outbox/{draftId}`, `status:"queued"`).
- Portal poller: LIVE on Cloud Run `gvc-invoice` / `portal.greenvalleycontractors.com`.
- Scheduler: `gvc-takeoff-outbox` ENABLED (every 10 min, America/New_York) ->
  `POST /v1/tasks/poll-takeoff-outbox` with `X-API-Key`.
- Cloud Run runtime SA `gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com`
  has `roles/firebasedatabase.admin` on project `gvc-takeoff`.
- Poller commit `ff65567`: falls back if RTDB `status` index is missing (400),
  so activation is not blocked on `firebase deploy --only database`.
- Gauntlet (Aug 6): queued valid test payload -> poll `checked:1 staged:1` ->
  RTDB `status:"staged"` + `portalDraftId: takeoff-p1ok...` -> outbox entry deleted.
  Never auto-sent.

## Optional follow-ups
1. Deploy takeoff Firebase rules from main (`.indexOn: ["status"]` on
   `gvc_portal_outbox`) so the poller can use server-side queries:
   `firebase deploy --only database --project gvc-takeoff`
2. Delete any leftover TEST draft in `/ui/estimate` if still visible.
3. Rotate `gvc-service-api-key` if it was pasted into terminals/chats.
4. Grant `gvc-github-deploy@...` Secret Manager + Cloud Scheduler admin if you
   want Actions workflow `p1-activate-outbox.yml` to manage this without hello@.

## Ack protocol

| Actor | Writes to `gvc_portal_outbox/{draftId}` |
|---|---|
| Takeoff app | `status:"queued"` (+ payload, queuedAt, queuedBy, bidTotal) |
| Portal poller (success) | `status:"staged"`, `stagedAt`, `portalDraftId:"takeoff-{draftId}"` |
| Portal poller (bad payload) | `status:"error"`, `error`, `processedAt` |

Poller consumes ONLY `status=="queued"`; never finalizes; never emails.
