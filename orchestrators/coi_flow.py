"""
GVC COI generator flow.
=========================================================================
Form in -> stamped Certificate of Insurance out -> Gmail draft in hello@.

The COI itself is NOT rendered here — the agent-issued blank ACORD 25 lives in
the portal state bucket (subsystems/coi/template.py) and stamping writes only
the certificate-holder name + address into its empty box
(subsystems/coi/stamp.py). Everything on the form is the agent's; we never
touch the policy data.

modes (mirrors the estimate/CO flows so the UX stays uniform):
  "dry-run"  : stamp + GCS preview URL. No Drive, no email, no Slack.
  "finalize" : stamp + file to Drive ("COIs Sent/<year>/") + hello@ Gmail
               draft (PDF attached, addressed to the builder contact) + Slack
               notice (GVC_COI_SLACK_CHANNEL) + Monday log (PLACEHOLDER —
               skips cleanly until GVC_MONDAY_COI_BOARD_ID is set; the
               destination board is still being decided). The draft is NOT
               sent — a human reviews and clicks Send (locked posture).

Input shape:
    {
      "holder":  {"name": "CMsquared LLC",
                  "address": "5777 Kellogg Ave\nCincinnati, OH 45230"},
      "contact": {"name": "Jane Smith", "email": "jane@cm2.com"}
    }
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from subsystems.coi.stamp import (
    coi_filename,
    coi_identifier,
    holder_lines,
    pretty_expiry,
    stamp_certificate_holder,
)

HELLO_FROM_ADDR = "hello@greenvalleycontractors.com"


# ---------------------------------------------------------------------------
# Validation + email body (pure)
# ---------------------------------------------------------------------------

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def validate(data: dict) -> None:
    _require(isinstance(data, dict), "COI payload must be an object.")
    holder = data.get("holder") or {}
    contact = data.get("contact") or {}
    _require(bool((holder.get("name") or "").strip()),
             "holder.name (Name / Project Name) is required.")
    _require(bool((holder.get("address") or "").strip()),
             "holder.address is required.")
    _require(bool((contact.get("name") or "").strip()),
             "contact.name is required (the draft is addressed to them).")
    email = (contact.get("email") or "").strip()
    _require("@" in email, "contact.email must be a valid email address.")


def compose_email_body(holder_name: str, contact_name: str,
                       expiry_label: str | None) -> str:
    """PURE: the hello@ draft body. Short — Andrea reviews before Send."""
    greeting = (contact_name or "there").split()[0]
    exp = pretty_expiry(expiry_label)
    validity = f", valid through {exp}" if exp else ""
    return "\n".join([
        f"{greeting},",
        "",
        f"Attached is Green Valley Contractors' current Certificate of "
        f"Liability Insurance, issued to {holder_name}{validity}.",
        "",
        "If you need an additional insured endorsement or any changes to the "
        "certificate, just let us know and we'll arrange it through our agent.",
        "",
        "Thanks,",
        "The Green Valley Team",
    ])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def process_coi(
    data: dict,
    output_dir: Path,
    *,
    mode: str,
    source_label: str = "<dict>",
) -> dict:
    """
    Run the COI flow. mode is "dry-run" or "finalize". Returns a writeback
    dict. Raises ValueError on validation problems and CoiTemplateMissing /
    PortalStoreNotConfigured when the blank template isn't available (the
    route maps those to friendly 503s).
    """
    validate(data)
    holder = data["holder"]
    contact = data["contact"]
    holder_name = holder["name"].strip()

    # Template first — everything depends on it; a missing blank must fail
    # BEFORE any side effects, with a message that says exactly what to do.
    from subsystems.coi.template import get_template
    template_bytes, meta = get_template()
    expiry_label = meta.get("expiry_label")

    lines = holder_lines(holder_name, holder.get("address") or "")
    stamped = stamp_certificate_holder(template_bytes, lines)

    identifier = coi_identifier(holder_name)
    filename = coi_filename(holder_name, expiry_label)
    output_path = Path(output_dir) / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(stamped)
    print(f"[{mode} {identifier}] COI PDF: {output_path}")

    writeback: dict = {
        "source": source_label,
        "identifier": identifier,
        "mode": mode,
        "filename": filename,
        "pdf_path": str(output_path),
        "holder_name": holder_name,
        "contact_email": contact["email"].strip(),
        "template_expiry_label": expiry_label,
    }

    if mode == "dry-run":
        try:
            from adapters.gcs import upload_preview_pdf, utc_timestamp
            writeback["preview_pdf_url"] = upload_preview_pdf(
                output_path, identifier=identifier, run_timestamp=utc_timestamp(),
            )
            print(f"[dry-run {identifier}] preview URL: {writeback['preview_pdf_url']}")
        except Exception as e:  # GCSNotConfigured or upload failure — non-fatal
            print(f"[dry-run {identifier}] preview upload skipped: "
                  f"{type(e).__name__}: {e}")
        return writeback

    if mode == "finalize":
        # 1) Drive: file into the browsable outbound record, COIs Sent/<year>/.
        #    Graceful — Drive down never blocks the Gmail draft. Idempotent:
        #    re-finalizing the same holder replaces the file in place.
        try:
            from adapters.drive import DriveNotConfigured, DriveUploader
            try:
                uploader = DriveUploader()
                folder = uploader.ensure_coi_folder(year=date.today().year)
                drive_file = uploader.upload_pdf_to_folder(
                    output_path, folder_id=folder["folder_id"], filename=filename,
                )
                writeback["drive_folder_path"] = folder["folder_path"]
                writeback["drive_pdf_url"] = drive_file.get("web_view_link")
                print(f"[finalize {identifier}] Drive: {folder['folder_path']}/{filename}")
            except DriveNotConfigured as e:
                writeback["drive_status"] = f"SKIPPED — {e}"
                print(f"[finalize {identifier}] {writeback['drive_status']}",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            writeback["drive_status"] = f"FAILED — {type(e).__name__}: {e}"
            print(f"[finalize {identifier}] Drive save FAILED: {e}", file=sys.stderr)

        # 2) Gmail draft in hello@ (NOT sent). Dedup by identifier: re-running
        #    the same holder updates the existing unsent draft in place.
        try:
            from adapters.gmail import HELLO_TOKEN_PATH, GmailNotConfigured, create_draft
            subject = f"Green Valley Contractors — COI — {holder_name}"
            try:
                draft = create_draft(
                    to=contact["email"].strip(),
                    subject=subject,
                    body=compose_email_body(holder_name, contact.get("name") or "",
                                            expiry_label),
                    attachment_path=output_path,
                    attachment_filename=filename,
                    from_addr=HELLO_FROM_ADDR,
                    invoice_identifier=identifier,  # generic dedup key
                    token_path=HELLO_TOKEN_PATH,
                )
                writeback["gmail_draft_id"] = draft["draft_id"]
                writeback["gmail_draft_url"] = draft["gmail_url"]
                print(f"[finalize {identifier}] hello@ draft: {draft['gmail_url']}")
            except GmailNotConfigured as e:
                writeback["gmail_status"] = f"SKIPPED — hello@ Gmail not configured: {e}"
                print(f"[finalize {identifier}] {writeback['gmail_status']}",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            writeback["gmail_status"] = f"FAILED — {type(e).__name__}: {e}"
            print(f"[finalize {identifier}] Gmail draft FAILED: {e}", file=sys.stderr)

        # 3) Slack notice. Channel comes ONLY from GVC_COI_SLACK_CHANNEL —
        #    until someone creates the channel and sets the env var this SKIPS
        #    cleanly (no post attempt, no degraded alert). Non-fatal always.
        try:
            from adapters.slack_notify import SlackNotConfigured, notify_coi_drafted
            try:
                notify_coi_drafted({
                    "holder_name": holder_name,
                    "contact_name": contact.get("name"),
                    "contact_email": contact["email"].strip(),
                    "expiry_pretty": pretty_expiry(expiry_label),
                })
                writeback["slack_notified"] = True
            except SlackNotConfigured as e:
                writeback["slack_notified"] = False
                writeback["slack_status"] = f"SKIPPED — {e}"
                print(f"[finalize {identifier}] Slack notice SKIPPED: {e}",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            writeback["slack_notified"] = False
            writeback["slack_error"] = f"{type(e).__name__}: {e}"
            print(f"[finalize {identifier}] Slack notice failed (non-fatal): {e}",
                  file=sys.stderr)

        # 4) Monday log — PLACEHOLDER (decided 2026-07-14): the destination board
        #    is undecided (GC Billing Profiles was considered and parked).
        #    log_coi skips cleanly until GVC_MONDAY_COI_BOARD_ID is set.
        try:
            from adapters.monday.coi import log_coi
            writeback.update(log_coi(
                holder_name=holder_name,
                contact_email=contact["email"].strip(),
                expiry_label=expiry_label,
                drive_url=writeback.get("drive_pdf_url"),
            ))
        except Exception as e:  # noqa: BLE001 — Monday must never block a COI
            writeback["monday_status"] = f"FAILED — {type(e).__name__}: {e}"
            print(f"[finalize {identifier}] Monday log failed (non-fatal): {e}",
                  file=sys.stderr)

        # 5) Ops alert when the finalize returned 200 but a step silently
        #    failed (parity with estimate/CO — the invisible-failure class).
        try:
            from adapters.slack_notify import notify_finalize_degraded
            failed: dict[str, str] = {}
            if str(writeback.get("gmail_status", "")).startswith("FAILED"):
                failed["Gmail draft"] = writeback["gmail_status"]
            if str(writeback.get("drive_status", "")).startswith("FAILED"):
                failed["Drive save"] = writeback["drive_status"]
            if writeback.get("slack_error"):
                failed["Slack notice"] = writeback["slack_error"]
            if str(writeback.get("monday_status", "")).startswith("FAILED"):
                failed["Monday log"] = writeback["monday_status"]
            notify_finalize_degraded("COI", identifier, failed)
        except Exception:  # noqa: BLE001 — alerting must never break the flow
            pass

        return writeback

    raise ValueError(f"Unknown mode: {mode!r} (expected 'dry-run' or 'finalize').")


# ---------------------------------------------------------------------------
# Bulk — "Annual COI List" (Google Sheet in, drafts out, YES/NO written back)
# ---------------------------------------------------------------------------

def _bulk_chunk_size() -> int:
    try:
        return max(1, int(os.environ.get("GVC_COI_BULK_CHUNK") or "15"))
    except ValueError:
        return 15


def _sheet_read_error(e: Exception) -> Exception:
    """Translate Google API errors into office-readable ValueErrors."""
    if type(e).__name__ == "HttpError":
        status = getattr(e, "status_code", None) or getattr(
            getattr(e, "resp", None), "status", None)
        if status in (403, 401):
            return ValueError(
                "The service account can't open that sheet. Share the "
                "spreadsheet with gvc-invoice-bot@gvc-invoice-system.iam."
                "gserviceaccount.com as Editor, then retry."
            )
        if status == 404:
            return ValueError("No spreadsheet found at that link — check the URL.")
    return e


def process_coi_bulk(
    sheet_url: str,
    output_dir: Path,
    *,
    mode: str,
    source_label: str = "<bulk>",
    after_row: int = 0,
    chunk: int | None = None,
) -> dict:
    """
    Bulk run over the Annual COI List sheet.

    mode "dry-run": read + parse the WHOLE sheet, return the review plan
    (counts + per-row states/reasons) plus a stamped sample preview of the
    first ready row. No writes anywhere.

    mode "finalize": process up to `chunk` ready rows with sheet row number
    > after_row — per row: stamp → Drive → hello@ draft → write YES (or NO
    on failure) into the Sent column. Row failures never stop the batch.
    Returns {results, next_after_row, remaining, ...}; the caller continues
    batch-by-batch until remaining == 0 (chunking keeps each HTTP request
    well inside the Cloud Run timeout, and YES-skip makes interrupted runs
    resumable). The call that finishes the batch ALSO: marks every invalid
    (skipped, missing-info) row NO in the Sent column so the ledger shows
    every unsent row (idempotent — already-NO cells untouched; returned as
    `invalid_marked`), returns `sheet_totals` from a fresh post-run read
    ({yes, no, invalid} — the accurate final count for the UI), and posts
    ONE Slack summary of that same fresh state. Rows already YES are never
    re-processed; rows marked NO retry on the next fresh run (row-cursor
    semantics), not within one run.
    """
    from adapters.sheets import SheetsClient, spreadsheet_id_from_url
    from subsystems.coi import bulk as coi_bulk

    spreadsheet_id = spreadsheet_id_from_url(sheet_url)
    if not spreadsheet_id:
        raise ValueError(
            "That doesn't look like a Google Sheets link. Paste the sheet's "
            "URL (docs.google.com/spreadsheets/d/…)."
        )

    sc = SheetsClient()
    try:
        tab, rows = sc.read_rows(spreadsheet_id)
    except Exception as e:  # noqa: BLE001 — translate Google errors
        raise _sheet_read_error(e)
    plan = coi_bulk.build_plan(rows)

    from subsystems.coi.template import get_template
    template_bytes, meta = get_template()
    expiry_label = meta.get("expiry_label")

    if mode == "dry-run":
        review: dict = {
            "source": source_label,
            "mode": mode,
            "sheet_tab": tab,
            "counts": plan["counts"],
            "entries": plan["entries"],
            "template_expiry_label": expiry_label,
        }
        first_ready = next((e for e in plan["entries"] if e["state"] == "ready"),
                           None)
        if first_ready:
            try:
                payload = coi_bulk.entry_to_coi_payload(first_ready)
                sample = process_coi(payload, output_dir, mode="dry-run",
                                     source_label=f"{source_label}:sample")
                review["sample_row_number"] = first_ready["row_number"]
                review["sample_preview_url"] = sample.get("preview_pdf_url")
                review["sample_filename"] = sample.get("filename")
            except Exception as e:  # noqa: BLE001 — sample is best-effort
                review["sample_error"] = f"{type(e).__name__}: {e}"
        return review

    if mode != "finalize":
        raise ValueError(f"Unknown mode: {mode!r} (expected 'dry-run' or 'finalize').")

    # ---- finalize: one chunk of ready rows past the cursor ----
    chunk = chunk or _bulk_chunk_size()
    status_col = plan["columns"]["status"]
    eligible = [e for e in plan["entries"]
                if e["state"] == "ready" and e["row_number"] > after_row]
    todo, rest = eligible[:chunk], eligible[chunk:]

    # Shared per-chunk resources: template already loaded; one Drive folder.
    uploader = None
    folder = None
    drive_setup_error = None
    try:
        from adapters.drive import DriveNotConfigured, DriveUploader
        try:
            uploader = DriveUploader()
            folder = uploader.ensure_coi_folder(year=date.today().year)
        except DriveNotConfigured as e:
            drive_setup_error = f"SKIPPED — {e}"
    except Exception as e:  # noqa: BLE001
        drive_setup_error = f"FAILED — {type(e).__name__}: {e}"

    results: list[dict] = []
    for entry in todo:
        row_res: dict = {"row_number": entry["row_number"], "name": entry["name"]}
        try:
            payload = coi_bulk.entry_to_coi_payload(entry)
            validate(payload)
            holder_name = payload["holder"]["name"]
            lines = holder_lines(holder_name, payload["holder"]["address"])
            stamped = stamp_certificate_holder(template_bytes, lines)
            identifier = coi_identifier(holder_name)
            filename = coi_filename(holder_name, expiry_label)
            output_path = Path(output_dir) / filename
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(stamped)

            if uploader is not None and folder is not None:
                try:
                    drive_file = uploader.upload_pdf_to_folder(
                        output_path, folder_id=folder["folder_id"],
                        filename=filename)
                    row_res["drive_pdf_url"] = drive_file.get("web_view_link")
                except Exception as e:  # noqa: BLE001 — Drive is non-fatal
                    row_res["drive_status"] = f"FAILED — {type(e).__name__}: {e}"
            elif drive_setup_error:
                row_res["drive_status"] = drive_setup_error

            # The Gmail draft is the point of the run — its failure fails the row.
            from adapters.gmail import HELLO_TOKEN_PATH, create_draft
            draft = create_draft(
                to=payload["contact"]["email"],
                subject=f"Green Valley Contractors — COI — {holder_name}",
                body=compose_email_body(holder_name,
                                        payload["contact"].get("name") or "",
                                        expiry_label),
                attachment_path=output_path,
                attachment_filename=filename,
                from_addr=HELLO_FROM_ADDR,
                invoice_identifier=identifier,
                token_path=HELLO_TOKEN_PATH,
            )
            row_res["gmail_draft_url"] = draft["gmail_url"]
            row_res["ok"] = True
        except Exception as e:  # noqa: BLE001 — a row failure never stops the batch
            row_res["ok"] = False
            row_res["error"] = f"{type(e).__name__}: {e}"
            print(f"[bulk row {entry['row_number']}] FAILED: {e}", file=sys.stderr)

        # Ledger writeback — YES/NO into the Sent column. A writeback failure
        # is loud (the sheet is the operator's ledger) but non-fatal.
        try:
            sc.write_cell(spreadsheet_id, tab_title=tab,
                          row_number=entry["row_number"], col_index=status_col,
                          value="YES" if row_res.get("ok") else "NO")
        except Exception as e:  # noqa: BLE001
            row_res["writeback_error"] = f"{type(e).__name__}: {e}"
            print(f"[bulk row {entry['row_number']}] Sent-column writeback "
                  f"FAILED: {e}", file=sys.stderr)

        results.append(row_res)

    out: dict = {
        "source": source_label,
        "mode": mode,
        "sheet_tab": tab,
        "processed": len(results),
        "results": results,
        "remaining": len(rest),
        "next_after_row": todo[-1]["row_number"] if todo else after_row,
        "template_expiry_label": expiry_label,
        "monday_status": "SKIPPED — placeholder (bulk logging lands with the "
                         "board decision)",
    }

    # Batch complete → close out the ledger + report the accurate totals.
    if not rest:
        # (a) Mark every invalid (skipped, missing-info) row NO so the sheet
        #     shows each unsent row (decided 2026-07-16 — was: cell untouched,
        #     which hid skipped rows from the counts). Idempotent: cells
        #     already NO are left alone. Never fatal.
        invalid_marked: list[dict] = []
        for entry in plan["entries"]:
            if entry["state"] != "invalid" or entry["status"] == "NO":
                continue
            marked = {"row_number": entry["row_number"], "name": entry["name"],
                      "reasons": entry["reasons"]}
            try:
                sc.write_cell(spreadsheet_id, tab_title=tab,
                              row_number=entry["row_number"],
                              col_index=status_col, value="NO")
            except Exception as e:  # noqa: BLE001
                marked["writeback_error"] = f"{type(e).__name__}: {e}"
                print(f"[bulk row {entry['row_number']}] invalid-row NO "
                      f"writeback FAILED: {e}", file=sys.stderr)
            invalid_marked.append(marked)
        out["invalid_marked"] = invalid_marked

        # (b) Fresh post-run read = the accurate final count (stateless truth
        #     across chunked calls). Failed attempts vs skipped-invalid are
        #     disjoint: NO + still-parseable = errored attempt; invalid =
        #     skipped for missing info (also NO after the sweep above).
        try:
            _, fresh_rows = sc.read_rows(spreadsheet_id)
            fresh = coi_bulk.build_plan(fresh_rows)
            yes = sum(1 for e in fresh["entries"] if e["status"] == "YES")
            failed_no_rows = [e["row_number"] for e in fresh["entries"]
                              if e["status"] == "NO" and e["state"] != "invalid"]
            invalid_rows = [e["row_number"] for e in fresh["entries"]
                            if e["state"] == "invalid"]
            out["sheet_totals"] = {"yes": yes, "no": len(failed_no_rows),
                                   "invalid": len(invalid_rows)}

            # (c) ONE Slack summary of that same fresh state.
            msg = coi_bulk.bulk_summary_message(
                out["sheet_totals"], expiry_label=expiry_label,
                failed_rows=failed_no_rows, invalid_rows=invalid_rows)
            channel = os.environ.get("GVC_COI_SLACK_CHANNEL")
            if channel:
                from adapters.slack_notify import post_message
                post_message(msg, channel=channel)
                out["slack_notified"] = True
            else:
                out["slack_status"] = "SKIPPED — GVC_COI_SLACK_CHANNEL not set."
        except Exception as e:  # noqa: BLE001 — summary is best-effort
            out["slack_notified"] = False
            out["slack_error"] = f"{type(e).__name__}: {e}"

    return out
