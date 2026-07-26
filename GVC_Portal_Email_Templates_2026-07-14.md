# GVC Portal — Email Draft Templates (for team confirmation)

Audit date: 2026-07-14. Covers all three portal services that generate Gmail drafts
(Estimate, Invoice, Change Order). Paid-by-Check does not send an email — it only
records payment in Stripe/Monday/Drive.

All drafts land in hello@/billing@ (same mailbox) and are never auto-sent; a human
reviews and clicks Send. Re-running the same estimate/invoice/CO updates the
existing draft in place (matched by identifier in the subject) instead of creating
a duplicate.

**Change made 2026-07-14:** invoice email sign-off changed from `Andrea` to
`The Green Valley Team c/o Andrea` across all three invoice variants (standard,
final, progress). Estimate and Change Order sign-offs are unchanged (see below).

---

## 1. Estimate

Source: `orchestrators/estimate_flow.py`

**Subject:**
`Green Valley Contractors — Estimate {identifier} — {job.name}`

**Body — new estimate:**
```
{greeting_name},

Thank you for the opportunity to bid {job.name}. Attached is our estimate
({identifier}), valid through {expiry_pretty}.

The estimate total is {main_total_pretty}[, with optional add-ons bringing it
to {total_with_options_pretty} if selected.]

Please review the attached scope and pricing and let us know if you'd like to
proceed or have any questions.

Thanks,
{closing_name}
```

**Body — revision ("Update this Estimate"):** same structure; intro line becomes:
> Thank you for the opportunity to bid {job.name}. Attached is our revised
> estimate ({identifier}), reflecting the requested changes and valid through
> {expiry_pretty}. It supersedes the previous version.

`{closing_name}` = the salesperson on the deal (`prepared_by.name`), falling back
to "Green Valley Contractors" if none is set — **not changed** in this pass.

---

## 2. Invoice — UPDATED

Source: `adapters/gmail.py` → `draft_invoice_email()`

### Standard
**Subject:** `Invoice {identifier} - {job_name}`
```
{greeting_name},

[(Project note: {email_context})]

Attached is your invoice for {job_name}.

[You can pay online here:
  {hosted_invoice_url}

ACH is preferred — no processing fee. Credit card payments incur a 3%
processing fee.]

Let me know if you need anything else from us.

Thanks,
The Green Valley Team c/o Andrea
```

### Final
**Subject:** `Final Invoice {identifier} - {job_name}`
```
{greeting_name},

[(Project note: {email_context})]

Attached is the final invoice for {job_name}.

[payment block — same as standard]

Appreciate the work together on this one. Reach out if anything looks off or
if you need additional documentation for close-out.

Looking forward to the next one.

Thanks,
The Green Valley Team c/o Andrea
```

### Progress (AIA pay application)
**Subject:** `{job_name} - Pay Application #{pay_app_number} / Invoice {identifier}`
```
{greeting_name},

[(Project note: {email_context})]

Attached is pay application #{pay_app_number} for {job_name}[ covering work
through {period_end_date}].

[payment block — same as standard]

Let me know if you need anything additional to process.

Thanks,
The Green Valley Team c/o Andrea
```

---

## 3. Change Order

Source: `orchestrators/change_order_flow.py`

**Subject:** `Green Valley Contractors — Change Order {co_number} — {job_name}`

**Body:**
```
{greeting_name},

Please find attached Change Order {co_number} for {job.name}[ — {co.title}].

The total for this change order is {total_pretty}.

Please review the attached change order and reply to approve so we can
schedule the work. Let us know if you have any questions.

Thanks,
{closing_name}
```

`{closing_name}` = `prepared_by.name`, falling back to "Green Valley Contractors" —
**not changed** in this pass.

---

## Open question for the team

Estimate and Change Order still sign off with the individual preparer's name
(or "Green Valley Contractors" if unset), while Invoice now signs off with
"The Green Valley Team c/o Andrea" regardless of who ran it. Confirm whether
that split is intentional or whether estimate/CO should match the new invoice
wording too.
