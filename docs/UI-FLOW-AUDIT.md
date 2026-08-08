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

### Fixed later

5. **Owner Pulse decisions** — wired from open Action Requests (NFJ /
   aimed-at-owner); UI cards + Job Check links (no more `JSON.stringify`).
   Hub Needs also surfaces top decisions.

6. **Job Start `with_ops` invisible on hub (r71 / r76)** — Ops had to open Job
   Start to find packets. Hub now folds GCS drafts: Accept handoff Needs for
   non-senders (admins included); senders see Waiting queue rows. Send Back is
   ops-only (same two-party rule as Accept) — senders no longer see a mislabeled
   "recall" button. Still needs a human Ops acceptor; no Sales pull-back API yet.

### Still open (next passes)

7. ~~COI / Time Off / Admin — hub “needs setup” badges~~ (r74). Rail shows
   Setup when Time Off URL / COI template / Admin GCS backend is missing.
8. Estimate/Invoice already have Next links — keep that pattern mandatory.
   Path strip now also mounts on Change Order + Paid by Check (spine includes
   CO + Check).
9. ~~Photos / Projects link discovery on Job Check~~ — blocked-photo hint now
   opens/scrolls the Link Projects panel when Ops→Projects is missing; GFolder
   gaps name the office Monday column to fill.
10. ~~Multi-check photo soft-stop on Paid by Check~~ (r75) — Confirm locked
    when extract reports `multi_check > 1`; re-upload one check to continue.
11. Money-form shared CSS extraction / Field Manual token convergence.

---

## Flow rules reminder

See `docs/UI-SYSTEM.md` §3. Short version:

- Primary action obvious
- Sticky commit bar when scrolling
- Dirty leave protected
- Errors name the next step
- Success screens link the next tool on the spine
- Ungranted / unconfigured = explained, not silent
