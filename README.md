# GVC Invoice Generator

Takes a JSON file describing an invoice, creates a finalized Stripe invoice,
and renders a GVC-branded PDF with an embedded "Pay Now" link + QR code that
both point at Stripe's hosted payment page.

Supports residential (single line) and commercial (progress billing with
retainage, T&M change orders, AIA G702/G703 export from Excel, branded
Change Order Templates with embedded crew approvals).

This started as the MVP slice of the larger Monday-centered system. The
input JSON shape maps 1:1 onto the future Monday Invoices board fields,
so the swap to a Monday-triggered flow is a single function change
(`load_input()`).

---

## Setup (one-time)

### 1. System dependencies (Mac)

WeasyPrint needs Pango/Cairo/GDK-Pixbuf installed at the OS level:

```bash
brew install pango gdk-pixbuf libffi
```

### 2. Python environment

The venv lives at `~/.venv/` (outside the repo). Create it once:

```bash
python3 -m venv ~/.venv
~/.venv/bin/pip install -r requirements.txt
```

All scripts are invoked via the `./gvc` wrapper, which sets the
`DYLD_FALLBACK_LIBRARY_PATH` WeasyPrint needs to find Pango/Cairo and
uses `~/.venv/bin/python`. **Always run scripts through `./gvc`** — calling
`~/.venv/bin/python invoice.py` directly will fail with a
`libgobject-2.0-0` load error.

### 3. Secrets (`.env`)

Create `.env` in the project root (already gitignored). Required:

```bash
STRIPE_API_KEY=sk_live_...               # or sk_test_... for sandbox
GVC_DRIVE_SHARED_DRIVE_ID=0A...           # Shared Drive root for archive
MONDAY_API_TOKEN=eyJh...                  # Monday GraphQL token
```

Plus credential files (also gitignored):
- `.google-service-account.json` — Drive uploads + AIA export
- `.google-oauth-client.json` + `.gmail-token.json` — Gmail drafts (run `./gvc gmail.py setup` once to mint the token)

