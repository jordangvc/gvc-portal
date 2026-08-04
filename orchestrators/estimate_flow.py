"""
GVC Estimate generator.
=========================================================================
Form/JSON in -> GVC-branded estimate PDF out -> Gmail draft in hello@.

Deliberately simple and SEPARATE from the invoice flow: NO Stripe, NO
QuickBooks object, NO signature/open tracking. It mirrors the invoice's
dry-run -> review -> finalize shape so the two systems share a UX.

modes:
  "dry-run"  : render the PDF only (preview). No email, no Slack.
  "finalize" : render + create a Gmail draft in hello@ (PDF attached) +
               post a #estimates Slack notice. The draft is NOT sent — a
               human reviews and clicks Send (same locked posture as the
               invoice flow's billing@ drafts).

The canonical JSON shape is documented in example_estimate.json.
"""
from __future__ import annotations

import os
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

# Reuse the invoice's branding/money/logo helpers so the estimate and invoice
# PDFs stay visually consistent. We do NOT modify invoice.py.
from shared.paths import REPO_ROOT as ROOT, TEMPLATES_DIR, OUTPUT_DIR
from shared.money import fmt_money
from subsystems.invoice.pdf import make_logo_data_uri
from subsystems.estimate.scope_catalog import (
    build_additional_services,
    default_catalog,
    group_scope_details,
)

ESTIMATE_VALIDITY_DAYS = 30
HELLO_FROM_ADDR = "hello@greenvalleycontractors.com"

# Standard company block on every estimate. Overridable per-estimate via the
# input JSON `company` object. NOTE: GVC_COMPANY_PHONE should be set to the
# standard Quo company number (confirm with Jordan — defaulting to the documented
# main line for now).
DEFAULT_COMPANY = {
    "name": "Green Valley Contractors, Inc.",
    "address": "5121 Highland Center Rd\nBrookville, IN 47012",
    "phone": os.environ.get("GVC_COMPANY_PHONE", "(513) 912-2235"),
    "email": "hello@greenvalleycontractors.com",
    "website": "www.greenvalleycontractors.com",
    "tagline": "Drywall Experts Since 1987",
}

