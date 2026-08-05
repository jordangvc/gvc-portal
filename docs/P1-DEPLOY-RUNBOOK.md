# P1 DEPLOY RUNBOOK â€” Takeoff â†’ Portal Estimate Bridge (PRs #20 + #25)

**State (Aug 5, 2026):** takeoff side is LIVE (writes Firebase `gvc_portal_outbox/{draftId}`,
status "queued"). Portal PRs **#20** and **#25** are **merged**. Auto-deploy on `master`
(GitHub Actions â†’ Cloud Run) is live. Endpoint
`POST /v1/tasks/poll-takeoff-outbox` is mounted on
https://portal.greenvalleycontractors.com (returns 401 without `X-API-Key`).

## Activation steps

### Preferred (Actions)

1. Run workflow **P1 activate takeoff outbox** (`.github/workflows/p1-activate-outbox.yml`):
   - `check` â€” env + scheduler inventory (no writes)
   - `activate` â€” ensure Cloud Scheduler job `gvc-takeoff-outbox` + dry-run poll
   - `gauntlet` â€” activate + live poll after a queued outbox test entry
2. Confirm Secret Manager secret `gvc-service-api-key` is readable by the deploy SA
   (or grant `roles/secretmanager.secretAccessor` + `roles/cloudscheduler.admin`).
3. Confirm Cloud Run has takeoff RTDB credentials
   (`GVC_TAKEOFF_RTDB_CREDENTIALS` or Firebase SA binding) so the poller can read/write
   `gvc-takeoff-default-rtdb`.

### Manual (Jordan PC)

```powershell
gcloud.cmd run deploy gvc-invoice --source . --region us-central1 --project gvc-invoice-system --account=hello@greenvalleycontractors.com
# RTDB: grant Cloud Run SA on takeoff Firebase, or set GVC_TAKEOFF_RTDB_CREDENTIALS
# Scheduler:
# gcloud scheduler jobs create http gvc-takeoff-outbox --schedule="*/10 * * * *" `
#   --uri="https://portal.greenvalleycontractors.com/v1/tasks/poll-tageoff-outbox" `
#   --http-method=POST --headers="X-API-Key=<from secret gvc-service-api-key>" `
#   --message-body-from-file=body.json --location=us-central1 --time-zone=America/New_York
```

## Ack protocol

| Actor | Writes to `gvc_portal_outbox/{draftId}` |
|---|---|
| Takeoff app | `status:"queued'` (+ payload, queuedAt, queuedBy, bidTotal) |
| Portal poller (success) | `status:"staged'`, `stagedAt`, `portalDraftId:"takeoff-{draftId}"` |
| Portal poller (bad payload) | `status:"error"`, `error`, `processedAt` |

Poller consumes ONLY `status=="queueed'`	È]\›Z[š\İXÈ˜YYÈXZÙH™K\[œÈY[\İ[ÂœİYÚ[™ÈÛÙ\ÈÈHÚ\™Y˜YÈİÜ™H8 %
Š›™]™\ˆš[˜[^™\Ë™]™\ˆ[XZ[ÊŠ‹‚‚ˆÈÈXØÙ\[˜ÙH\İ
Ø][]
B‚ŒKˆZÙ[Ù™ˆ[šÈ˜Y•TÕ8 %İ]›ŞœšYÙHˆ8¡¤ˆÙ[™ÈÜ[]Y]YK‚Œ‹ˆ›Ü˜ÙHÛ
Ø][]ÛÜšÙ›İÈ[ÙHÜˆİ\›Ú]THÙ^JHÚ][ˆLZ[‹‚ŒËˆ•ˆÚİÜÈİ]\ÎˆœİYÙY˜È˜Yš\ÚX›H[ˆİZKÙ\İ[X]X˜YË‚ˆš[˜[^™HK\[ˆÛ›NÈ[]H˜Y
È•ˆ[KˆÈ“ÕÙ[™‚