The CLI fails fast at startup if a required env var is missing for the chosen
mode (`--dry-run` doesn't require Stripe; `live` does).

---

## Usage

### Preview design without touching Stripe

```bash
./gvc invoice.py --input example_input.json --dry-run
```

Writes `output/<invoice_identifier>.pdf` using a placeholder Stripe URL.
Use this to iterate on a JSON file before committing to Stripe.

### Generate a real invoice

```bash
./gvc invoice.py --input example_input.json
```

Does, in order:
1. Looks up the Stripe Customer by `stripe_customer_id` or email, creating
   only if neither matches (idempotent on email).
2. Adds one Stripe InvoiceItem per line item, plus one for the discount.
3. Creates the Stripe Invoice in `send_invoice` collection mode (so Andrea
   controls send, not Stripe).
4. **Finalizes** the invoice so the `hosted_invoice_url` is generated.
5. Renders the branded PDF with that URL embedded as both a "Pay Now" link
   and a QR code.
6. Prints a Monday writeback payload (Stripe IDs + hosted URL + PDF path) to
   stdout — this is what the orchestrator will push back to the Monday item
   once the full pipeline is live.

**The script does NOT call Stripe's `send_invoice`.** Per the locked
architecture, Andrea reviews the GVC-branded PDF and sends it via Gmail.
The Stripe hosted URL is the payment portal, not the email channel.

### Flags

| Flag | Purpose |
|---|---|
| `--input` / `-i` | Path to invoice JSON (required) |
| `--output-dir` / `-o` | Where the PDF lands (default `./output`) |
| `--dry-run` | Skip all Stripe calls; use placeholder hosted URL |
| `--no-finalize` | Create Stripe invoice as draft (no hosted URL until finalized) |

---

## Input JSON shape

See `example_input.json` for a working sample. Schema below.

```jsonc
{
  "client": {
    "name": "Henderson Residence",              // required
    "contact_name": "Mark Henderson",           // optional, shown as "Attn:"
    "email": "mark.henderson@example.com",      // required — used for Stripe lookup
    "billing_address": "4821 Walnut Hills Drive\nCincinnati, OH 45208",
    "stripe_customer_id": null                  // optional; skips lookup if provided
  },
  "job": {
    "name": "Henderson Kitchen Remodel",        // required
    "site_address": "4821 Walnut Hills Drive\nCincinnati, OH 45208"  // optional
  },
  "invoice": {
    "identifier": "GVC-2026-0142",              // required, used as Stripe idempotency key
    "issue_date": "2026-04-12",                 // YYYY-MM-DD
    "due_date":   "2026-05-12",                 // YYYY-MM-DD
    "payment_terms": "Net 30",                  // free-text label
    "notes": "Thank you for your business...",  // optional
    "line_items": [                             // ≥ 1 required
      {
        "description": "Drywall hang and finish — Henderson Kitchen Remodel",
        "detail": "Kitchen, dining nook, ceiling repair.",  // optional sub-line
        "amount": 4298.00                       // canonical: pass total amount directly
      }
    ],
    "discount": {                               // optional, visible line item
      "description": "Repeat client courtesy (10% finishing labor)",
      "amount": 185.00                          // pass as positive; rendered as -$185.00
    }
  }
}
```

### Field rules

- `line_items[].amount` is the canonical shape (total dollars for that line).
  Back-compat: `quantity` + `unit_price` is still accepted and will be multiplied
  on enrichment, but new files should use `amount` directly.
- `client.email` is the Stripe Customer lookup key. Same email → same Stripe
  customer, even across reruns.
- `invoice.identifier` is the Stripe idempotency key. Re-running with the same
  identifier will **not** create a duplicate invoice or duplicate line items.
- Discount is rendered as a transparent line item (per the architecture's
  "courtesy work and free touch-ups should show as a transparent line").
- The `is_past_due` flag and the "Past Due" badge are computed automatically
  from `due_date` vs. today — no manual flag needed.
- `notes` accepts `\n` for line breaks. Pre-line whitespace is preserved.

---

## Commercial progress billing

Commercial jobs (`C` series identifiers) typically use AIA G702/G703 progress
billing with retainage and may include T&M change orders. The pipeline
supports both end-to-end. The extra fields below are all **optional** — a
residential invoice can ignore them entirely.

### Progress / final / standard

```jsonc
"invoice": {
  "invoice_type": "progress",   // or "final" / "standard" (default)
  "pay_app_number": 1,           // required when invoice_type == "progress"
  "period_end_date": "2026-05-31"  // YYYY-MM-DD, end of the billing period
}
```

- `invoice_type` controls the Gmail subject line, the email body wording,
  and the Stripe `description` (`"Progress Bill #N"` / `"Final Bill"` /
  `{identifier}`). With T&M / CO lines present the description also gets a
  `" plusTM"` suffix.

### Retainage

```jsonc
"invoice": {
  "retainage": {
    "percentage": 10,         // for display only — "Retainage (10%)"
    "amount": 2342.69,        // the actual held amount (positive number)
    "scope": "all",           // "base" (default) or "all"
    "description": "10% retainage held — billed at project completion"
  }
}
```

- **Stripe never sees retainage as a negative line item.** Each Stripe
  invoice item's amount is reduced by its share of retainage before being
  pushed, so Stripe always reflects the actual net due.
- `scope: "base"` (default): the first non-CO line absorbs the full retainage.
  CO / T&M lines bill at full value (typical when the contract treats change
  orders as non-retained).
- `scope: "all"`: retainage is allocated proportionally across **every**
  line (base + CO), with the last line absorbing the rounding remainder so
  cents tie out. Use when the GC's billing rules say "10% on all progress
  billings" inclusive of T&M.
- PDF renders: "Work Completed This Period" / "Retainage (10%) — held on
  this application" / "Amount Due This Application" — labels adapt to scope.

### Change orders / T&M

Two paired blocks: a `line_items[]` entry on the invoice itself (so the
amount rolls into Stripe and the customer's payable total), and a
`change_orders[]` entry that drives the standalone CO Template PDF
(GVC-branded informational doc with embedded crew approvals).

```jsonc
"invoice": {
  "line_items": [
    {
      "description": "Pay Application #1 — base contract work this period",
      "amount": 18200.00,
      "kind": "work"
    },
    {
      "description": "Change Order GVC-2026-C-005-CO-01 — T&M finishing",
      "detail": "73.5 hrs @ $70/hr + materials. See CO Template PDF.",
      "amount": 5226.90,
      "kind": "co",
      "co_number": "GVC-2026-C-005-CO-01"
    }
  ],
  "change_orders": [
    {
      "co_number": "GVC-2026-C-005-CO-01",
      "title": "T&M Finishing — Drywall touch-up and base molding",
      "description": "Long-form description of the scope.",
      "breakdown": [
        {"label": "Labor — 73.5 hrs @ $70/hr", "amount": 5145.00},
        {"label": "Materials", "amount": 81.90}
      ],
      "total": 5226.90,
      "approval_note": "Rate approved by Matthew Beaver per attached email…",
      "appendix_images": [
        "inputs/<id>/TM_Approval.png",
        "inputs/<id>/TM_Approval_Finisher.jpeg"
      ]
    }
  ]
}
```

- `line_items[].kind` defaults to `"work"`. Set `"co"` (or `"tm"`) to render
  the line with an orange "CHANGE ORDER · CO#" tag on the GVC invoice PDF.
- The line's `co_number` ties it to its `change_orders[]` block so the
  customer can cross-reference the line to the CO Template PDF.
- **CO numbering convention** (set 2026-05-19): `{invoice-id}-CO-{NN}` —
  e.g. `GVC-2026-C-005-CO-01`. Appears on the CO Template, in the invoice
  line description, and in any internal reference. Scoped per invoice so
  the second CO on the same invoice would be `GVC-2026-C-005-CO-02`.
- The CO Template PDF is **not an invoice** — it carries a high-visibility
  disclaimer banner referencing the parent invoice identifier so the
  customer or downstream GC can't mistake it for payable.
- `appendix_images` (PNG/JPEG) are embedded one-per-page after the cover.

### AIA G702/G703 from Excel (canonical path)

Excel is the source of truth. The pipeline downloads the .xlsx, exports
the named G702 + current-period G703 sheets to PDFs via Google Sheets,
and attaches them to the Gmail draft alongside the GVC invoice.

```jsonc
"invoice": {
  "aia_excel_path": "inputs/<id>/<job>_G702_G703.xlsx",
  "g702_sheet_name": "G702 - Pay Application",
  // g703_sheet_name auto-derived from pay_app_number if omitted —
  // see "G703 tab naming convention" below
}
```

- **G703 tab naming convention** (set 2026-05-19): each progress period's
  G703 sheet must be named `"{N} - G703 - Schedule of Values"` where
  N is the pay-app number. First period = `"1 - G703 - Schedule of Values"`.
  When the JSON declares `pay_app_number` and omits `g703_sheet_name`, the
  pipeline derives the name automatically.
- `g702_sheet_name` varies per workbook (no convention yet) — declare it
  explicitly.

#### Where the AIA PDFs land

After generation:

1. **Attached to the Gmail draft** so the customer receives them with the
   invoice.
2. **Written back to a dated subfolder of the job's Invoice/ folder**
   (`job.drive_invoice_folder_id`, or its alias `drive_source_folder_id`)
   with the friendly name `{identifier} - G702 - Pay App {N}.pdf` / `…G703 -
   Schedule of Values Pay App {N}.pdf`. The subfolder is named
   `Completed Invoices YYYY-MM-DD` (UTC run date) and is created on each
   live run — same-day re-runs reuse the existing subfolder and replace
   same-named files. The invoice PDF, any CO Template PDFs, and the
   sentinel all land in the same dated subfolder, so each progress bill
   has its own self-contained "what we billed" snapshot.

The writeback requires the source folder to be shared with the service
account (`gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com` —
Editor). For folders in your personal Drive, share manually. For folders
in the GVC Shared Drive, the service account already has access via its
Shared Drive membership.

### Pre-made AIA PDFs (legacy / fallback path)

If you have G702/G703 PDFs already (and don't want the Excel→PDF step),
declare them as `extra_pdfs` instead. They'll be attached to the Gmail
draft as-is. The drift risk is on you — the canonical path is Excel.

```jsonc
"invoice": {
  "extra_pdfs": [
    "inputs/<id>/G702.pdf",
    "inputs/<id>/G703.pdf"
  ]
}
```

### Per-job Invoice/ folder (new directory structure)

```jsonc
"job": {
  "drive_invoice_folder_id": "<id of the Invoice/ folder for the job>"
}
```

This binds the invoice run to a specific job folder in the GVC Drive tree:

```
Green Valley Contractors / Projects / <year> /
  <Residential|Commercial> / <customer> /
  <[Number Street] | [Builder/Client]> /
  Invoice/                  ← drive_invoice_folder_id
    ├── (billing instructions, AIA xlsx, approval images — sources)
    ├── Completed Invoices 2026-05-20/   ← created on each live run
    │     ├── <customer>_<street>_<id>.pdf   (invoice PDF)
    │     ├── <id> - G702 - Pay App N.pdf
    │     ├── <id> - G703 - Schedule of Values Pay App N.pdf
    │     ├── <id>-CO-NN.pdf                  (CO Template, if any)
    │     └── <id> - made and sent.txt        (sentinel)
    ├── Completed Invoices 2026-07-15/   ← next progress bill, separate
    └── Change Order/       ← optional, holds raw CO source docs
```

When set, each live run creates (or reuses) a `Completed Invoices
YYYY-MM-DD/` subfolder inside the Invoice/ folder and uploads ALL output
PDFs (invoice + G702/G703 + CO Templates) and the `{identifier} - made
and sent.txt` sentinel into that dated subfolder. Source files at the
Invoice/ root (billing instructions, AIA xlsx, approval images) stay
untouched — they're still readable on the next progress bill via
`drive:FILE_ID` references in the JSON.

The older field name `drive_source_folder_id` is still accepted as an
alias (same semantics). When neither field is set, drive.py falls back
to the legacy `Invoices/<year>/<customer>/` archive tree — invoice PDF
only, no AIA / CO / sentinel writeback.

Same Drive-sharing rules apply (see AIA writeback above): folders inside
the GVC Shared Drive are visible to the service account by default;
personal-Drive folders must be shared with
`gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com` as Editor.

---

## How this maps to the locked architecture

| Architecture concept | This MVP |
|---|---|
| Source of truth | The JSON input file (will become Monday Invoices board) |
| Orchestrator | `orchestrators/invoice_flow.py` (`process_one`; web entry `app.service:app`, CLI `main()`) |
| Template engine | Jinja2 + WeasyPrint (will become Google Sheets pipeline) |
| PDF storage | Local `./output` directory + per-job `Invoice/` folder in the GVC Drive tree (when `drive_invoice_folder_id` is set) |
| Payments | Stripe Invoices, finalized, hosted URL embedded in PDF ✅ matches |
| Customer identity | Stripe Customer lookup-or-create by email ✅ matches |
| Email send | Out of scope — Andrea sends via Gmail ✅ matches (admin-approved send) |
| Writeback | Currently stdout; will write to Monday on `--full` mode |

When the Monday pipeline ships, `load_input()` becomes a Monday API call and
the stdout writeback payload becomes a Monday column update. Everything else
in this script is reusable.

---

## Catching up on the overdue backlog

Recommended workflow:

1. For each overdue residential invoice, copy `example_input.json` to
   `inputs/<identifier>.json` and fill in the actual job data.
2. Run with `--dry-run` first to sanity-check the PDF.
3. Run for real (`./gvc invoice.py -i inputs/GVC-2026-0142.json`) — this
   creates the Stripe invoice and the branded PDF in one step.
4. Andrea reviews the PDF and sends it via Gmail using the existing template.
5. Paste the printed Monday writeback payload into the Monday item's
   `Stripe invoice ID` / `Hosted URL` / `PDF URL` columns (manual for now —
   automated later).

Idempotency means it's safe to re-run a backlog script. Re-running an existing
identifier returns the same Stripe invoice rather than creating a duplicate.

---

## What's deliberately not in v1

- Monday read/writeback for commercial flows (Monday → JSON works for
  residential; commercial JSON is hand-authored for now)
- Gmail send (Andrea handles via existing process — drafts only)
- Reminder scheduling (Stripe handles invoice reminders if enabled; the
  per-client reminder rules belong in the future Monday Clients board)
- Drive-folder ingest CLI (one command to take a Drive URL → download
  assets → emit JSON skeleton — captured as a follow-up; today's commercial
  jobs are seeded manually after reading the source folder)
- G703 line-item auto-extraction (currently the JSON author types out the
  schedule of values; the Excel is the source of truth at run time)

## What's done

- Stripe invoices, finalized, with hosted payment URL ✓
- GVC-branded invoice PDF with embedded "Pay Now" + QR ✓
- Drive archive upload to `Invoices/<year>/<customer>/` ✓
- Gmail draft (no auto-send) ✓
- Commercial progress billing with retainage (PDF-only, never on Stripe) ✓
- T&M / change-order line items with their own CO Template PDF (crew
  approval scans embedded) ✓
- AIA G702/G703 PDF export from Excel via Google Sheets ✓
- Source-folder writeback (fresh G702/G703 PDFs + sentinel) ✓
- Monday read for residential (`--monday-item`) ✓

---

## Files

Layered package (2026-06 — see [docs/portal-modularization-2026-06.md](docs/portal-modularization-2026-06.md)).
Imports flow one way: `app → orchestrators → subsystems/adapters → shared`.

```
GVC_Portal_System/
├── app/
│   └── service.py                 # FastAPI routes + auth ONLY (entry: app.service:app)
├── orchestrators/                 # one end-to-end flow per operation
│   ├── invoice_flow.py            #   process_one + _run/_run_correction (+ CLI main())
│   ├── check_flow.py              #   paid-by-check extract + commit
│   ├── estimate_flow.py           #   process_estimate
│   └── change_order_flow.py       #   process_change_order
├── subsystems/                    # domain logic per area (no cross-system orchestration)
│   ├── invoice/                   #   model · pdf · correct · drafts · aia
│   ├── estimate/                  #   number · drafts
│   ├── change_order/              #   document (CO PDF) · number
│   └── checks/                    #   deposit (OCR parse, match, plan)
├── adapters/                      # one module per external system (all outbound I/O)
│   ├── stripe_invoice.py drive.py gmail.py gcs.py vision.py slack_notify.py
│   └── monday/                    #   client · co · estimate
├── shared/                        # cross-cutting; imports nothing internal
│   ├── paths.py money.py boards.py errors.py
│   └── access.py auth.py portal_store.py activity.py activity_read.py
├── templates/  web/  assets/      # Jinja PDF templates · portal HTML · logo
├── inputs/  scripts/  docs/  tests/  mcp_server/
├── Dockerfile  requirements.txt  README.md  AGENTS.md
└── _attic/                        # dissolved invoice.py kept for reference (off import path)
```
