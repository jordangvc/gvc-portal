# Estimate auto-QA + Billing hub + searchable activity

**Date:** 2026-08-04  
**Branch:** `cursor/estimate-qa-billing-hub-d74a`  
**artifact_readiness:** implementation-ready

## Problem (employee lens)

1. Andrea manually re-reads every hello@ estimate draft (amount, customer name, email, body) before Send.
2. Opening Invoice requires knowing a Project # — no bridge from Estimating, no “ready to bill” list.
3. Staff must remember job names / numbers; they need lookup by builder, supervisor, address, city, state.
4. Work across hubs is hard to find later — need a searchable report of what happened.

## Locked product decisions (proceed without blocking Jordan)

| Decision | Choice | Why |
|---|---|---|
| QA notify channel | Slack **DM** to office review email + optional #bids ping | Existing `post_dm` pattern; env-overridable |
| Office review email | `GVC_OFFICE_REVIEW_EMAIL` default `andrea@greenvalleycontractors.com` | Matches current human reviewer |
| Also email Andrea? | Yes — create a short hello@ **draft to Andrea** (not auto-send) summarizing pass/fail + links | Locked architecture: drafts only |
| Billing hub gate | Reuse `invoice` feature (no new grant) | Andrea already has invoice; zero admin step |
| Billing hub URL | `/ui/billing` | Distinct “hub” from the generator form |
| Ready queue sources | Ops group `group_mm3zq4q2` (“Ready to Invoice”) + Bid Board Accepted (not yet handed off) + Projects with useful invoice-status text | Use Monday columns already in `shared/boards.py` |
| Search | Shared Monday multi-leg `contains_text` on name / project# / builder / supervisor / location | City/state live inside `location5` text today |
| Deep links on fail | Gmail draft URL, Monday item URL, portal `/ui/estimate?q=` / `/ui/billing?project=` | One click to the problem |
| Activity | Log `estimate.qa`, `billing.open`, `billing.search`, `billing.lookup`; free-text search on Activity page | Answers “what happened to X?” |

## Addendum — multi-email + no-email delivery (same ship)

- **Multi To/Cc:** `client.emails` / comma-separated `client.email` + `client.cc_emails` → Gmail To/Cc on estimate + invoice drafts.
- **No email:** checkbox → `client.no_email` + `delivery_method` (`print`|`mail`|`hand_deliver`). Draft addressed to hello@ with `[NO EMAIL — PRINT]` banner; Stripe uses synthetic `@noemail.gvc.invalid` (never mailed).
- Pure helper: `shared/recipients.py`.

## Non-goals (this ship)

- Auto-sending the client estimate (Andrea still clicks Send).
- Auto-creating Stripe invoices from Accepted bids without human confirm.
- Changing Monday board schemas / new columns.
- PandaDoc anything.

## Workstreams (parallel)

### A — Estimate auto-QA
- Pure `subsystems/estimate/qa.py`: checks draft body vs enriched payload (amount, client name, client email, subject/job, gmail draft created, monday item id when expected).
- Wire after finalize in `estimate_flow` (non-fatal).
- Slack DM + office draft via `slack_notify.notify_estimate_qa_result`.
- Tests: `tests/test_estimate_qa.py`.

### B — Multi-field search
- `adapters/monday/search.py`: `search_projects_rich`, `search_bids_rich`.
- Legs: name, project# / estimate#, builder (`text`), supervisor (`text5`), location (`location5`).
- Tests: `tests/test_monday_search.py`.

### C — Billing hub
- `adapters/monday/billing.py` + `orchestrators/billing_flow.py`.
- `web/billing.html`: queue cards + search + “Open invoice form” deep links + recent activity strip.
- Routes wired in `app/service.py` (integrator).

### D — Activity / discoverability
- Free-text `q` filter on `/ui/activity`.
- Invoice form gains rich project search (not Project #-only).
- Estimate search hint updated for multi-field.
- Hub tile: Estimating → Billing hub; bump portal r21 → r22.

## Success criteria

1. Finalize estimate → Andrea gets Slack DM: ✅ ready to send OR ❌ with specific failures + clickable links.
2. `/ui/billing` lists Ready-to-Invoice / Accepted work without typing a Project #.
3. Search finds a job by builder name, supervisor, street fragment, city, or state.
4. `/ui/activity` free-text finds customer / estimate # / “qa” / actor across hubs.
