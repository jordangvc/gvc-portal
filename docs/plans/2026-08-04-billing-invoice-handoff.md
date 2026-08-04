# Billing → Invoice deep-link handoff (r25)

## Problem
Open Invoice from Billing Hub opened a blank invoice form. Crew had to remember/copy Project #.

## Cause
Billing Hub already emitted `/ui/invoice?monday_item_id=…` when Project # was missing, but `bootInvoiceFromUrl()` only read `project_number` / `q`.

## Fix
1. Invoice boot honors `monday_item_id` / `item_id`
2. Job Start boot honors `?bid=`
3. `invoice_href` carries Project # + Monday id (+ q fallback when no PN)