# Standard estimate terms. Transcribed VERBATIM from the canonical GVC template
# (GVC Estimate v01), with these deliberate edits — Jordan/Andrea to bless:
#   - "Table 1" -> "the pricing table above" (our doc has no 'Table 1' label).
#   - "2024 Standard Rate Sheet" -> "current Standard Rate Sheet" (de-stale).
#   - WARRANTY: dropped the unfilled "(list specific types of defects…)" template
#     placeholder; kept the substance.
#   - The template's "Please sign below to acknowledge…" signature line is
#     intentionally OMITTED (no signature tracking, per scope). Add a printed
#     acceptance line later if desired.
# Edit here in ONE place; it renders on every estimate. Per-job variances go in
# the editable "Special Notes" section, which supersedes these defaults.
STANDARD_BOILERPLATE = {
    "inclusions": [
        "Services outlined in the pricing table above.",
        "GVC's estimate is based on a carefully developed schedule that includes: "
        "submittal and approval of equipment and materials; review and approval of "
        "the scope of work; logical sequencing of work; procurement activities; "
        "resource leveling; manpower allocation; and project duration.",
        "Coordination with project personnel to refine project tasks and ensure "
        "effective communication throughout the project.",
    ],
    "exclusions": [
        "Any items or services not specifically detailed in this proposal.",
        "Shipping costs (if applicable).",
    ],
    "clarifications": [
        "This proposal and the associated scope of work are based on the design "
        "provided by GVC, relying primarily on verbal directions from the owner and "
        "minimum code standards as interpreted by GVC.",
        "Utilities: The Owner/Builder will be responsible for providing necessary "
        "utilities, including electricity, water, and ventilation/heat, to facilitate "
        "the completion of the work. The workspace temperature must be maintained at a "
        "minimum of 55°F during colder months.",
        "Fasteners: All fasteners used will comply with relevant code requirements, "
        "utilizing nails or screws at seams and edges, and screws in the field as "
        "necessary.",
        "Drywall Installation: 1/2\" standard drywall will be installed throughout the "
        "project area, with mold-resistant drywall used in designated areas, excluding "
        "wet areas set for tile installation.",
        "Cleanup: Upon completion of the installation, GVC will remove and properly "
        "dispose of any leftover drywall scraps from the site.",
        "Finishing: The drywall finishing process will achieve a Level 5 finish, which "
        "includes: tape coating, bedding, a third coat, rolling skim coat, and sanding.",
        "Ceiling Finish: The ceiling finish will be determined by the builder/owner "
        "prior to the commencement of the project. Available finish options include: "
        "smooth, crows-foot texture, and knockdown crows-foot texture.",
        "Post-Installation Cleanup: Once sanding is complete, floors will be scraped "
        "and broom-swept. Empty containers used for finishing will be discarded in the "
        "site's dumpster or off-site if no dumpster is available.",
        "Schedule Delays: Delays caused by factors outside GVC's control (e.g., delays "
        "\"caused by others\") will not be considered in this proposal. Any necessary "
        "change orders will be issued by GVC.",
        "Cost Considerations: No premium or overtime costs have been included in this "
        "proposal and are not applicable for the tasks listed. All change orders and "
        "extra work orders will be based on our current Standard Rate Sheet.",
        "This proposal assumes timely and appropriate access to the owner's assets. "
        "Delays due to inclement weather have not been factored into the proposed costs.",
        "Our working schedule will be Monday to Friday, 8 hours per day (typically "
        "9 AM – 5 PM), excluding holidays, rest days, special holidays, and weekends.",
    ],
    "payment": "An invoice will be sent upon project completion, with payment due upon receipt.",
    "warranty": "The Contractor warrants that all work will be completed to the highest "
                "standards under industry best practices. This warranty covers any defects "
                "in workmanship for a period of six (6) months from the date of completion. "
                "It does not cover damage caused by normal wear and tear or intentional acts.",
    "insurance": "The Contractor maintains general liability insurance coverage for the "
                 "duration of the project. A copy of the insurance policy will be provided "
                 "to the Client upon request.",
}


# ---------------------------------------------------------------------------
# Validation + enrichment
# ---------------------------------------------------------------------------

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def validate(data: dict) -> None:
    _require(isinstance(data, dict), "Estimate payload must be an object.")
    client = data.get("client") or {}
    job = data.get("job") or {}
    est = data.get("estimate") or {}

    _require(bool(client.get("name")), "client.name is required.")
    _require(bool(client.get("email")), "client.email is required (used for the Gmail draft).")
    _require(bool(job.get("name")), "job.name (project name) is required.")
    # estimate.identifier is OPTIONAL — when absent the service assigns the
    # next YYYY-MMDD-NNN (see estimate_number.py). A supplied value must be
    # well-formed (manual numbers were retired 2026-06-10).
    ident = (est.get("identifier") or "").strip()
    if ident:
        from subsystems.estimate.number import ESTIMATE_NUMBER_RE
        _require(
            bool(ESTIMATE_NUMBER_RE.match(ident)),
            f"estimate.identifier {ident!r} is not YYYY-MMDD-NNN "
            "(leave it blank to auto-assign).",
        )

    items = est.get("line_items") or []
    _require(len(items) > 0, "At least one line item is required.")
    for i, li in enumerate(items, 1):
        _require(bool((li or {}).get("description")), f"Line item {i}: description is required.")
        _require(
            isinstance(li.get("unit_price"), (int, float)),
            f"Line item {i}: a numeric unit_price is required.",
        )


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _pretty_date(d: date) -> str:
    # %-d isn't portable to Windows, but the renderer runs on macOS/Linux.
    return d.strftime("%B %-d, %Y")


