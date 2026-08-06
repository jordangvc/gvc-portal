"""
GVC Change Order program — standalone create + revision flow.
=========================================================================
Mirrors the estimate flow (estimate_flow.py): form/JSON in -> GVC-branded CO
PDF out -> hello@ Gmail draft + #change-orders Slack + a Monday CO item under
the parent $Project. NO Stripe (a CO is billed later through the invoice
system as its own invoice `CO.{n}-{estimate#}`).

Locked decisions (original design):
  - The estimate number is the spine. CO id = `CO.{n}-{estimate#}`,
    service-assigned (co_number.py), n increments per job.
  - Prefer pulling the linked job from the Monday Project item (single source
    of truth); a pasted Drive folder URL is the backup link + the filing
    destination for the CO PDF.
  - NEW MODEL (2026-07-16): a CO is a TOP-LEVEL item on the Projects board
    (`CO.{n} - {parent title}`, in the parent's group) + a task on the
    Operations board. Subitems are retired for new COs (Jordan rejected
    them) — see adapters/monday/co.py.
  - Revision (2026-07-16, mirrors estimate revision): the CO NUMBER never
    changes. The prior PDF + sidecar are archived in Drive with an `e{n}-`
    prefix; the Monday CO item + Ops task are updated IN PLACE (CO Status
    resets to Drafted); a fresh hello@ draft (dedup by co_number updates the
    unsent draft) + #change-orders notice go out with revision wording.
    Revising a Billed CO is WARN + ALLOW — the invoice is never touched.

modes:
  "dry-run"  : render the CO PDF only (preview). No email, Slack, Drive, Monday.
  "finalize" : render + file the PDF to the job's Drive folder (Change Orders/
               subfolder) + hello@ Gmail draft (PDF attached, NOT sent) +
               #change-orders Slack notice + create/update the Monday CO item
               + Operations task.

Canonical JSON shape: see example_change_order.json. `_link` may carry
`monday_item_id` and/or `gfolder_url`/`drive_folder_url` (set by the form's
Find-the-Project lookup/search).
"""
from __future__ import annotations

import sys
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from subsystems.change_order.document import render_co_pdf
from subsystems.change_order.number import next_co_number, parse_co_number
from subsystems.change_order.revision import (
    archive_version, co_pdf_filename, next_archive_name, sidecar_filename,
)
from shared.doc_number import core_number
from shared.money import fmt_money

HELLO_FROM_ADDR = "hello@greenvalleycontractors.com"
CO_SUBFOLDER_NAME = "Change Orders"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def validate(data: dict) -> None:
    _require(isinstance(data, dict), "Change order payload must be an object.")
    client = data.get("client") or {}
    job = data.get("job") or {}
    co = data.get("change_order") or {}

    _require(bool(client.get("name")), "client.name is required.")
    _require(bool(client.get("email")), "client.email is required (the CO draft is sent here).")
    _require(bool(job.get("name")), "job.name (project name) is required.")
    _require(bool((co.get("base_number") or "").strip()),
             "change_order.base_number is required (the linked estimate / project #).")
    _require(bool((co.get("title") or "").strip()), "change_order.title is required.")

    breakdown = co.get("breakdown") or []
    _require(len(breakdown) > 0, "At least one breakdown line is required.")
    for i, row in enumerate(breakdown, 1):
        _require(bool((row or {}).get("label")), f"Breakdown line {i}: a label is required.")
        _require(isinstance((row or {}).get("amount"), (int, float)),
                 f"Breakdown line {i}: a numeric amount is required.")

    # If the caller pinned a CO number, it must be well-formed.
    supplied = (co.get("co_number") or "").strip()
    if supplied:
        _require(parse_co_number(supplied) is not None,
                 f"change_order.co_number {supplied!r} is not CO.{{n}}-{{base}} "
                 "(leave it blank to auto-assign).")


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------

def normalize_base_number(base: str) -> str:
    """
    The base is the estimate / project spine core — it must NOT itself be a
    CO id. Strip `CO.{n}-` wrappers and EST-/PRO-/INV- prefixes so we emit
    `CO.{n}-YYYY-MMDD-NNN` (never `CO.1-EST-…` or `CO.1-CO.3-…`).
    """
    base = (base or "").strip()
    while True:
        parsed = parse_co_number(base)
        if not parsed:
            break
        base = parsed[1]
    core = core_number(base)
    return core or base


