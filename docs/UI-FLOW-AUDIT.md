# Flow / Dead-End Audit — GVC Portal

**Date:** 2026-08-08  
**Lens:** Can a field / office user continue, go back, save, and recover?

---

## Money spine (happy path)

```
Takeoff → Estimate → Job Start → Job Check → Change Order → Invoice → Paid by Check
                 ↘ Billing Hub ↗
```

Morning Brief sits beside the spine (daily control). Field Manual / Training
support the field. Admin / Activity are company tools.

---

## Page scorecard

| Page | Continue | Back | Feedback | Notes |
|---|---|---|---|---|
| Hub | Pass | N/A | Pass* | *dimmed tools now toast |
| Morning | Pass* | Hub brand | Pass* | *Maps after Optimize fixed |
| Morning GM | Pass | Link to brief | Pass | Thin but OK |
| Owner Pulse | Soft-fail | Hub | Load only | Decisions still raw-ish |
| Job Check | Pass | Hub / Change job | Strong | Dirty confirm exists |
| Job Start | Pass* | Hub / Change bid* | Strong | *autosave flush on leave |
| Billing | Pass | Hub | Pass* | *activity copy fixed |
| Invoice | Pass | Hub | Strong | Has Next: Billing / Check |
| Estimate | Pass | Hub | Strong | Has Next: Job Start |
| Change Order | Pass | Hub | Strong | Bill this CO CTA |
| Paid by Check | Pass if services up | Hub | Good | Vision/Monday cliffs |
| COI | Fail if no template | Hub | Good | Admin upload only |
| Lien | Read-only by design | Hub | Honesty banners | Not an action flow |
| Field Manual | Pass | Hub | Offline-aware | Pass |
| Training | Pass | Hub | localStorage | Locked modules inert |
| Time Off | Fail if env unset | Hub | Config notice | Setup cliff |
| Admin | Fail if not GCS | Hub | Read-only banner | Setup cliff |
| Activity | Fail without logging | Hub | 503 advice | IAM cliff |

---

## Dead ends / broken continuation (tracked)

### Fixed in UI system pass

1. **Morning Optimize → Maps button stayed dead**  
   `web/morning.html` — sync `#btnMaps` after optimize; try/catch on mutations.
2. **Job Start Change bid dropped debounce**  
   `web/jobstart.html` — flush `saveDraft()` before leaving.
3. **Hub dimmed tools silent**  
   `web/hub.html` — toast “Ask an admin for access”.
4. **Billing activity “isn't wired yet”**  
   `web/billing.html` — honest unavailable / IAM copy.

### Still open (next passes)

5. Job Start self-sent wait — clearer copy + hub/ops guidance (improved; still
   needs a human Ops acceptor; no resend API yet). Hub Accept queue is on a
   parallel branch (r71) when that lands.
6. ~~Owner Pulse raw JSON~~ — fixed this pass: structured decision cards +
   Job Check links on safety stops; decisions from owner-tagged parking +
   escalated ARs.
7. COI / Time Off / Admin — hub “needs setup” badges before opening.
8. Estimate/Invoice already have Next links — keep that pattern mandatory.
9. Photos / Projects link discovery on Job Check — strengthen empty guidance.
10. Multi-check photo soft-stop on Paid by Check.

---

## Flow rules reminder

See `docs/UI-SYSTEM.md` §3. Short version:

- Primary action obvious
- Sticky commit bar when scrolling
- Dirty leave protected
- Errors name the next step
- Success screens link the next tool on the spine
- Ungranted / unconfigured = explained, not silent
