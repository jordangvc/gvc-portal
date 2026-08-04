"""Invoice orchestration: the end-to-end flow + HTTP run/correction gates.

``process_one`` (the dry-run/preflight/live pipeline, formerly invoice.py) plus
``_run`` / ``_run_correction`` (the FastAPI gates, formerly service.py). This is
the one place invoice billing is coordinated across Stripe, PDF, Drive, Gmail,
and Monday — the web layer just calls in.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from shared import paths
from shared.paths import LOGS_DIR, OUTPUT_DIR, REPO_ROOT as ROOT
from shared.errors import _friendly_error
from shared import activity
from shared.recipients import normalize_client_recipients
from subsystems.invoice.model import enrich, validate, validate_environment
from subsystems.invoice.pdf import render_co_pdfs, render_pdf
from subsystems.invoice import correct as invoice_correct
from subsystems.invoice.aia import AIANotConfigured, export_aia_pdfs
from adapters.stripe_invoice import (
    create_stripe_invoice,
    preflight_stripe,
    upsert_stripe_customer,
    void_stripe_invoice,
)
from adapters.drive import DriveNotConfigured, DriveUploader, resolve_local_or_drive_path
from adapters.gcs import GCSNotConfigured, upload_preview_pdf, utc_timestamp
from adapters.gmail import GmailNotConfigured, draft_invoice_email
from adapters.monday.client import (
    MondayClient,
    MondayInsufficientData,
    MondayNotConfigured,
    write_invoice_ledger,
)
from adapters.monday import co as monday_co
import stripe

PREFLIGHT_PLACEHOLDER_URL = "https://invoice.stripe.com/PREFLIGHT/{}"
DRYRUN_PLACEHOLDER_URL = "https://invoice.stripe.com/PLACEHOLDER/{}"


def process_one(
    data: dict,
    output_dir: Path,
    *,
    mode: str,
    finalize: bool,
    source_label: str = "<dict>",
    hosted_url_override: Optional[str] = None,
    from_invoice_id: Optional[str] = None,
) -> dict:
    """
    Run the full flow on an in-memory invoice dict.

    mode is one of: "live", "preflight", "dry-run".
    hosted_url_override: skip Stripe entirely in live mode and use this URL
      (for re-renders when the Stripe invoice already exists and is correct).
    from_invoice_id: issue the Stripe invoice as a REVISION of this finalized
      invoice (the correction/reissue flow) — Stripe links them and auto-voids
      the original on finalize. Only used on the create branch in live mode.
    Returns a writeback dict. Raises on validation/Stripe errors so the
    caller can decide whether to continue (batch) or exit (single).
    """
    validate(data)
    enriched = enrich(data)
    identifier = enriched["invoice"]["identifier"]
    output_path = output_dir / f"{identifier}.pdf"

    # AIA progress billing fields — all optional
    inv_data = enriched["invoice"]
    aia_excel_raw: Optional[str] = inv_data.get("aia_excel_path")
    g703_sheet: Optional[str] = inv_data.get("g703_sheet_name")
    g702_sheet: str = inv_data.get("g702_sheet_name", "AIA G702 ")
    extra_pdfs_raw: list[str] = inv_data.get("extra_pdfs") or []

    # Per the GVC G703 tab naming convention (set 2026-05-19), each progress
    # period's G703 tab in the AIA workbook is named "{N} - G703 - Schedule
    # of Values" where N is the pay-app number. If the JSON declares
    # pay_app_number but not g703_sheet_name, derive the name automatically.
    if not g703_sheet and inv_data.get("pay_app_number") is not None:
        g703_sheet = f"{inv_data['pay_app_number']} - G703 - Schedule of Values"

    writeback: dict = {
        "source": source_label,
        "identifier": identifier,
        "mode": mode,
        "stripe_customer_id": None,
        "stripe_invoice_id": None,
        "hosted_invoice_url": None,
        "pdf_path": str(output_path),
    }

    # Business summary for the activity log (shared/activity_detail). Read from
    # `enriched` so the customer/amount recorded are EXACTLY what the PDF and the
    # Stripe invoice carry — never recomputed from raw input, which would let the
    # log drift from the document. Additive keys; existing consumers ignore them.
    _client = enriched.get("client") or {}
    writeback.update({
        "customer": _client.get("name"),
        "job_name": (enriched.get("job") or {}).get("name"),
        "recipient": _client.get("email"),
        "cc": data.get("cc_email") or (data.get("_monday") or {}).get("cc_email"),
        "amount_pretty": inv_data.get("total_pretty"),
        "due_date_pretty": inv_data.get("due_date_pretty"),
    })

    if mode == "dry-run":
        hosted_url = DRYRUN_PLACEHOLDER_URL.format(identifier)
        print(f"[dry-run {identifier}] placeholder URL: {hosted_url}")
        render_pdf(enriched, hosted_url, output_path)
        print(f"[dry-run {identifier}] PDF: {output_path}")
        writeback["hosted_invoice_url"] = hosted_url
        co_paths = render_co_pdfs(enriched, output_dir)
        for p in co_paths:
            print(f"[dry-run {identifier}] CO template: {p}")
        if co_paths:
            writeback["co_pdf_paths"] = [str(p) for p in co_paths]

        # Upload each rendered PDF to GCS so Andrea/Claude can preview it
        # in a browser without running live. Non-fatal: dry-run still
        # succeeds if GCS isn't configured or upload fails.
        run_ts = utc_timestamp()
        try:
            writeback["preview_pdf_url"] = upload_preview_pdf(
                output_path, identifier=identifier, run_timestamp=run_ts,
            )
            print(f"[dry-run {identifier}] preview URL: {writeback['preview_pdf_url']}")
        except GCSNotConfigured as e:
            print(f"[dry-run {identifier}] preview upload skipped: {e}")
        except Exception as e:
            print(f"[dry-run {identifier}] preview upload failed "
                  f"({type(e).__name__}: {e}); writeback omits preview_pdf_url",
                  file=sys.stderr)

        if co_paths:
            co_urls: list[str] = []
            for p in co_paths:
                try:
                    co_urls.append(upload_preview_pdf(
                        p, identifier=identifier, run_timestamp=run_ts,
                    ))
                except GCSNotConfigured:
                    # Already logged above for the main PDF; stay quiet.
                    break
                except Exception as e:
                    print(f"[dry-run {identifier}] CO preview upload failed for "
                          f"{p.name} ({type(e).__name__}: {e})", file=sys.stderr)
            if co_urls:
                writeback["co_preview_pdf_urls"] = co_urls
                for url in co_urls:
                    print(f"[dry-run {identifier}] CO preview URL: {url}")

        if extra_pdfs_raw:
            print(f"[dry-run {identifier}] would attach extra PDFs: {extra_pdfs_raw}")
        elif aia_excel_raw and g703_sheet:
            print(f"[dry-run {identifier}] would export AIA sheets from '{aia_excel_raw}' "
                  f"(G702: '{g702_sheet}', G703: '{g703_sheet}')")
        return writeback

    if mode == "preflight":
        report = preflight_stripe(enriched)
        print(f"[preflight {identifier}] customer={report['customer']['action']} "
              f"id={report['customer']['id'] or '-'} email={report['customer']['email']}")
        if report["existing_invoice_with_identifier"]:
            ex = report["existing_invoice_with_identifier"]
            print(f"[preflight {identifier}] EXISTING invoice with this identifier: "
                  f"{ex['id']} status={ex['status']} amount_due={ex['amount_due']}")
        else:
            print(f"[preflight {identifier}] no existing invoice with this identifier")
        hosted_url = PREFLIGHT_PLACEHOLDER_URL.format(identifier)
        render_pdf(enriched, hosted_url, output_path)
        print(f"[preflight {identifier}] PDF: {output_path}")
        co_paths = render_co_pdfs(enriched, output_dir)
        for p in co_paths:
            print(f"[preflight {identifier}] CO template: {p}")
        writeback["hosted_invoice_url"] = hosted_url
        writeback["preflight"] = report
        if co_paths:
            writeback["co_pdf_paths"] = [str(p) for p in co_paths]
        if aia_excel_raw and g703_sheet and not extra_pdfs_raw:
            try:
                # Same drive: vs local resolution as the live branch below.
                from adapters.drive import resolve_local_or_drive_path
                tmp_dir = Path(os.environ.get("GVC_OUTPUT_DIR") or "/tmp") / "aia_excel"
                excel_path = resolve_local_or_drive_path(
                    aia_excel_raw, tmp_dir=tmp_dir, project_root=ROOT,
                )
                g702_pdf, g703_pdf = export_aia_pdfs(
                    excel_path, g702_sheet=g702_sheet, g703_sheet=g703_sheet,
                    output_dir=output_dir,
                )
                print(f"[preflight {identifier}] AIA export OK: {g702_pdf.name}, {g703_pdf.name}")
            except AIANotConfigured as e:
                print(f"[preflight {identifier}] AIA export skipped: {e}")
            except Exception as e:
                print(f"[preflight {identifier}] AIA export FAILED: {type(e).__name__}: {e}",
                      file=sys.stderr)
        return writeback

    # mode == "live"
    if hosted_url_override:
        hosted_url = hosted_url_override
        print(f"[live {identifier}] skipping Stripe — using provided hosted URL")
        writeback["hosted_invoice_url"] = hosted_url
    else:
        # Pre-check for an already-finalized invoice with this identifier.
        # Stripe's idempotency_key would return the same invoice on re-run,
        # but a paid invoice rejects new InvoiceItem.create calls. The
        # explicit short-circuit also gives Andrea's Claude skill a clear
        # `already_existed: true` flag so she sees "this was already
        # issued" rather than thinking she just created a new one.
        existing_check = preflight_stripe(enriched)
        existing = existing_check.get("existing_invoice_with_identifier")
        if existing and existing.get("status") in ("open", "paid"):
            print(f"[live {identifier}] Stripe invoice already exists "
                  f"(status={existing['status']}); reusing — no new create call.")
            hosted_url = existing.get("hosted_invoice_url") or "(no hosted URL)"
            writeback["already_existed"] = True
            writeback["stripe_customer_id"] = existing_check["customer"]["id"]
            writeback["stripe_invoice_id"] = existing["id"]
            writeback["hosted_invoice_url"] = existing.get("hosted_invoice_url")
            # If this invoice number already exists but under a DIFFERENT email
            # than the one just entered, the office is almost certainly trying to
            # correct a mistake by re-running. Re-running only reuses the old
            # invoice (it can't change a finalized invoice's details); point them
            # at the correction tool instead of leaving them confused.
            if existing_check.get("existing_invoice_email_mismatch"):
                writeback["correction_hint"] = (
                    "This invoice number is already issued under a different email. "
                    "Re-running can't change a finalized invoice. To fix the "
                    "recipient or amounts, use Reissue/Correct."
                )
        else:
            # Stripe needs an email key for idempotent customer upsert. When the
            # customer has no inbox, use a synthetic .invalid address (never mailed).
            _recips = normalize_client_recipients(
                enriched["client"],
                top_level_cc=data.get("cc_email") or (data.get("_monday") or {}).get("cc_email"),
            )
            _stripe_client = dict(enriched["client"])
            _stripe_client["email"] = _recips["stripe_email"]
            customer = upsert_stripe_customer(_stripe_client)
            print(f"[live {identifier}] customer: {customer.id} ({customer.email})")
            invoice = create_stripe_invoice(customer, enriched, finalize=finalize,
                                             from_invoice_id=from_invoice_id)
            hosted_url = invoice.hosted_invoice_url or "(draft — no hosted URL until finalized)"
            print(f"[live {identifier}] invoice: {invoice.id} status={invoice.status}")
            print(f"[live {identifier}] hosted URL: {hosted_url}")
            writeback["stripe_customer_id"] = customer.id
            writeback["stripe_invoice_id"] = invoice.id
            writeback["hosted_invoice_url"] = invoice.hosted_invoice_url
    render_pdf(enriched, hosted_url, output_path)
    print(f"[live {identifier}] PDF: {output_path}")

    # Drive upload — graceful skip if credentials aren't configured yet.
    #
    # Two target paths depending on the JSON:
    #
    # (a) NEW directory structure (preferred): the JSON sets
    #     job.drive_invoice_folder_id (or its alias drive_source_folder_id)
    #     to the ID of the pre-created Invoice/ folder for the job:
    #         Projects/<year>/<Residential|Commercial>/<customer>/
    #             <[Number Street] | [Builder/Client]>/Invoice/
    #     The invoice PDF, G702/G703 PDFs, CO Template PDFs, and sentinel
    #     all land in that one folder.
    #
    # (b) LEGACY tree (pre-2026-05-20 + local-CLI fallback): no folder ID
    #     in the JSON. drive.py walks/creates
    #         Invoices/<year>/<customer>/
    #     and drops just the invoice PDF there. No CO/AIA upload (those need
    #     an explicit folder ID).
    #
    # `drive_source_folder_id` is the older name (used pre-restructure for
    # AIA writeback + sentinel only); now treated as an alias for
    # `drive_invoice_folder_id` so existing JSONs keep working.
    invoice_folder_id = (
        enriched["job"].get("drive_invoice_folder_id")
        or enriched["job"].get("drive_source_folder_id")
    )

    drive_link: Optional[str] = None
    drive_uploader: Optional[DriveUploader] = None
    # outputs_folder_id is where every generated artifact (invoice PDF,
    # G702/G703, CO Templates, sentinel) is written. Starts as the parent
    # Invoice/ folder (backwards-compat fallback) and is upgraded to a
    # "Completed Invoices <YYYY-MM-DD>" subfolder when we can create one.
    # Source materials at the Invoice/ root stay untouched either way.
    outputs_folder_id: Optional[str] = invoice_folder_id
    try:
        drive_uploader = DriveUploader()
        # Project-canonical Drive target: if the JSON didn't carry an explicit
        # Invoice/ folder id (portal + Monday-sourced runs don't), find-or-create
        # the project's own folder:
        #   Projects/<year>/<Residential|Commercial>/<customer>/<project>/Invoice/
        # so PDFs never fall back to the legacy Invoices/<year>/<customer>/ archive
        # (the "wrong spot" bug). Requires customer + project label + a known type;
        # if the type is unknown we keep the legacy fallback rather than misfile.
        if not invoice_folder_id:
            _ptype = (enriched["job"].get("project_type") or "").lower()
            if not _ptype:
                _mjt = (enriched["job"].get("monday_job_type") or "").lower()
                _ptype = ("residential" if _mjt == "residential"
                          else "commercial" if _mjt in ("commercial", "aia") else "")
            _customer = enriched["client"].get("name")
            _project_label = enriched["job"].get("name")
            if _customer and _project_label and _ptype:
                try:
                    _year = int(enriched["invoice"]["issue_date"][:4])
                    _inv_folder = drive_uploader.ensure_invoice_folder(
                        customer=_customer, project_label=_project_label,
                        project_type=_ptype, year=_year,
                    )
                    invoice_folder_id = _inv_folder["folder_id"]
                    outputs_folder_id = invoice_folder_id
                    print(f"[live {identifier}] Drive: project Invoice/ folder "
                          f"{_inv_folder['folder_path']} (id={invoice_folder_id})")
                except Exception as e:
                    print(f"[live {identifier}] project Invoice/ folder resolve FAILED "
                          f"({type(e).__name__}: {e}); falling back to legacy tree",
                          file=sys.stderr)
        # Bind generated artifacts to a per-run dated subfolder so multiple
        # progress bills on a commercial job are visually separated and the
        # parent Invoice/ folder root only contains source materials. UTC
        # date deliberately — picks one tz that's stable and unambiguous in
        # Drive UI, even if Andrea runs at midnight local.
        if invoice_folder_id:
            try:
                run_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                outputs_folder_id = drive_uploader.ensure_completed_invoices_subfolder(
                    invoice_folder_id, date_str=run_date_str,
                )
                print(f"[live {identifier}] Drive: outputs subfolder "
                      f"'Completed Invoices {run_date_str}' (id={outputs_folder_id})")
            except Exception as e:
                print(f"[live {identifier}] dated outputs subfolder creation FAILED "
                      f"({type(e).__name__}: {e}); writing outputs to the parent "
                      f"Invoice/ folder instead", file=sys.stderr)
                outputs_folder_id = invoice_folder_id
        site_address = enriched["job"].get("site_address") or enriched["client"].get("billing_address", "")
        street = site_address.split("\n", 1)[0].strip()
        if invoice_folder_id:
            # New structure: pre-created Invoice/ folder, with per-run
            # 'Completed Invoices <date>/' subfolder for outputs.
            drive_info = drive_uploader.upload_invoice_pdf_to_folder(
                output_path,
                folder_id=outputs_folder_id,
                customer=enriched["client"]["name"],
                job_street_address=street,
                invoice_number=identifier,
            )
            print(f"[live {identifier}] Drive: {drive_info['filename']} → folder {outputs_folder_id}")
        else:
            # Legacy tree: Invoices/<year>/<customer>/.
            year = int(enriched["invoice"]["issue_date"][:4])
            drive_info = drive_uploader.upload_invoice_pdf(
                output_path,
                customer=enriched["client"]["name"],
                year=year,
                job_street_address=street,
                invoice_number=identifier,
            )
            print(f"[live {identifier}] Drive: {drive_info['folder_path']}/{drive_info['filename']}")
        print(f"[live {identifier}] Drive link: {drive_info['web_view_link']}")
        writeback["drive_file_id"] = drive_info["file_id"]
        writeback["drive_web_view_link"] = drive_info["web_view_link"]
        drive_link = drive_info["web_view_link"]

        # Persist the as-billed invoice JSON alongside the PDF (same folder) so
        # the correction/reissue flow can pull the EXACT original straight from
        # Drive — no reliance on a separate cache. Keyed by identifier so it's
        # findable by name; idempotent (replaced in place on a re-run). Non-fatal.
        if outputs_folder_id is not None:
            try:
                sidecar_name = f"{identifier}.gvc.json"
                sidecar = drive_uploader.upload_or_replace_file(
                    folder_id=outputs_folder_id,
                    filename=sidecar_name,
                    data=json.dumps(data, indent=2, default=str).encode("utf-8"),
                    mimetype="application/json",
                )
                print(f"[live {identifier}] Drive: as-billed JSON {sidecar_name} "
                      f"({sidecar['action']}) → folder {outputs_folder_id}")
                writeback["drive_json_file_id"] = sidecar["file_id"]
            except Exception as e:
                print(f"[live {identifier}] as-billed JSON sidecar FAILED (non-fatal): "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
    except DriveNotConfigured as e:
        print(f"[live {identifier}] Drive upload skipped: {e}")
    except Exception as e:
        # Drive failure shouldn't block the rest of the flow — Stripe invoice
        # is already created and the local PDF still exists. Record a marker so
        # the post-finalize degradation alert can surface this swallowed failure.
        writeback["drive_status"] = f"FAILED — {type(e).__name__}: {e}"
        print(f"[live {identifier}] Drive upload FAILED (non-fatal): {type(e).__name__}: {e}",
              file=sys.stderr)

    # Drop a sentinel ("Invoice made and sent.") into the same dated
    # 'Completed Invoices <date>/' subfolder where the invoice PDF lives
    # (falls back to the parent Invoice/ folder when the dated subfolder
    # couldn't be created). Closes the loop so anyone looking at that
    # subfolder sees that the invoice was issued.
    source_folder_id = outputs_folder_id  # backward-compat name in the rest of this function
    if source_folder_id and drive_uploader is not None:
        try:
            sentinel = drive_uploader.write_completion_sentinel(
                source_folder_id, invoice_identifier=identifier,
            )
            print(f"[live {identifier}] Drive sentinel: {sentinel['name']} "
                  f"(in folder {source_folder_id})")
            writeback["drive_sentinel_file_id"] = sentinel["file_id"]
        except Exception as e:
            print(f"[live {identifier}] Drive sentinel FAILED (non-fatal): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    elif source_folder_id:
        print(f"[live {identifier}] Drive sentinel skipped: Drive uploader not configured")

    # Generate one CO Template PDF per declared change_orders entry. These
    # are informational documents (not invoices) — the disclaimer banner on
    # page 1 references the parent invoice identifier so the customer can't
    # mistake the CO for a payable doc. Attached to the Gmail draft alongside
    # G702/G703.
    co_pdfs: list[Path] = []
    try:
        co_pdfs = render_co_pdfs(enriched, output_dir)
        for p in co_pdfs:
            print(f"[live {identifier}] CO template: {p}")
        if co_pdfs:
            writeback["co_pdf_paths"] = [str(p) for p in co_pdfs]
    except Exception as e:
        print(f"[live {identifier}] CO template FAILED (non-fatal): "
              f"{type(e).__name__}: {e}", file=sys.stderr)

    # Upload CO Template PDFs into the same dated subfolder as the invoice
    # PDF (or the parent Invoice/ folder as fallback). Goal: every artifact
    # for one billing run lives in one obvious place.
    if co_pdfs and invoice_folder_id and drive_uploader is not None:
        try:
            for p in co_pdfs:
                upload = drive_uploader.upload_pdf_to_folder(
                    p, folder_id=outputs_folder_id, filename=p.name,
                )
                print(f"[live {identifier}] CO Drive upload: {upload['filename']} "
                      f"→ {upload['web_view_link']}")
            writeback["drive_co_writeback"] = "ok"
        except Exception as e:
            print(f"[live {identifier}] CO Drive upload FAILED (non-fatal): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)

    # Resolve AIA G702/G703 PDFs for progress billing.
    # Two paths: (a) JSON declares pre-existing PDFs via extra_pdfs, OR
    # (b) JSON declares the .xlsx + sheet names and we generate fresh PDFs
    # from the Excel via aia.py (the canonical path going forward).
    # extra_pdfs may be local paths (the legacy CLI) or `drive:FILE_ID` refs (the
    # web/commercial flow, where pre-made G702/G703 PDFs live in the job's
    # Drive folder). Resolve each the same way as the AIA workbook so a pasted
    # Drive link works without putting files on local disk.
    if extra_pdfs_raw:
        from adapters.drive import resolve_local_or_drive_path
        _extra_tmp = Path(os.environ.get("GVC_OUTPUT_DIR") or "/tmp") / "extra_pdfs"
        aia_pdfs: list[Path] = [
            resolve_local_or_drive_path(p, tmp_dir=_extra_tmp, project_root=ROOT)
            for p in extra_pdfs_raw
        ]
    else:
        aia_pdfs = []
    aia_pdfs_were_generated = False
    if not aia_pdfs and aia_excel_raw and g703_sheet:
        try:
            # aia_excel_path may be either a local path (the legacy CLI)
            # or a `drive:FILE_ID` reference (Andrea's cloud commercial flow
            # — the xlsx lives in the job's Drive folder and Claude passes
            # the file ID through). resolve_local_or_drive_path() downloads
            # the Drive file to /tmp on first call, then reuses it.
            from adapters.drive import resolve_local_or_drive_path
            tmp_dir = Path(os.environ.get("GVC_OUTPUT_DIR") or "/tmp") / "aia_excel"
            excel_path = resolve_local_or_drive_path(
                aia_excel_raw, tmp_dir=tmp_dir, project_root=ROOT,
            )
            g702_pdf, g703_pdf = export_aia_pdfs(
                excel_path, g702_sheet=g702_sheet, g703_sheet=g703_sheet,
                output_dir=output_dir,
            )
            aia_pdfs = [g702_pdf, g703_pdf]
            aia_pdfs_were_generated = True
            print(f"[live {identifier}] AIA export: {g702_pdf.name}, {g703_pdf.name}")
        except AIANotConfigured as e:
            print(f"[live {identifier}] AIA export skipped: {e}")
        except Exception as e:
            print(f"[live {identifier}] AIA export FAILED (non-fatal): {type(e).__name__}: {e}",
                  file=sys.stderr)

    # Write the freshly-generated AIA PDFs back to the dated outputs
    # subfolder (same place the invoice PDF lives), or the parent Invoice/
    # folder as fallback. Anyone opening that subfolder sees the
    # current-period documents without manually exporting from the xlsx
    # at the parent folder root.
    # Skipped when we used pre-existing PDFs (no point re-uploading them) or
    # when no source folder is set.
    if aia_pdfs_were_generated and source_folder_id and drive_uploader is not None:
        try:
            pay_app_n = enriched["invoice"].get("pay_app_number")
            for i, p in enumerate(aia_pdfs):
                # Friendly name: "GVC-2026-C-005 - G702 - Pay App 1.pdf" etc.
                sheet_label = "G702 - Pay App" if i == 0 else "G703 - Schedule of Values"
                suffix = f" {pay_app_n}" if pay_app_n is not None and i == 0 else \
                         (f" Pay App {pay_app_n}" if pay_app_n is not None and i == 1 else "")
                friendly = f"{identifier} - {sheet_label}{suffix}.pdf"
                upload = drive_uploader.upload_pdf_to_folder(
                    p, folder_id=source_folder_id, filename=friendly,
                )
                print(f"[live {identifier}] Drive writeback: {upload['filename']} → {upload['web_view_link']}")
            writeback["drive_aia_writeback"] = "ok"
        except Exception as e:
            print(f"[live {identifier}] Drive AIA writeback FAILED (non-fatal): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)

    # Gmail draft — graceful skip if OAuth not configured.
    # Attachments order: GVC invoice PDF (primary), then AIA G702/G703, then
    # any CO Template PDFs. Order matters because most email clients render
    # the attachment row in this sequence; keeping the invoice first signals
    # the payable doc.
    # Multi-recipient To + Cc supported; no-email customers draft to the office
    # with a print/mail banner (synthetic Stripe email is never the draft To).
    gmail_draft_url: Optional[str] = None
    try:
        top_cc = data.get("cc_email") or (data.get("_monday") or {}).get("cc_email")
        # No-email drafts stay in hello@/billing@ Drafts addressed to the office
        # mailbox Andrea already reviews (same as estimate no-email path).
        recipients = normalize_client_recipients(
            enriched["client"],
            office_fallback=os.environ.get("GVC_HELLO_FROM", "hello@greenvalleycontractors.com"),
            top_level_cc=top_cc,
        )
        writeback["recipients"] = {
            "no_email": recipients.get("no_email"),
            "delivery_method": recipients.get("delivery_method"),
            "to": recipients.get("to_emails") or [],
            "cc": recipients.get("cc_emails") or [],
            "customer_emails": recipients.get("customer_emails") or [],
        }
        inv = enriched["invoice"]
        gmail_extras: list[Path] = []
        gmail_extras.extend(aia_pdfs)
        gmail_extras.extend(co_pdfs)
        draft_result = draft_invoice_email(
            customer_name=enriched["client"]["name"],
            contact_name=enriched["client"].get("contact_name"),
            to=recipients["to_header"],
            cc=recipients.get("cc_header"),
            invoice_identifier=identifier,
            job_name=enriched["job"]["name"],
            amount_pretty=inv["total_pretty"],
            due_date_pretty=inv["due_date_pretty"],
            hosted_invoice_url=writeback.get("hosted_invoice_url") or "",
            pdf_path=output_path,
            invoice_type=inv.get("invoice_type", "standard"),
            pay_app_number=inv.get("pay_app_number"),
            period_end_date=inv.get("period_end_date_pretty"),
            extra_pdfs=gmail_extras or None,
            email_context=inv.get("email_context"),
            office_notice=recipients.get("office_notice"),
            subject_prefix=("[NO EMAIL — PRINT]" if recipients.get("no_email") else None),
        )
        gmail_draft_url = draft_result["gmail_url"]
        print(f"[live {identifier}] Gmail draft: {gmail_draft_url}"
              f" to={recipients.get('to_header')!r}")
        writeback["gmail_draft_id"] = draft_result["draft_id"]
        writeback["gmail_draft_url"] = gmail_draft_url
        # TODO(monday-trigger): when a Monday "Ready to Invoice" automation
        # invokes this flow, post `gmail_draft_url` to Andrea's notification
        # channel (Slack DM, email, or Monday item update) so she sees the
        # draft is ready to review without polling the Drafts folder.
        # For now, the draft URL goes to stdout + the returned writeback dict;
        # the eventual Monday automation can pick it up from the HTTP response
        # of service.py's /v1/invoice/from-monday endpoint.
    except GmailNotConfigured as e:
        print(f"[live {identifier}] Gmail draft skipped: {e}")
    except Exception as e:
        print(f"[live {identifier}] Gmail draft FAILED (non-fatal): {type(e).__name__}: {e}",
              file=sys.stderr)

    # Monday writeback — only when the source was a Monday item AND token is configured
    # Skipped when --hosted-url was used (Stripe objects not available).
    monday_item_id = (data.get("_monday") or {}).get("item_id")
    if monday_item_id and not hosted_url_override:
        try:
            mc = MondayClient()
            result = mc.writeback(
                monday_item_id,
                stripe_customer_id=writeback.get("stripe_customer_id"),
                stripe_invoice_id=writeback.get("stripe_invoice_id"),
                hosted_invoice_url=writeback.get("hosted_invoice_url"),
                drive_web_view_link=drive_link,
            )
            if result["written"]:
                print(f"[live {identifier}] Monday writeback: wrote {len(result['written'])} columns")
            if result["skipped"]:
                print(f"[live {identifier}] Monday writeback: skipped (column ID env not set) — {', '.join(result['skipped'])}")
        except MondayNotConfigured as e:
            print(f"[live {identifier}] Monday writeback skipped: {e}")
        except Exception as e:
            print(f"[live {identifier}] Monday writeback FAILED (non-fatal): {type(e).__name__}: {e}",
                  file=sys.stderr)

    # Invoices-board ledger row — server-side, fires on EVERY live invoice run
    # (portal UI, /v1 JSON, or Monday source). This is the replacement for the
    # log-gvc-invoice-to-monday Claude skill: deterministic, idempotent on the
    # Document # key, and graceful (never raises). Skipped on --hosted-url
    # re-renders (no fresh Stripe objects, so nothing new to record).
    if not hosted_url_override:
        from adapters.monday.client import write_invoice_ledger
        linked_pid = ((data.get("_monday") or {}).get("item_id")
                      or enriched["job"].get("monday_item_id"))
        ledger = write_invoice_ledger(
            enriched, writeback,
            linked_project_id=linked_pid,
            drive_folder_id=outputs_folder_id,
        )
        writeback["ledger"] = ledger
        if ledger.get("ledger_synced"):
            print(f"[live {identifier}] Invoices ledger: {ledger['ledger_action']} "
                  f"{ledger.get('ledger_item_url')}")
        else:
            print(f"[live {identifier}] Invoices ledger: {ledger.get('ledger_status')}")

    # Slack: notify the billing channel of a genuine NEW live invoice, then
    # alert on any silently-failed finalize step. Both best-effort — a Slack
    # problem must never break invoice creation. Skipped for re-renders
    # (hosted_url_override) and reuse of an already-issued invoice
    # (already_existed), neither of which is a new "invoice sent" event.
    try:
        from adapters.slack_notify import notify_invoice_sent, notify_finalize_degraded
        if not hosted_url_override and not writeback.get("already_existed"):
            try:
                notify_invoice_sent(enriched, writeback)
                writeback["slack_notified"] = True
            except Exception as e:  # noqa: BLE001 — non-fatal notice
                writeback["slack_error"] = f"{type(e).__name__}: {e}"
                print(f"[live {identifier}] Slack invoice notice failed (non-fatal): "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
            failed: dict[str, str] = {}
            if not writeback.get("gmail_draft_url"):
                failed["Gmail draft"] = "no draft URL on a live invoice"
            if str(writeback.get("drive_status", "")).startswith("FAILED"):
                failed["Drive save"] = writeback["drive_status"]
            if writeback.get("slack_error"):
                failed["Slack notice"] = writeback["slack_error"]
            # A silently-missed ledger row leaves the Invoices board pointing at
            # nothing (or, after a correction, at a VOIDED Stripe invoice) — the
            # exact failure class behind the CU-0166 check incident. Alert on it.
            if not (writeback.get("ledger") or {}).get("ledger_synced"):
                failed["Invoices ledger row"] = (
                    (writeback.get("ledger") or {}).get("ledger_status")
                    or "ledger write did not run")
            notify_finalize_degraded("Invoice", identifier, failed)
    except Exception:  # noqa: BLE001 — alerting must never break the flow
        pass

    return writeback


def _run(data: dict, *, mode: str, finalize: bool, source_label: str) -> dict:
    if mode in ("preflight", "live") and not os.environ.get("STRIPE_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "code": "STRIPE_NOT_CONFIGURED",
                "detail": "STRIPE_API_KEY env var not set on the service.",
                "advice": "Ask an admin to set the STRIPE_API_KEY secret.",
            },
        )

    # Configure Stripe in the request lifecycle
    if mode in ("preflight", "live"):
        import stripe
        stripe.api_key = os.environ["STRIPE_API_KEY"]

    try:
        wb = process_one(
            data,
            OUTPUT_DIR,
            mode=mode,
            finalize=finalize,
            source_label=source_label,
        )

        # Fix #4: Stamp the writeback with an unambiguous string when the run
        # was live, so Claude can read it back to Andrea verbatim.
        if mode == "live":
            wb["mode_warning"] = (
                "LIVE — Stripe invoice was created and the Gmail draft is "
                "waiting in billing@. Open the draft, review, then click Send."
            )

        # Fix #5: Promote a buried Gmail-skip to a top-level status so Claude
        # can warn Andrea instead of her thinking the draft just appeared.
        # process_one() currently prints "Gmail draft skipped: ..." but
        # doesn't propagate it. We can't catch it from here cleanly without
        # a process_one() change; instead we infer: if mode is live and
        # there's no gmail_draft_url in the writeback, surface that.
        if mode == "live" and not wb.get("gmail_draft_url"):
            wb["gmail_status"] = (
                "FAILED — no Gmail draft was created. The Stripe invoice may "
                "have been created (check `stripe_invoice_id`). Do NOT retry; "
                "ask an admin to check `gmail_ready` on /health."
            )

        # Change Order billing writeback: if this LIVE invoice bills one or more
        # COs (invoice.billed_change_orders), flip each CO → Billed on Monday.
        # Prefers top-level item ids (monday_item_id); legacy monday_subitem_id
        # still works. Runs only after Stripe success, on live; graceful +
        # idempotent (never unwinds the invoice). Works for standard or AIA
        # invoices alike — it's just a reference-driven status write.
        if mode == "live":
            refs = (data.get("invoice") or {}).get("billed_change_orders") or []
            if refs:
                wb.update(monday_co.mark_billed_batch(
                    refs,
                    invoice_identifier=wb.get("identifier") or (data.get("invoice") or {}).get("identifier", ""),
                    invoice_url=wb.get("hosted_invoice_url"),
                ))

        return {"ok": True, "writeback": wb}
    except HTTPException:
        raise
    except Exception as e:
        # Log + translate to the friendly envelope.
        print(f"[service] error during _run: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


def _run_correction(
    original: Optional[dict] = None, *, corrected: Optional[dict] = None,
    corrections: dict = None, intent: str = "auto", mode: str = "dry-run", actor: str = "",
) -> dict:
    """
    Correct an already-issued invoice as a DIFF: compare `original` to the edited
    payload (`corrected`, or `corrections` merged onto `original`), then AUTO-ROUTE:

      noop       Nothing actually changed.
      in_place   Only non-monetary fields changed (recipient email, contact,
                 phone, billing address, the email note). Edit the EXISTING Stripe
                 invoice (no money change), keep the number + hosted URL, and
                 refresh the PDF / Drive / Monday row / Gmail draft.
      revision   A monetary or document field changed. A finalized Stripe invoice
                 is immutable for these, so issue a Stripe REVISION (from_invoice)
                 under "… Rev N" — Stripe links it and auto-voids the original on
                 finalize — then fan the change out to Drive / Monday / Gmail.

    dry-run returns the field-by-field diff + the routed plan + a rebuilt preview
    PDF; live applies it. All Stripe/Drive/Monday/Gmail work reuses process_one so
    the correction never reimplements the billing flow. Errors route through the
    same _friendly_error envelope as a normal run.
    """
    corrections = corrections or {}
    if not os.environ.get("STRIPE_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "STRIPE_NOT_CONFIGURED",
                    "detail": "STRIPE_API_KEY env var not set on the service.",
                    "advice": "Ask an admin to set the STRIPE_API_KEY secret."},
        )

    import stripe
    stripe.api_key = os.environ["STRIPE_API_KEY"]

    try:
        # INLINE intent: the invoice form holds the desired end-state; force an
        # in-place recipient fix and read the "before" recipient from Stripe (the
        # only authoritative source for it). No diff/reconstruction guesswork.
        if intent == "recipient":
            if corrected is None:
                raise HTTPException(status_code=422, detail={
                    "ok": False, "code": "BAD_CORRECTION_INPUT",
                    "detail": "A recipient correction needs the current invoice data.",
                    "advice": "Re-open the invoice form and try again."})
            base = original or corrected
        else:
            if original is None:
                raise HTTPException(status_code=422, detail={
                    "ok": False, "code": "BAD_CORRECTION_INPUT",
                    "detail": "An automatic correction needs the original invoice JSON.",
                    "advice": "Paste the original invoice JSON (or use the inline button on the invoice form)."})
            base = original
            if corrected is None:
                corrected = invoice_correct.merge_corrections(original, corrections)

        identifier = (base.get("invoice") or {}).get("identifier", "")

        # Locate the original invoice in Stripe by its identifier (email-proof:
        # preflight also searches gvc_invoice_id metadata).
        report = preflight_stripe(base)
        existing = report.get("existing_invoice_with_identifier")
        existing_status = (existing or {}).get("status")
        existing_id = (existing or {}).get("id")
        existing_customer_id = (
            (report.get("customer") or {}).get("id")
            or (existing or {}).get("customer")
        )

        if intent == "recipient":
            # Forced in-place recipient fix. Build the change list from the
            # existing Stripe customer's email vs the corrected one (exact, not
            # reconstructed) so the confirm screen shows the real before→after.
            route = invoice_correct.ROUTE_IN_PLACE
            new_identifier = identifier
            old_email = None
            try:
                if existing_customer_id:
                    old_email = stripe.Customer.retrieve(existing_customer_id).get("email")
            except Exception:  # noqa: BLE001 — display-only
                old_email = None
            new_email = (corrected.get("client") or {}).get("email")
            changes = []
            if new_email and old_email != new_email:
                changes.append({"path": "client.email", "from": old_email,
                                "to": new_email, "monetary": False})
            plan = invoice_correct.plan_for_route(
                route, changes or [{"path": "client.email", "from": old_email,
                                    "to": new_email, "monetary": False}],
                existing_status=existing_status, new_identifier=new_identifier,
            )
        else:
            # AUTO: the git-diff of the invoice + the route it implies.
            changes = invoice_correct.diff_payload(base, corrected)
            route = invoice_correct.route_for_changes(changes)
            corrected_id = (corrected.get("invoice") or {}).get("identifier", identifier)
            new_identifier = (
                corrected_id if corrected_id != identifier
                else invoice_correct.next_revision_identifier(identifier)
            ) if route == invoice_correct.ROUTE_REVISION else identifier
            plan = invoice_correct.plan_for_route(
                route, changes, existing_status=existing_status, new_identifier=new_identifier,
            )

        # Payload used for the run/preview: in-place keeps the number; revision
        # carries the bumped identifier.
        run_payload = json.loads(json.dumps(corrected))  # cheap deep copy
        if route == invoice_correct.ROUTE_REVISION:
            run_payload.setdefault("invoice", {})["identifier"] = new_identifier

        if mode == "dry-run":
            preview = None
            if route != invoice_correct.ROUTE_NOOP:
                preview = process_one(
                    run_payload, OUTPUT_DIR, mode="dry-run", finalize=True,
                    source_label="ui:correct",
                )
            activity.log_event("invoice.correct.preview", actor=actor, target=f"{identifier}:{route}")
            return {
                "ok": True, "mode": "dry-run", "route": route, "plan": plan,
                "changes": changes,
                "original_identifier": identifier,
                "corrected_identifier": new_identifier,
                "original_stripe_invoice": existing,
                "preview": preview,
            }

        # ---- mode == "live" ----
        if route == invoice_correct.ROUTE_NOOP:
            return {"ok": True, "mode": "live", "route": route, "steps": [],
                    "plan": plan, "message": "Nothing changed — no correction applied."}

        steps: list[str] = []

        if route == invoice_correct.ROUTE_IN_PLACE:
            # Edit the existing invoice in place — no new Stripe invoice, no void.
            cl = run_payload.get("client") or {}
            if existing_customer_id:
                mod: dict = {}
                if cl.get("email"):
                    mod["email"] = cl["email"]
                if cl.get("name"):
                    mod["name"] = cl["name"]
                if mod:
                    stripe.Customer.modify(existing_customer_id, **mod)
                    steps.append("stripe_customer_updated")
                # Pin the re-run to this exact customer so it deterministically
                # REUSES the existing Stripe invoice (Layer-2 guard) — no new
                # create, no idempotency collision.
                run_payload.setdefault("client", {})["stripe_customer_id"] = existing_customer_id
            else:
                steps.append("stripe_customer_skip_no_match")
            wb = process_one(
                run_payload, OUTPUT_DIR, mode="live", finalize=True, source_label="ui:correct",
            )
            steps.append("pdf_drive_monday_gmail_refreshed")
            activity.log_event("invoice.correct.in_place", actor=actor, target=identifier)
            return {"ok": True, "mode": "live", "route": route, "steps": steps,
                    "changes": changes, "corrected_identifier": new_identifier,
                    "writeback": wb, "plan": plan}

        # ---- route == revision ----
        # Issue the revision via process_one with from_invoice_id → Stripe links
        # it to the original and auto-voids the original on finalize.
        wb = process_one(
            run_payload, OUTPUT_DIR, mode="live", finalize=True,
            source_label="ui:correct", from_invoice_id=existing_id,
        )
        steps.append("stripe_revision_issued" if existing_id else "stripe_new_invoice_no_original")

        # Mark the original Invoices Sent row Void (best-effort — Stripe already
        # auto-voided the original invoice itself). The note carries the NEW
        # Stripe invoice id so the old row itself points anyone (or any tool)
        # at the replacement — the missing pointer was the root cause of the
        # CU-0166 check-recording failure (2026-07-06).
        try:
            from datetime import date as _date
            mc = MondayClient()
            row = mc.find_invoice_row_by_document(identifier)
            if row:
                new_stripe_id = wb.get("stripe_invoice_id")
                stripe_ref = f" (Stripe {new_stripe_id})" if new_stripe_id else ""
                mc.set_invoice_void(
                    row["monday_item_id"],
                    note=(f"Voided — reissued as {new_identifier}{stripe_ref} "
                          f"on {_date.today().isoformat()}"),
                )
                steps.append("monday_row_voided")
            else:
                steps.append("monday_row_not_found")
        except Exception as e:  # noqa: BLE001 — ledger void is best-effort
            print(f"[correct] Monday void skipped: {type(e).__name__}: {e}", file=sys.stderr)
            steps.append("monday_void_skipped")

        # The revision's OWN ledger row (created inside process_one via
        # write_invoice_ledger) is what makes the board point at the new
        # Stripe invoice — surface a miss instead of swallowing it.
        if not (wb.get("ledger") or {}).get("ledger_synced"):
            steps.append("ledger_row_missing")

        # Delete the superseded Gmail draft (the revision got its own fresh draft
        # under the new number).
        try:
            from adapters import gmail as gmail
            d = gmail.delete_draft_by_invoice_id(identifier)
            steps.append("old_gmail_draft_deleted" if d.get("deleted") else "old_gmail_draft_absent")
        except Exception as e:  # noqa: BLE001 — draft cleanup is best-effort
            print(f"[correct] old draft delete skipped: {type(e).__name__}: {e}", file=sys.stderr)
            steps.append("old_gmail_draft_skip")

        activity.log_event("invoice.correct.revision", actor=actor,
                           target=f"{identifier} -> {new_identifier}")
        return {"ok": True, "mode": "live", "route": route, "steps": steps,
                "changes": changes, "corrected_identifier": new_identifier,
                "writeback": wb, "plan": plan}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[service] error during _run_correction: {type(e).__name__}: {e}",
              file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a GVC invoice PDF with Stripe payment link.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", "-i", help="Path to a single invoice JSON file")
    src.add_argument("--batch", "-b", help="Glob of JSON files to process (e.g. 'inputs/*.json')")
    src.add_argument("--monday-item", "-m", type=int,
                     help="Monday Projects-board item ID — auto-builds invoice from Monday data")
    ap.add_argument("--output-dir", "-o", default=str(OUTPUT_DIR), help="Output directory")

    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Skip Stripe entirely; use placeholder URL")
    mode.add_argument("--preflight", action="store_true",
                      help="Read-only Stripe lookup; renders PDF with placeholder URL but does NOT create anything")
    ap.add_argument("--no-finalize", action="store_true",
                    help="(live mode only) Leave Stripe invoice as draft — no hosted URL")
    ap.add_argument("--hosted-url",
                    help="(live mode only) Skip Stripe entirely and use this URL — for re-renders "
                         "when the Stripe invoice already exists and is correct")
    args = ap.parse_args()

    if args.dry_run:
        run_mode = "dry-run"
    elif args.preflight:
        run_mode = "preflight"
    else:
        run_mode = "live"

    if args.hosted_url and run_mode != "live":
        print("ERROR: --hosted-url is only valid in live mode (no --dry-run / --preflight).",
              file=sys.stderr)
        return 2
    if args.hosted_url and args.batch:
        print("ERROR: --hosted-url cannot be used with --batch.", file=sys.stderr)
        return 2

    needs_stripe = run_mode in ("preflight", "live") and not args.hosted_url
    warnings, errors = validate_environment(
        mode=run_mode,
        needs_stripe=needs_stripe,
        monday_source=args.monday_item is not None,
    )
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if needs_stripe:
        stripe.api_key = os.environ["STRIPE_API_KEY"]

    output_dir = Path(args.output_dir)
    finalize = not args.no_finalize

    # Batch mode
    if args.batch:
        paths = sorted(Path(p) for p in glob.glob(args.batch))
        if not paths:
            print(f"ERROR: no files matched glob: {args.batch}", file=sys.stderr)
            return 2

        if run_mode == "live":
            banner = "=" * 70
            print(banner)
            print(f"LIVE MODE — will create/finalize {len(paths)} Stripe invoice(s).")
            print(banner)
        elif run_mode == "preflight":
            print("=" * 70)
            print(f"PREFLIGHT — NO STRIPE WRITES. Checking {len(paths)} file(s).")
            print("=" * 70)

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = LOGS_DIR / f"batch-{ts}.jsonl"
        failures: list[tuple[str, str]] = []

        with open(log_path, "w") as logf:
            for p in paths:
                print(f"\n--- {p} ---")
                try:
                    with open(p, "r") as f:
                        data = json.load(f)
                    wb = process_one(data, output_dir, mode=run_mode, finalize=finalize,
                                     source_label=str(p))
                    logf.write(json.dumps(wb) + "\n")
                    logf.flush()
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    print(f"[ERROR {p}] {err}", file=sys.stderr)
                    failures.append((str(p), err))
                    logf.write(json.dumps({"input": str(p), "error": err}) + "\n")
                    logf.flush()

        print(f"\nBatch log written: {log_path}")
        if failures:
            print(f"\n{len(failures)} failure(s):", file=sys.stderr)
            for path, err in failures:
                print(f"  {path}: {err}", file=sys.stderr)
            return 1
        return 0

    # Monday-item mode — build the dict from Monday, then run process_one
    if args.monday_item is not None:
        try:
            mc = MondayClient()
            data = mc.build_invoice_dict(args.monday_item)
        except MondayNotConfigured as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        except MondayInsufficientData as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 3
        print(f"Built invoice from Monday item {args.monday_item}:")
        print(f"  customer: {data['client']['name']} <{data['client']['email']}>")
        print(f"  job:      {data['job']['name']}")
        print(f"  amount:   ${data['invoice']['line_items'][0]['amount']:,.2f}")
        print(f"  due:      {data['invoice']['due_date']}")
        wb = process_one(data, output_dir, mode=run_mode, finalize=finalize,
                         source_label=f"monday:{args.monday_item}")
        print("\nWriteback payload (preview):")
        print(json.dumps(wb, indent=2))
        return 0

    # Single-file mode
    with open(args.input, "r") as f:
        data = json.load(f)
    wb = process_one(data, output_dir, mode=run_mode, finalize=finalize,
                     source_label=args.input, hosted_url_override=args.hosted_url or None)
    print("\nWriteback payload (preview):")
    print(json.dumps(wb, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
