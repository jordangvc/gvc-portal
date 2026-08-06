# P5 SPEC — Ready-to-Invoice Consumer (Operations → Costed Draft → Stripe)

**Decision on record (Jordan, Aug 4 2026): the Portal (Stripe) is the billing system of
record.** QuickBooks has zero activity since Oct 2024 and is out of the flow.

**Target:** gvc-portal, house pattern: `orchestrators/invoice_ready_flow.py` +
Cloud Scheduler → `POST /v1/tasks/check-ready-to-invoice` (model on sent_watch_flow /
takeoff_outbox_flow). **Size:** M. **Depends on:** none (parallel to P1), but reuses
`orchestrators/invoice_flow.py` + `adapters/stripe_invoice.py`.

## Company rates (stand-behind)

From gvc-takeoff `docs/PRICING-RULE-AUG2026.md` (validated Kavouras / Delk):

| Component | Rate | Applies |
|---|---|---|
| Hang + finish labor | **$1.17 / ordered board SF** | Always |
| Material adder | **$0.70 / ordered board SF** | Only when GVC supplies board |

T&M / patch: JDC bands — $70/hr, $250 trip, 1.4 mat markup, $750 min.

## Trigger source

monday Operations board 1920364853: items in group `group_mm3zq4q2` (Ready to Invoice).

## Per-item behavior (never auto-send)

1. Resolve linked Projects item; pull Payroll Rate×Count actuals.
2. Build internal costing worksheet (by-sheet or T&M).
3. Stage DRAFT invoice for human review (live mode — post-gauntlet).
4. On human finalize+send: stamp Ops Scheduled Day = Invoiced.
5. Idempotent; per-item try/except; dry_run flag.

## Gauntlet

Dry-run replay of 3 already-invoiced jobs; drafted amounts within ±5%; zero writes in dry-run.

## Status

- **Code:** dry-run consumer shipped (`check_ready_to_invoice`, default `dry_run=true`).
- **Live staging / scheduler activate:** after dry-run gauntlet on real Ready queue.