def enrich(data: dict, catalog: Optional[dict] = None) -> dict:
    d = deepcopy(data)

    company = {**DEFAULT_COMPANY, **(d.get("company") or {})}
    prepared_by = d.get("prepared_by") or {}
    client = d.get("client") or {}
    job = d.get("job") or {}
    est = d.get("estimate") or {}

    issue = _parse_date(est.get("date")) or date.today()
    expiry = _parse_date(est.get("expiry_date")) or (issue + timedelta(days=ESTIMATE_VALIDITY_DAYS))

    main_items: list[dict] = []
    optional_items: list[dict] = []
    main_total = 0.0
    options_total = 0.0

    for li in est.get("line_items") or []:
        qty = li.get("quantity")
        qty = 1 if qty in (None, "") else float(qty)
        unit = float(li["unit_price"])
        line_total = round(unit * qty, 2)
        row = {
            "description": li.get("description", ""),
            "detail": li.get("detail") or "",
            "quantity": int(qty) if float(qty).is_integer() else qty,
            "unit_price_pretty": fmt_money(unit),
            "line_total": line_total,
            "line_total_pretty": fmt_money(line_total),
            # Scope-selection provenance — present only on lines added from the
            # Scope Selection section. Drives the Scope Details page; harmless
            # (empty) on plain manual line items.
            "scope_trade": (li.get("scope_trade") or "").strip(),
            "scope_title": (li.get("scope_title") or "").strip(),
            "scope_detail": (li.get("scope_detail") or "").strip(),
        }
        if li.get("optional"):
            optional_items.append(row)
            options_total += line_total
        else:
            main_items.append(row)
            main_total += line_total

    main_total = round(main_total, 2)
    options_total = round(options_total, 2)
    total_with_options = round(main_total + options_total, 2)

    # Special notes — accept a list, or a newline/^- delimited string.
    raw_notes = est.get("special_notes")
    if isinstance(raw_notes, str):
        special_notes = [
            ln.strip().lstrip("-•").strip()
            for ln in raw_notes.splitlines()
            if ln.strip()
        ]
    else:
        special_notes = [str(n).strip() for n in (raw_notes or []) if str(n).strip()]

    # Scope Details page (3-level hierarchy: trade -> selected scope ->
    # descriptor bullets). Built from ALL line items in input order, so a
    # scope tagged "optional" still gets its scope block.
    scope_details = group_scope_details(est.get("line_items") or [])

    enriched_est = {
        "identifier": est["identifier"],
        "date": issue.isoformat(),
        "date_pretty": _pretty_date(issue),
        "expiry_date": expiry.isoformat(),
        "expiry_pretty": _pretty_date(expiry),
        "scope_summary": job.get("scope_summary") or est.get("scope_summary") or "",
        "main_items": main_items,
        "optional_items": optional_items,
        "has_optional": bool(optional_items),
        "main_total": main_total,
        "main_total_pretty": fmt_money(main_total),
        "options_total": options_total,
        "options_total_pretty": fmt_money(options_total),
        "total_with_options": total_with_options,
        "total_with_options_pretty": fmt_money(total_with_options),
        "special_notes": special_notes,
        "notes": est.get("notes") or "",
        "scope_details": scope_details,
        "has_scope_details": bool(scope_details),
    }

    # Additional Services page — the full GVC offerings menu, from the catalog.
    cat = catalog or default_catalog()
    additional_services = build_additional_services(cat)

    return {
        "company": company,
        "prepared_by": prepared_by,
        "client": client,
        "job": job,
        "estimate": enriched_est,
        "boilerplate": STANDARD_BOILERPLATE,
        "additional_services": additional_services,
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_estimate_pdf(enriched: dict, output_path: Path) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("estimate.html.j2")
    html_str = tmpl.render(
        company=enriched["company"],
        prepared_by=enriched["prepared_by"],
        client=enriched["client"],
        job=enriched["job"],
        estimate=enriched["estimate"],
        boilerplate=enriched["boilerplate"],
        additional_services=enriched.get("additional_services") or [],
        logo_data_uri=make_logo_data_uri(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(ROOT)).write_pdf(str(output_path))
    return output_path


def _compose_email_body(enriched: dict, *, revised: bool = False) -> str:
    est = enriched["estimate"]
    client = enriched["client"]
    job = enriched["job"]
    prepared_by = enriched.get("prepared_by") or {}

    greeting_name = (client.get("contact_name") or client.get("name") or "there").split()[0]
    closing_name = prepared_by.get("name") or "Green Valley Contractors"

    # Follow-up copy is tied to the selected project type (decided 2026-07-22).
    # Commercial estimates use a softer, relationship-oriented follow-up ask;
    # residential keeps the original "let us know if you'd like to proceed" line.
    is_commercial = (job.get("project_type") or "").strip().lower().startswith("commercial")
    if is_commercial:
        follow_up = (
            "Please review the attached scope and pricing. Please let us know when you "
            "feel it would be appropriate for us to follow up regarding feedback on the "
            "job. We want to make sure we are staying in touch without becoming a nuisance."
        )
    else:
        follow_up = (
            "Please review the attached scope and pricing and let us know if you'd like "
            "to proceed or have any questions."
        )

    if revised:
        intro = (
            f"Thank you for the opportunity to bid {job.get('name', 'your project')}. "
            f"Attached is our revised estimate ({est['identifier']}), reflecting the "
            f"requested changes and valid through {est['expiry_pretty']}. "
            f"It supersedes the previous version."
        )
    else:
        intro = (
            f"Thank you for the opportunity to bid {job.get('name', 'your project')}. "
            f"Attached is our estimate ({est['identifier']}), valid through {est['expiry_pretty']}."
        )

    lines = [
        f"{greeting_name},",
        "",
        intro,
        "",
        f"The estimate total is {est['main_total_pretty']}"
        + (f", with optional add-ons bringing it to {est['total_with_options_pretty']} if selected."
           if est.get("optional_items") else "."),
        "",
        follow_up,
        "",
        "Thanks,",
        closing_name,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def process_estimate(
    data: dict,
    output_dir: Path,
    *,
    mode: str,
    source_label: str = "<dict>",
    revise: bool = False,
) -> dict:
    """
    Run the estimate flow. mode is "dry-run" or "finalize".
    Returns a writeback dict. Raises ValueError on validation problems.

    revise=True — "Update this Estimate" (decided 2026-07-02): the OUTBOUND
    number is unchanged (estimate.identifier is REQUIRED and reused); the
    superseded PDF + sidecar in Drive are archived under an `e{n}-` prefix
    before the new versions land under the canonical names; the Monday
    column sync switches to overwrite; Slack/Gmail use revision wording.
    """
    validate(data)
    if revise and not ((data.get("estimate") or {}).get("identifier") or "").strip():
        raise ValueError(
            "Revision requires estimate.identifier — the original estimate "
            "number that stays on the updated document."
        )

    # Assign the estimate number when the caller didn't supply one.
    # dry-run shows the PROPOSED next number (not reserved); finalize
    # re-resolves at that moment so the number reflects reality even if
    # other estimates finalized since the preview.
    if not (data.get("estimate") or {}).get("identifier"):
        from subsystems.estimate.number import next_estimate_number
        data = deepcopy(data)
        data.setdefault("estimate", {})["identifier"] = next_estimate_number(
            output_dir=Path(output_dir)
        )

    # Load the standard-scope catalog for the Additional Services page. Never
    # blocks: load_catalog() falls back to the shipped default if the store is
    # unconfigured/missing, and we swallow anything unexpected.
    try:
        from subsystems.estimate.scope_catalog import load_catalog
        catalog = load_catalog()
    except Exception:  # noqa: BLE001
        catalog = None

    enriched = enrich(data, catalog=catalog)
    est = enriched["estimate"]
    identifier = est["identifier"]
    output_path = Path(output_dir) / f"{identifier}.pdf"

    render_estimate_pdf(enriched, output_path)
    print(f"[{mode} {identifier}] estimate PDF: {output_path}")

    writeback: dict = {
        "source": source_label,
        "identifier": identifier,
        "mode": mode,
        "pdf_path": str(output_path),
        "main_total": est["main_total"],
        "total_with_options": est["total_with_options"],
        "expiry_date": est["expiry_date"],
    }

    if mode == "dry-run":
        # Upload a preview to GCS when configured (same path as invoices), so
        # the form can show a signed URL in browsers without a server-disk hop.
        try:
            from adapters.gcs import GCSNotConfigured, upload_preview_pdf, utc_timestamp
            writeback["preview_pdf_url"] = upload_preview_pdf(
                output_path, identifier=identifier, run_timestamp=utc_timestamp(),
            )
            print(f"[dry-run {identifier}] preview URL: {writeback['preview_pdf_url']}")
        except Exception as e:  # GCSNotConfigured or upload failure — non-fatal
            print(f"[dry-run {identifier}] preview upload skipped: {type(e).__name__}: {e}")
        return writeback

    if mode == "finalize":
        # 0) Save the PDF to the project's Drive folder (find-or-create the
        #    Projects/<year>/<type>/<client>/<project>/Estimate/ chain).
        #    Graceful: Drive being down never blocks the Gmail draft.
        #
        #    Version-safety invariant (2026-07-02): a previously SENT PDF is
        #    never overwritten. If the canonical filename already exists in
        #    the folder (revision — or a re-run under the same number), it is
        #    archived first by renaming to `e{n}-<name>` (file ID and links
        #    preserved), then the new PDF lands under the canonical name.
        #    The as-sent JSON sidecar gets the same treatment, so every
        #    archived version pairs a PDF with the exact data that built it.
        revision_version: int | None = None  # v2 = first revision; for Slack
        try:
            from adapters.drive import DriveNotConfigured, DriveUploader
            from subsystems.estimate.revision import (
                estimate_pdf_filename, next_archive_name, sidecar_filename,
            )
            job = enriched["job"]
            client_d = enriched["client"]
            issue_year = int(est["date"][:4])
            location = (job.get("street_address") or job.get("location")
                        or job.get("name") or "").strip()
            project_label = f"{location} | {client_d.get('name','')}".strip(" |")
            pdf_name = estimate_pdf_filename(identifier, project_label)
            sidecar_name = sidecar_filename(identifier)
            try:
                uploader = DriveUploader()
                folder = uploader.ensure_estimate_folder(
                    customer=client_d.get("name", "Unknown Client"),
                    project_label=project_label,
                    project_type=job.get("project_type") or "residential",
                    year=issue_year,
                )

                # -- Archive the superseded version (never destroy a sent doc) --
                try:
                    existing_pdf = uploader.find_child_file(folder["folder_id"], pdf_name)
                    if existing_pdf:
                        names = uploader.list_child_names(folder["folder_id"])
                        pdf_archive = next_archive_name(names, pdf_name)
                        uploader.rename_file(existing_pdf["id"], pdf_archive)
                        archived = [pdf_archive]
                        # Same e{n}- prefix for the sidecar so versions pair up.
                        prefix = pdf_archive[: -len(pdf_name)]
                        existing_sc = uploader.find_child_file(
                            folder["folder_id"], sidecar_name)
                        if existing_sc:
                            uploader.rename_file(existing_sc["id"], prefix + sidecar_name)
                            archived.append(prefix + sidecar_name)
                        writeback["drive_archived"] = archived
                        from subsystems.estimate.revision import archive_version
                        n = archive_version(pdf_archive) or 0
                        revision_version = n + 1
                        print(f"[finalize {identifier}] Drive: archived prior "
                              f"version as {', '.join(archived)}")
                except Exception as e:  # noqa: BLE001 — archive is best-effort;
                    # the upload below replaces in place if archiving failed.
                    writeback["drive_archive_error"] = f"{type(e).__name__}: {e}"
                    print(f"[finalize {identifier}] Drive archive failed "
                          f"(non-fatal): {e}", file=sys.stderr)

                drive_file = uploader.upload_pdf_to_folder(
                    output_path,
                    folder_id=folder["folder_id"],
                    filename=pdf_name,
                )
                writeback["drive_folder_path"] = folder["folder_path"]
                writeback["drive_pdf_url"] = drive_file.get("web_view_link")
                print(f"[finalize {identifier}] Drive: {folder['folder_path']}/"
                      f"{drive_file['filename']}")

                # -- As-sent JSON sidecar (enables full-field revision prefill) --
                try:
                    import json as _json
                    sc = uploader.upload_or_replace_file(
                        folder_id=folder["folder_id"],
                        filename=sidecar_name,
                        data=_json.dumps(data, indent=2, default=str).encode("utf-8"),
                        mimetype="application/json",
                    )
                    writeback["drive_sidecar_file_id"] = sc.get("file_id")
                except Exception as e:  # noqa: BLE001 — sidecar is non-fatal
                    writeback["drive_sidecar_error"] = f"{type(e).__name__}: {e}"
                    print(f"[finalize {identifier}] sidecar save failed "
                          f"(non-fatal): {e}", file=sys.stderr)
            except DriveNotConfigured as e:
                writeback["drive_status"] = f"SKIPPED — {e}"
                print(f"[finalize {identifier}] {writeback['drive_status']}",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            writeback["drive_status"] = f"FAILED — {type(e).__name__}: {e}"
            print(f"[finalize {identifier}] Drive save FAILED: {e}", file=sys.stderr)

        # 0b) ALSO drop the PDF at the root of Jake's numbered plan folder when
        #     Plan Folder # is known. Soft-fail: missing/ambiguous/no_access
        #     never blocks Gmail / Monday / the Projects/.../Estimate/ path.
        plan_no = ((enriched.get("job") or {}).get("plan_folder_number") or "").strip()
        if plan_no:
            try:
                from adapters.drive import DriveNotConfigured, DriveUploader
                from adapters.monday.estimate import JAKE_PLAN_FOLDER_ROOT
                from subsystems.estimate.plan_folder import (
                    upload_pdf_to_jake_plan_folder,
                )
                from subsystems.estimate.revision import estimate_pdf_filename as _est_pdf_name
                _job = enriched.get("job") or {}
                _client = enriched.get("client") or {}
                _loc = (_job.get("street_address") or _job.get("location")
                        or _job.get("name") or "").strip()
                _label = f"{_loc} | {_client.get('name', '')}".strip(" |")
                _pf_name = _est_pdf_name(identifier, _label)
                try:
                    _pf_up = DriveUploader()
                    pf_report = upload_pdf_to_jake_plan_folder(
                        _pf_up,
                        pdf_path=output_path,
                        filename=_pf_name,
                        plan_number=plan_no,
                        root_id=JAKE_PLAN_FOLDER_ROOT,
                    )
                    writeback.update(pf_report)
                    if pf_report.get("plan_folder_ok"):
                        print(
                            f"[finalize {identifier}] Plan folder: "
                            f"{pf_report.get('plan_folder_name')}/"
                            f"{pf_report.get('plan_folder_filename')}"
                        )
                    else:
                        print(
                            f"[finalize {identifier}] Plan folder "
                            f"{pf_report.get('plan_folder_status')}",
                            file=sys.stderr,
                        )
                except DriveNotConfigured as e:
                    writeback["plan_folder_ok"] = False
                    writeback["plan_folder_reason"] = "no_access"
                    writeback["plan_folder_status"] = f"SKIPPED — {e}"
                    print(
                        f"[finalize {identifier}] {writeback['plan_folder_status']}",
                        file=sys.stderr,
                    )
            except Exception as e:  # noqa: BLE001 — never block finalize
                writeback["plan_folder_ok"] = False
                writeback["plan_folder_reason"] = "error"
                writeback["plan_folder_status"] = f"FAILED — {type(e).__name__}: {e}"
                print(
                    f"[finalize {identifier}] Plan folder upload FAILED: {e}",
                    file=sys.stderr,
                )

        # 1) Gmail draft in hello@ (NOT sent). Graceful if hello@ token absent.
        try:
            from adapters.gmail import HELLO_TOKEN_PATH, GmailNotConfigured, create_draft
            client = enriched["client"]
            subject = f"Green Valley Contractors — Estimate {identifier} — {enriched['job'].get('name','')}".strip(" —")
            filename = f"{identifier} - {enriched['job'].get('name','Estimate')}.pdf"
            # CC the salesperson (the estimate's prepared_by) on the hello@ draft so
            # the sales→office handoff is visible: when the office reviews and Sends,
            # the salesperson is looped in automatically. Skip if there's no
            # salesperson email or it's the same as the client recipient.
            pb_email = ((enriched.get("prepared_by") or {}).get("email") or "").strip()
            cc_salesperson = pb_email if (pb_email and pb_email.lower() != (client.get("email") or "").lower()) else None
            if cc_salesperson:
                writeback["cc_salesperson"] = cc_salesperson
            try:
                draft = create_draft(
                    to=client["email"],
                    subject=subject,
                    body=_compose_email_body(enriched, revised=revise),
                    attachment_path=output_path,
                    attachment_filename=filename,
                    from_addr=HELLO_FROM_ADDR,
                    cc=cc_salesperson,
                    invoice_identifier=identifier,  # generic dedup-by-subject key
                    token_path=HELLO_TOKEN_PATH,
                )
                writeback["gmail_draft_id"] = draft["draft_id"]
                writeback["gmail_draft_url"] = draft["gmail_url"]
                print(f"[finalize {identifier}] hello@ draft: {draft['gmail_url']}")
            except GmailNotConfigured as e:
                writeback["gmail_status"] = (
                    f"SKIPPED — hello@ Gmail not configured: {e} "
                    "(run `python gmail.py setup-hello`)."
                )
                print(f"[finalize {identifier}] {writeback['gmail_status']}", file=sys.stderr)
        except Exception as e:
            writeback["gmail_status"] = f"FAILED — {type(e).__name__}: {e}"
            print(f"[finalize {identifier}] Gmail draft FAILED: {e}", file=sys.stderr)

        # 2) Slack notice (channel via GVC_ESTIMATES_SLACK_CHANNEL). Non-fatal:
        #    a Slack outage / rate-limit must never block the finalize. The
        #    outcome is recorded in the writeback (slack_notified +
        #    slack_status/slack_error) so the caller can surface it to the user
        #    and emit a structured `estimate.slack` activity event. Distinguish
        #    "skipped" (not configured) from a real "failed" post.
        try:
            from adapters.slack_notify import SlackNotConfigured, notify_estimate_drafted
            try:
                notify_estimate_drafted(enriched, revised=revise,
                                        version=revision_version)
                writeback["slack_notified"] = True
            except SlackNotConfigured as e:
                writeback["slack_notified"] = False
                writeback["slack_status"] = f"SKIPPED — {e}"
                print(f"[finalize {identifier}] Slack notice SKIPPED — not configured: {e}",
                      file=sys.stderr)
        except Exception as e:
            writeback["slack_notified"] = False
            writeback["slack_error"] = f"{type(e).__name__}: {e}"
            print(f"[finalize {identifier}] Slack notice failed (non-fatal): {e}", file=sys.stderr)

        # 3) Monday Bid Board write-back: find-or-create the item,
        #    fill-if-empty backfill, Stage -> "Sent to Client", attach PDF.
        #    write_back() never raises (see monday_estimate.py).
        from adapters.monday.estimate import write_back as monday_write_back
        writeback.update(
            monday_write_back(enriched, pdf_path=output_path,
                              estimate_number=identifier, revise=revise)
        )
        if revise:
            writeback["revised"] = True
            if revision_version:
                writeback["revision_version"] = revision_version

        # 4) Ops alert if a finalize step silently failed. Finalize returns 200
        #    even when a downstream step (Gmail draft / Drive save / the Slack
        #    notice) fails, so without this an outage stays invisible — exactly
        #    how the #bids (then "#leads") notice once went dark. Best-effort; never raises.
        try:
            from adapters.slack_notify import notify_finalize_degraded
            failed: dict[str, str] = {}
            if str(writeback.get("gmail_status", "")).startswith("FAILED"):
                failed["Gmail draft"] = writeback["gmail_status"]
            if str(writeback.get("drive_status", "")).startswith("FAILED"):
                failed["Drive save"] = writeback["drive_status"]
            if writeback.get("slack_error"):
                failed["Slack notice"] = writeback["slack_error"]
            notify_finalize_degraded("Estimate", identifier, failed)
        except Exception:  # noqa: BLE001 — alerting must never break the flow
            pass

        return writeback

    raise ValueError(f"Unknown mode: {mode!r} (expected 'dry-run' or 'finalize').")