def _parse_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _pretty_date(d: date) -> str:
    return d.strftime("%B %-d, %Y")


def _gather_existing_identifiers(data: dict, *, refresh: bool) -> list[str]:
    """
    Existing CO identifiers for this job, used to compute the next n.
    Always includes anything the lookup already attached
    (change_order.existing_co_identifiers). On finalize (refresh=True) we also
    re-read the parent's Monday CO items (new-model items + legacy subitems)
    and the Drive folder's file names, so a CO created since the preview
    doesn't collide. All best-effort.
    """
    co = data.get("change_order") or {}
    link = data.get("_link") or {}
    existing = list(co.get("existing_co_identifiers") or [])

    if not refresh:
        return existing

    item_id = link.get("monday_item_id")
    if item_id:
        try:
            from adapters.monday.client import MondayClient
            from adapters.monday import co as monday_co
            ctx = monday_co.get_project_context(MondayClient(), int(item_id))
            existing.extend(ctx.get("existing_co_identifiers") or [])
        except Exception as e:  # noqa: BLE001 — non-fatal; numbering falls back to local
            print(f"[change-order] Monday CO re-read skipped: {type(e).__name__}: {e}",
                  file=sys.stderr)

    folder_url = link.get("drive_folder_url") or link.get("gfolder_url")
    if folder_url:
        try:
            from adapters.drive import DriveUploader, folder_id_from_url
            fid = folder_id_from_url(folder_url)
            if fid:
                up = DriveUploader()
                # Look inside the Change Orders/ subfolder if present, else the folder.
                sub = up._find_child(fid, CO_SUBFOLDER_NAME, is_folder=True)
                scan_id = sub["id"] if sub else fid
                for name in up.list_child_names(scan_id):
                    existing.append(Path(name).stem)
        except Exception as e:  # noqa: BLE001 — non-fatal
            print(f"[change-order] Drive scan skipped: {type(e).__name__}: {e}",
                  file=sys.stderr)

    return existing


def assign_co_number(data: dict, *, refresh: bool) -> str:
    co = data.get("change_order") or {}
    supplied = (co.get("co_number") or "").strip()
    if supplied:
        return supplied
    base = normalize_base_number(co["base_number"])
    return next_co_number(base, _gather_existing_identifiers(data, refresh=refresh))


# ---------------------------------------------------------------------------
# Enrich + render
# ---------------------------------------------------------------------------

def _co_total(co: dict) -> float:
    if co.get("total") is not None:
        return round(float(co["total"]), 2)
    return round(sum(float(r["amount"]) for r in co.get("breakdown") or []), 2)


def _build_co_payload(data: dict, co_number: str, issue: date) -> dict:
    co = data.get("change_order") or {}
    payload = {
        "co_number": co_number,
        "base_number": normalize_base_number(co.get("base_number") or ""),
        "issue_date_pretty": _pretty_date(issue),
        "title": co.get("title") or "",
        "description": co.get("description") or "",
        "breakdown": [
            {"label": r.get("label", ""), "amount": float(r["amount"])}
            for r in co.get("breakdown") or []
        ],
        "approval_note": co.get("approval_note") or "",
        "appendix_images": co.get("appendix_images") or [],
    }
    if co.get("total") is not None:
        payload["total"] = float(co["total"])
    return payload


