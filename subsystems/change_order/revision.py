"""
Change Order revision — pure helpers.
=========================================================================
Confirmed design (decided 2026-07-16 — docs/portal-co-parity-design.md):

  * The CO NUMBER never changes on revision. The client always sees the same
    `CO.{n}-{base}`; the revision replaces the document under that id.
  * Prior versions are archived IN PLACE in the job's Change Orders/ Drive
    folder by renaming with an `e{n}-` prefix (estimate pattern; the generic
    archive helpers are reused from subsystems/estimate/revision).
  * The full as-sent CO data is persisted as a JSON sidecar
    ("<identifier>.gvc-co.json") next to the PDF at every finalize — so a
    revision can prefill EVERY form field including the breakdown. COs sent
    before the sidecar shipped fall back to the Monday project prefill
    (re-enter the breakdown).
  * Revising a CO whose Monday status is Billed is WARN + ALLOW — the linked
    invoice is never touched.

Everything here is pure (no I/O) so it unit-tests without Drive/Monday.
"""
from __future__ import annotations

from typing import Optional

# The e{n}- archive naming is document-agnostic — reuse the estimate helpers
# so there is exactly ONE implementation of the archive rule.
from subsystems.estimate.revision import archive_version, next_archive_name  # noqa: F401

SIDECAR_SUFFIX = ".gvc-co.json"


def sidecar_filename(identifier: str) -> str:
    """Canonical name of the as-sent JSON sidecar for a CO."""
    return f"{identifier}{SIDECAR_SUFFIX}"


def co_pdf_filename(co_number: str, job_name: str) -> str:
    """
    Canonical Drive filename for a CO PDF (the ONE naming rule — the flow's
    Drive upload and the revision archiver both use this). Mirrors the name
    the flow has always produced: "{co_number} - {job_name}.pdf", sanitized.
    NOTE: sanitization is applied by the caller via drive.slug_for_path (kept
    there so this module stays import-light); this helper only composes.
    """
    return f"{co_number} - {job_name or 'Change Order'}"


def merge_revision_prefill(sidecar_data: dict, *,
                           monday_item_id: Optional[int] = None) -> dict:
    """
    Turn a loaded sidecar (the exact canonical CO JSON that was finalized)
    into a form prefill. The sidecar wins on everything it has; the ONLY
    injected value is _link.monday_item_id from the current lookup, so the
    revision writes back to that exact Projects item even if the original
    was created by folder-match.
    """
    data = dict(sidecar_data or {})
    link = dict(data.get("_link") or {})
    if monday_item_id:
        link["monday_item_id"] = int(monday_item_id)
    data["_link"] = link
    return data


def prior_total(sidecar_data: dict) -> float:
    """Sum of the sidecar's breakdown (the as-sent CO total), for the UI."""
    co = (sidecar_data or {}).get("change_order") or {}
    if co.get("total") is not None:
        try:
            return round(float(co["total"]), 2)
        except (TypeError, ValueError):
            pass
    total = 0.0
    for row in co.get("breakdown") or []:
        try:
            total += float((row or {}).get("amount") or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)
