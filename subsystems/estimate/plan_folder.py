"""Jake plan-folder helpers for estimate finalize.

On Accept, when a Plan Folder # is known, the estimate PDF is ALSO uploaded to
the root of Jake's numbered plan folder (in addition to Projects/.../Estimate/).
Lookup uses DriveUploader.find_numbered_child_folder against
adapters.monday.estimate.JAKE_PLAN_FOLDER_ROOT.

Every outcome is soft-fail: missing / ambiguous / no_access / upload errors
never block Gmail, Monday, or the Projects Estimate/ path.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional


_PLAN_NUM_RE = re.compile(r"^\s*(\d{1,5})")


def normalize_plan_folder_number(value: Any) -> Optional[str]:
    """Return the leading 1–5 digits of a Plan Folder #, or None."""
    if value is None:
        return None
    m = _PLAN_NUM_RE.match(str(value))
    return m.group(1) if m else None


def upload_pdf_to_jake_plan_folder(
    uploader,
    *,
    pdf_path: Path,
    filename: str,
    plan_number: str,
    root_id: str,
) -> dict:
    """
    Find Jake's numbered plan folder under `root_id` and upload `pdf_path`
    into that folder's root (idempotent replace-by-name).

    Returns a writeback fragment. NEVER raises.
      success: plan_folder_ok=True, plan_folder_id, plan_folder_name,
               plan_folder_pdf_url, plan_folder_number
      soft-fail: plan_folder_ok=False, plan_folder_reason, plan_folder_status
                 (+ candidates on ambiguous)
    """
    report: dict = {"plan_folder_ok": False}
    number = normalize_plan_folder_number(plan_number)
    if not number:
        report["plan_folder_reason"] = "missing"
        report["plan_folder_status"] = "SKIPPED — no Plan Folder # supplied."
        return report
    report["plan_folder_number"] = number

    root = (root_id or "").strip()
    if not root:
        report["plan_folder_reason"] = "missing"
        report["plan_folder_status"] = (
            "SKIPPED — Jake plan-folder root is not configured."
        )
        return report

    try:
        found = uploader.find_numbered_child_folder(root, number)
    except Exception as e:  # noqa: BLE001 — soft-fail
        report["plan_folder_reason"] = "error"
        report["plan_folder_status"] = (
            f"FAILED — find_numbered_child_folder: {type(e).__name__}: {e}"
        )
        return report

    if not isinstance(found, dict) or not found.get("ok"):
        reason = (found or {}).get("reason") or "not_found"
        detail = (found or {}).get("detail") or (
            f"Plan folder {number} was not resolved."
        )
        report["plan_folder_reason"] = reason
        report["plan_folder_status"] = f"SKIPPED — {detail}"
        if (found or {}).get("candidates"):
            report["plan_folder_candidates"] = list(found["candidates"])
        return report

    folder_id = found["folder_id"]
    report["plan_folder_id"] = folder_id
    report["plan_folder_name"] = found.get("name")

    try:
        drive_file = uploader.upload_pdf_to_folder(
            Path(pdf_path),
            folder_id=folder_id,
            filename=filename,
        )
    except Exception as e:  # noqa: BLE001 — soft-fail
        report["plan_folder_reason"] = "upload_error"
        report["plan_folder_status"] = (
            f"FAILED — upload to plan folder: {type(e).__name__}: {e}"
        )
        return report

    report["plan_folder_ok"] = True
    report["plan_folder_pdf_url"] = drive_file.get("web_view_link")
    report["plan_folder_filename"] = drive_file.get("filename") or filename
    return report