def _compose_email_body(data: dict, co_number: str, total_pretty: str,
                        *, revised: bool = False) -> str:
    client = data.get("client") or {}
    job = data.get("job") or {}
    co = data.get("change_order") or {}
    prepared_by = data.get("prepared_by") or {}

    greeting_name = (client.get("contact_name") or client.get("name") or "there").split()[0]
    closing_name = prepared_by.get("name") or "Green Valley Contractors"

    if revised:
        intro = (
            f"Please find attached the REVISED Change Order {co_number} for "
            f"{job.get('name', 'your project')}"
            + (f" — {co.get('title')}." if co.get("title") else ".")
            + " This supersedes the previous version (same change order number)."
        )
    else:
        intro = (
            f"Please find attached Change Order {co_number} for {job.get('name', 'your project')}"
            + (f" — {co.get('title')}." if co.get("title") else ".")
        )

    lines = [
        f"{greeting_name},",
        "",
        intro,
        "",
        f"The total for this change order is {total_pretty}.",
        "",
        "Please review the attached change order and reply to approve so we can "
        "schedule the work. Let us know if you have any questions.",
        "",
        "Thanks,",
        closing_name,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def process_change_order(
    data: dict,
    output_dir: Path,
    *,
    mode: str,
    source_label: str = "<dict>",
    revise: bool = False,
) -> dict:
    """
    Run the CO flow. mode is "dry-run" or "finalize". Returns a writeback dict.

    revise=True — "Update this Change Order" (mirrors the estimate revision):
    the CO NUMBER is unchanged (change_order.co_number is REQUIRED and reused);
    the superseded PDF + sidecar in Drive are archived under an `e{n}-` prefix
    before the new versions land under the canonical names; the Monday CO item
    + Ops task are updated in place (CO Status reset to Drafted); Slack/Gmail
    use revision wording.
    """
    validate(data)
    if revise and not ((data.get("change_order") or {}).get("co_number") or "").strip():
        raise ValueError(
            "Revision requires change_order.co_number — the original CO "
            "number that stays on the updated document."
        )

    co_number = assign_co_number(data, refresh=(mode == "finalize"))
    # Persist the RESOLVED number onto a copy of the payload (mirrors
    # estimate_flow's `data.setdefault("estimate", {})["identifier"] = ...`):
    # the sidecar written at finalize is `data` verbatim, and a later revision
    # needs change_order.co_number to already be the pinned CO id, not blank
    # (the common case — the form leaves the CO number field empty so the
    # service auto-assigns it).
    data = deepcopy(data)
    data.setdefault("change_order", {})["co_number"] = co_number
    issue = _parse_date((data.get("change_order") or {}).get("date"))
    co_payload = _build_co_payload(data, co_number, issue)
    total = _co_total(data.get("change_order") or {})
    total_pretty = fmt_money(total)

    output_path = Path(output_dir) / f"{co_number}.pdf"
    render_co_pdf(
        co_payload,
        job=data.get("job") or {},
        client=data.get("client") or {},
        output_path=output_path,
        standalone=True,
    )
    print(f"[{mode} {co_number}] change order PDF: {output_path}")

    writeback: dict = {
        "source": source_label,
        "identifier": co_number,
        "base_number": co_payload["base_number"],
        "mode": mode,
        "pdf_path": str(output_path),
        "total": total,
        "total_pretty": total_pretty,
    }

    if mode == "dry-run":
        try:
            from adapters.gcs import upload_preview_pdf, utc_timestamp
            writeback["preview_pdf_url"] = upload_preview_pdf(
                output_path, identifier=co_number, run_timestamp=utc_timestamp(),
            )
            print(f"[dry-run {co_number}] preview URL: {writeback['preview_pdf_url']}")
        except Exception as e:  # GCSNotConfigured or upload failure — non-fatal
            print(f"[dry-run {co_number}] preview upload skipped: {type(e).__name__}: {e}")
        return writeback

    if mode == "finalize":
        link = data.get("_link") or {}
        folder_url = link.get("gfolder_url") or link.get("drive_folder_url")
        job_name = (data.get("job") or {}).get("name") or "Change Order"

        # 1) File the CO PDF into the job's Drive folder, under "Change Orders/".
        #    Preferred path: NO pasted link — auto-resolve the project's own
        #    folder the way the estimate + invoice flows do (Monday is the source
        #    of truth for the project), so the CO lands in the SAME project folder
        #    the estimate created, under its Change Orders/ leaf. A pasted / Monday
        #    GFolder URL, when present, still wins as an explicit override.
        #
        #    Version-safety invariant (mirrors the estimate revision flow): a
        #    previously sent PDF is never overwritten. If the canonical filename
        #    already exists in the folder (revision — or a re-run under the same
        #    number), it is archived first by renaming to `e{n}-<name>` (file ID
        #    and links preserved), then the new PDF lands under the canonical
        #    name. The as-sent JSON sidecar gets the same treatment.
        revision_version: Optional[int] = None  # v2 = first revision; for Slack
        try:
            from adapters.drive import (DriveNotConfigured, DriveUploader, folder_id_from_url,
                               slug_for_path)
            up = DriveUploader()
            fid = folder_id_from_url(folder_url) if folder_url else None
            try:
                if fid:
                    # Explicit link points at the project's parent folder.
                    co_folder_id = up.ensure_folder(CO_SUBFOLDER_NAME, fid)
                    co_folder_path = CO_SUBFOLDER_NAME
                else:
                    # No link — derive the chain from client/job, mirroring
                    # estimate_flow so it resolves to the existing project folder.
                    client_d = data.get("client") or {}
                    job_d = data.get("job") or {}
                    from subsystems.jobstart import naming as _naming
                    location = (job_d.get("street_address") or job_d.get("location")
                                or "").strip()
                    project_label = _naming.compose_job_name(
                        location, client_d.get("name", ""),
                        raw_name=(job_d.get("name") or "").strip() or None,
                        project_type=(job_d.get("project_type") or "").strip() or None,
                        job_title=(job_d.get("job_title") or "").strip() or None)
                    _ptype = (job_d.get("project_type") or "").lower()
                    if not _ptype:
                        _mjt = (job_d.get("monday_job_type") or "").lower()
                        _ptype = ("commercial" if _mjt in ("commercial", "aia")
                                  else "residential")
                    folder = up.ensure_change_order_folder(
                        customer=client_d.get("name", "Unknown Client"),
                        project_label=project_label,
                        project_type=_ptype,
                        year=issue.year,
                    )
                    co_folder_id = folder["folder_id"]
                    co_folder_path = folder["folder_path"]

                filename = slug_for_path(co_pdf_filename(co_number, job_name)) + ".pdf"
                sidecar_name = sidecar_filename(co_number)

                # -- Archive the superseded version (never destroy a sent doc) --
                try:
                    existing_pdf = up.find_child_file(co_folder_id, filename)
                    if existing_pdf:
                        names = up.list_child_names(co_folder_id)
                        pdf_archive = next_archive_name(names, filename)
                        up.rename_file(existing_pdf["id"], pdf_archive)
                        archived = [pdf_archive]
                        # Same e{n}- prefix for the sidecar so versions pair up.
                        prefix = pdf_archive[: -len(filename)]
                        existing_sc = up.find_child_file(co_folder_id, sidecar_name)
                        if existing_sc:
                            up.rename_file(existing_sc["id"], prefix + sidecar_name)
                            archived.append(prefix + sidecar_name)
                        writeback["drive_archived"] = archived
                        n = archive_version(pdf_archive) or 0
                        revision_version = n + 1
                        print(f"[finalize {co_number}] Drive: archived prior "
                              f"version as {', '.join(archived)}")
                except Exception as e:  # noqa: BLE001 — archive is best-effort;
                    # the upload below replaces in place if archiving failed.
                    writeback["drive_archive_error"] = f"{type(e).__name__}: {e}"
                    print(f"[finalize {co_number}] Drive archive failed "
                          f"(non-fatal): {e}", file=sys.stderr)

                drive_file = up.upload_pdf_to_folder(
                    output_path, folder_id=co_folder_id, filename=filename,
                )
                writeback["drive_pdf_url"] = drive_file.get("web_view_link")
                writeback["drive_folder_path"] = f"{co_folder_path}/{filename}"
                print(f"[finalize {co_number}] Drive: {co_folder_path}/{filename}")

                # -- As-sent JSON sidecar (enables full-field revision prefill) --
                try:
                    import json as _json
                    sc = up.upload_or_replace_file(
                        folder_id=co_folder_id,
                        filename=sidecar_name,
                        data=_json.dumps(data, indent=2, default=str).encode("utf-8"),
                        mimetype="application/json",
                    )
                    writeback["drive_sidecar_file_id"] = sc.get("file_id")
                except Exception as e:  # noqa: BLE001 — sidecar is non-fatal
                    writeback["drive_sidecar_error"] = f"{type(e).__name__}: {e}"
                    print(f"[finalize {co_number}] sidecar save failed "
                          f"(non-fatal): {e}", file=sys.stderr)
            except DriveNotConfigured as e:
                writeback["drive_status"] = f"SKIPPED — {e}"
                print(f"[finalize {co_number}] {writeback['drive_status']}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — Drive never blocks the draft
            writeback["drive_status"] = f"FAILED — {type(e).__name__}: {e}"
            print(f"[finalize {co_number}] Drive save FAILED: {e}", file=sys.stderr)

        # 2) hello@ Gmail draft (NOT sent). Dedup by co_number updates an
        #    unsent draft in place — so a revision replaces the pending draft
        #    rather than leaving a stale one behind.
        try:
            from adapters.gmail import HELLO_TOKEN_PATH, GmailNotConfigured, create_draft
            client = data.get("client") or {}
            subject = (f"Green Valley Contractors — Change Order {co_number} — "
                       f"{job_name}").strip(" —")
            filename = f"{co_number} - {job_name}.pdf"
            try:
                draft = create_draft(
                    to=client["email"],
                    subject=subject,
                    body=_compose_email_body(data, co_number, total_pretty, revised=revise),
                    attachment_path=output_path,
                    attachment_filename=filename,
                    from_addr=HELLO_FROM_ADDR,
                    invoice_identifier=co_number,  # dedup-by-subject key
                    token_path=HELLO_TOKEN_PATH,
                )
                writeback["gmail_draft_id"] = draft["draft_id"]
                writeback["gmail_draft_url"] = draft["gmail_url"]
                print(f"[finalize {co_number}] hello@ draft: {draft['gmail_url']}")
            except GmailNotConfigured as e:
                writeback["gmail_status"] = f"SKIPPED — hello@ Gmail not configured: {e}"
                print(f"[finalize {co_number}] {writeback['gmail_status']}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            writeback["gmail_status"] = f"FAILED — {type(e).__name__}: {e}"
            print(f"[finalize {co_number}] Gmail draft FAILED: {e}", file=sys.stderr)

        # 3) #change-orders Slack notice.
        try:
            from adapters.slack_notify import SlackNotConfigured, notify_change_order_drafted
            try:
                notify_change_order_drafted({
                    "co_number": co_number,
                    "base_number": co_payload["base_number"],
                    "client_name": (data.get("client") or {}).get("name"),
                    "job_name": job_name,
                    "total_pretty": total_pretty,
                    "prepared_by_name": (data.get("prepared_by") or {}).get("name"),
                }, revised=revise, version=revision_version)
                writeback["slack_notified"] = True
            except SlackNotConfigured as e:
                writeback["slack_notified"] = False
                writeback["slack_status"] = f"SKIPPED — {e}"
        except Exception as e:  # noqa: BLE001 — a real post failure, surfaced below
            writeback["slack_notified"] = False
            writeback["slack_error"] = f"{type(e).__name__}: {e}"
            print(f"[finalize {co_number}] Slack notice failed (non-fatal): {e}", file=sys.stderr)

        # 4) Monday: create/update the top-level CO item + the Operations task
        #    (new model, 2026-07-16). Graceful; never raises. On revise, the
        #    existing CO item is updated in place and CO Status resets to
        #    Drafted; a prior Billed status surfaces as monday_billed_warning
        #    (WARN + ALLOW — the invoice is never touched).
        from adapters.monday import co as monday_co
        scope_notes = (data.get("change_order") or {}).get("description") or co_payload["title"]
        writeback.update(monday_co.write_back(
            parent_item_id=link.get("monday_item_id"),
            folder_url=folder_url,
            co_identifier=co_number,
            amount=total,
            issue_date=issue.isoformat(),
            drive_url=writeback.get("drive_pdf_url"),
            gmail_url=writeback.get("gmail_draft_url"),
            scope_notes=scope_notes,
            revise=revise,
        ))
        if revise:
            writeback["revised"] = True
            if revision_version:
                writeback["revision_version"] = revision_version

        # 5) Ops alert if a finalize step silently failed (Gmail / Drive / the
        #    Slack notice). Finalize returns success even when a downstream step
        #    fails, so this keeps a half-failed CO from going unnoticed — same
        #    treatment as the estimate + invoice flows. Best-effort; never raises.
        try:
            from adapters.slack_notify import notify_finalize_degraded
            failed: dict[str, str] = {}
            if str(writeback.get("gmail_status", "")).startswith("FAILED"):
                failed["Gmail draft"] = writeback["gmail_status"]
            if str(writeback.get("drive_status", "")).startswith("FAILED"):
                failed["Drive save"] = writeback["drive_status"]
            if writeback.get("slack_error"):
                failed["Slack notice"] = writeback["slack_error"]
            notify_finalize_degraded("Change Order", co_number, failed)
        except Exception:  # noqa: BLE001 — alerting must never break the flow
            pass

        return writeback

    raise ValueError(f"Unknown mode: {mode!r} (expected 'dry-run' or 'finalize').")
