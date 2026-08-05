# P1 DEPLOY RUNBOOK - Takeoff -> Portal Estimate Bridge

**State (Aug 5, 2026):**
- Takeoff writer: LIVE (`gvc_portal_outbox/{draftId}`, `status:"queued"`).
- Portal code: MERGED (PRs #20 + #25) and DEPLOYED on Cloud Run `gvc-invoice`
  (`gvc-invoice-system` / `us-central1`). Custom domain:
  `https://portal.greenvalleycontractors.com`.
- Live probe: `POST /v1/tasks/poll-takeoff-outbox` returns **401** without
  `X-API-Key` (endpoint present).
- Activation workflow: Actions -> **P1 activate takeoff outbox**
  (`.github/workflows/p1-activate-outbox.yml`, modes `check` | `activate` | `gauntlet`).
  ASCII-only fix commit `e2c1de1` (SITE_BASE). Check run green:
  https://github.com/jordangvc/gvc-portal/actions/runs/31003470432

## What is still blocked (Jordan / hello@ on GCP)

Verified by activate run https://github.com/jordangvc/gvc-portal/actions/runs/31003573969:

| Check | Result |
|---|---|
| `GVC_SERVICE_API_KEY` on Cloud Run | PRESENT |
| `GVC_TAKEOFF_RTDB_CREDENTIALS` / `GVC_TAKEOFF_RTDB_URL` / Firebase SA | **MISSING** |
| Deploy SA can `secretmanager.versions.access` on `gvc-service-api-key` | **NO** |
| Deploy SA can list/create Cloud Scheduler jobs | **NO** |
| Scheduler job `gvc-takeoff-outbox` | not created (skipped - no API key access) |

Poller uses Cloud Run **runtime** ADC by default, or a file at
`GVC_TAKEOFF_RTDB_CREDENTIALS`. That SA (or mounted SA) must have RTDB access
on project `gvc-takeoff`, and RTDB rules need `".indexOn": "status"` under
`/gvc_portal_outbox`.

## Jordan unlock - copy/paste (Windows / hello@)

```bash
PROJECT=gvc-invoice-system
REGION=us-central1
DEPLOY_SA=gvc-github-deploy@$PROJECT.iam.gserviceaccount.com

# 1) Let the GitHub deploy SA read the API key + manage scheduler
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$DEPLOY_SA" \
  --role="roles/secretmanager.secretAccessor" \
  --account=hello@greenvalleycontractors.com
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$DEPLOY_SA" \
  --role="roles/cloudscheduler.admin" \
  --account=hello@greenvalleycontractors.com

# 2) Grant Cloud Run runtime SA access to takeoff RTDB
#    (preferred: no extra secret file). Find runtime SA:
gcloud run services describe gvc-invoice --region=$REGION --project=$PROJECT \
  --format='value(spec.template.spec.serviceAccountName)' \
  --account=hello@greenvalleycontractors.com
# Then in Firebase console for gvc-takeoff (or IAM): allow that SA to access
# Realtime Database. Alternately mount a takeoff Firebase SA JSON and set:
#   GVC_TAKEOFF_RTDB_CREDENTIALS=/secrets/takeoff-rtdb.json
#   GVC_TAKEOFF_RTDB_URL=https://gvc-takeoff-default-rtdb.firebaseio.com

# 3) Re-run activation (creates scheduler + dry-run poll)
# GitHub -> Actions -> "P1 activate takeoff outbox" -> Run workflow -> mode=activate

# 4) Gauntlet
# Takeoff app: queue draft named "TEST - outbox bridge"
# Actions -> mode=gauntlet  (or wait <=10 min for scheduler)
# Expect RTDB status staged + draft in /ui/estimate - then DELETE. Never auto-send.
```

Manual scheduler (if Actions still cannot):

```bash
KEY=$(gcloud secrets versions access latest --secret=gvc-service-api-key --project=$PROJECT --account=hello@greenvalleycontractors.com)
gcloud scheduler jobs create http gvc-takeoff-outbox \
  --location=$REGION --project=$PROJECT \
  --schedule="*/10 * * * *" --time-zone="America/New_York" \
  --uri="https://portal.greenvalleycontractors.com/v1/tasks/poll-takeoff-outbox" \
  --http-method=POST \
  --headers="X-API-Key=$KEY,Content-Type=application/json" \
  --message-body='{"dry_run":false,"limit":20}' \
  --account=hello@greenvalleycontractors.com
```

## Ack protocol

| Actor | Writes to `gvc_portal_outbox/{draftId}` |
|---|---|
| Takeoff app | `status:"queued"` (+ payload, queuedAt, queuedBy, bidTotal) |
| Portal poller (success) | `status:"staged"`, `stagedAt`, `portalDraftId:"takeoff-{draftId}"` |
| Portal poller (bad payload) | `status:"error"`, `error`, `processedAt` |

Poller consumes ONLY `status=="queued"`; deterministic draft ids; staging goes
to shared drafts - **never finalizes, never emails**.

## Acceptance test (gauntlet)

1. Takeoff: junk draft "TEST - outbox bridge" -> Send to Portal Queue.
2. Within 10 min (or force poll): RTDB `staged`; draft visible in `/ui/estimate`.
3. Finalize dry-run for PDF if desired; do NOT send. Delete draft + RTDB entry.
