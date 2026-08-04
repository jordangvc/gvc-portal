# Estimate → Invoice dollar import (v1)

**Branch:** `cursor/estimate-invoice-import-c3e6`

## Problem

`/ui/api/invoice/lookup` prefills WHO/WHERE/Project # from the Projects board
on purpose (the office keys dollars from the actual billing materials), but
that leaves a gap: the SAME project already has an estimate on file with real
line items (or at least a rounded total on the Bid Board), and the office had
to re-type it every time.

## Fix (smallest slice)

1. **`subsystems/invoice/estimate_import.py`** — pure helpers (no I/O):
   `map_line_items` (estimate `line_items` → invoice line dicts,
   `amount = unit_price * quantity`, skips `optional: true` by default),
   `parse_monday_total` (safe `"$12,345.00"` / numeric / junk → `float | None`),
   `build_estimate_import` (assembles the payload below).
2. **`adapters/monday/client.py`** — `build_invoice_prefill` now also reads the
   Projects item's Linked Opportunity → Bid Board rounded total (`number`
   column) and Estimate # (`numbers18`), returned as a private
   `_bid_snapshot` the caller pops off.
3. **`app/service.py`** — `ui_invoice_lookup` derives the EST- number (from
   `job.project_number`'s shared core, or the Bid Board's bare core as a
   fallback), tries the as-sent Drive sidecar (`{EST-…}.gvc-est.json`, same
   file `subsystems/estimate/revision.py` writes at finalize), and attaches
   an additive `estimate_import` key to the prefill:
   `{available, estimate_number, monday_total, line_items,
   line_items_total, source, notes}`. Never raises — a Drive hiccup or a
   project with no linked estimate just degrades to `available: false`.
4. **`web/invoice.html`** — a new "Estimate import" card under Line items,
   shown after a successful lookup (mirrors the billable-COs picker style
   from #23): "Add estimate lines" / "Add estimate total" buttons, opt-in
   only, each disables itself once clicked so it can't double-add.

## Non-goals (this ship)

- No Monday schema changes (reads existing Bid Board / Projects columns only).
- No auto-overwrite of existing invoice lines — always an explicit click.
- No PandaDoc, no Stripe changes.
- A "Projects board Estimate $ mirror" column doesn't exist yet; if one is
  added later, `build_invoice_prefill` is the single place to also read it.

## Tests

`tests/test_estimate_invoice_import.py` — offline, pure helper coverage
(`parse_monday_total` edge cases, `map_line_items` optional-skip/include,
`build_estimate_import` source selection: sidecar / monday_only / none).

## Smoke test

1. Look up a project on `/ui/invoice` whose linked Bid Board item has a
   rounded total (or a Drive `{EST-…}.gvc-est.json` sidecar).
2. Confirm the "Estimate import" card appears under Line items with
   "Add estimate lines" and/or "Add estimate total".
3. Click one — a line item appears with the right description/amount; the
   button flips to "Added" and disables.
4. Confirm existing manually-typed lines are untouched.
