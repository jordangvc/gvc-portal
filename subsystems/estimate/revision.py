"""
Estimate revision — pure helpers.
=========================================================================
Confirmed design (decided 2026-07-02):

  * The OUTBOUND estimate number never changes on revision. The client
    always sees the same `YYYY-MMDD-NNN`; once a client agrees, that number
    propagates forward through all downstream documentation (COs, invoices).
  * Prior versions are archived IN PLACE in the project's Estimate/ Drive
    folder by renaming with an `e{n}-` prefix: `e1-` = the original,
    `e2-` = the first revision once superseded, and so on. The live
    (canonical-name) file is always the current version.
  * The full as-sent estimate data is persisted as a JSON sidecar
    ("<identifier>.gvc-est.json") next to the PDF at every finalize —
    mirroring the invoice correction sidecar — so a revision can prefill
    EVERY form field including line items. Estimates sent before the
    sidecar shipped fall back to the Monday prefill (no line items).

Everything here is pure (no I/O) so it unit-tests without Drive/Monday.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

SIDECAR_SUFFIX = ".gvc-est.json"

# e{n}- archive prefix. Anchored + numeric so ordinary filenames that happen
# to start with "e" (e.g. "estimate notes.pdf") never match.
ARCHIVE_PREFIX_RE = re.compile(r"^e(\d+)-")


def sidecar_filename(identifier: str) -> str:
    """Canonical name of the as-sent JSON sidecar for an estimate."""
    return f"{identifier}{SIDECAR_SUFFIX}"


def estimate_pdf_filename(identifier: str, project_label: str) -> str:
    """Canonical Drive filename for an estimate PDF (the ONE naming rule —
    estimate_flow's Drive upload and the revision archiver both use this)."""
    return f"Estimate {identifier} - {project_label}.pdf"


def archive_version(name: str) -> Optional[int]:
    """Return n when `name` is an `e{n}-…` archive, else None."""
    m = ARCHIVE_PREFIX_RE.match(name or "")
    return int(m.group(1)) if m else None


def next_archive_name(existing_names: Iterable[str], filename: str) -> str:
    """
    Compute the archive name for `filename` given the folder's current
    children: `e{n}-{filename}` where n = 1 + the highest existing archive
    OF THIS SAME FILE. Scoped to the exact filename so two different
    estimates living in one project folder (it happens — a drywall bid and
    a painting bid on the same deal) keep independent e-counters.
    """
    versions = [
        v for name in existing_names
        if (v := archive_version(name)) is not None
        and name[len(f"e{v}-"):] == filename
    ]
    return f"e{(max(versions) + 1) if versions else 1}-{filename}"


def merge_revision_prefill(sidecar_data: dict, *, monday_item_id: Optional[int] = None) -> dict:
    """
    Turn a loaded sidecar (the exact canonical estimate JSON that was
    finalized) into a form prefill. The sidecar wins on everything it has;
    the ONLY injected value is job.monday_item_id from the current lookup,
    so the revision writes back to that exact Bid Board item even if the
    original was created by name-match (pre-prefill era).
    """
    data = dict(sidecar_data or {})
    job = dict(data.get("job") or {})
    if monday_item_id:
        job["monday_item_id"] = int(monday_item_id)
    data["job"] = job
    return data
