"""
Field Manual checklist RUNS — a crew member starts a procedure checklist against
a specific job, works it, and anyone on the crew can resume it later.

Deliberately a near-copy of `subsystems/estimate/drafts.py`: same GCS-object +
generation-guard + last-writer-wins shape, same defensive caps, same no-op-on-
stale behaviour. If you fix a bug in one, look at the other.

WHY RUNS ARE SHARED, NOT PRIVATE (decided by Jordan, 2026-07-29): Mark starts the
hang checklist on a job and Robert finishes it after lunch. A run that died with
whoever's phone started it would not survive how the crews actually work.

WHY THERE IS NO MONDAY WRITEBACK (same decision): Job Check owns the Projects-board
stage columns and is the only thing that writes them. Two features writing the same
column is how a stale run silently regresses a status the office set. A finished run
here is a record that the crew worked the checklist — nothing more.

OFFLINE IS THE NORMAL CASE, NOT THE EDGE CASE. Jobsite signal is bad. The browser
holds the working copy in localStorage and syncs best-effort; the server is the
shared copy, not the source of truth mid-shift. Last writer wins on `updated_at`,
and a stale write is dropped rather than clobbering someone further along.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

DOC_VERSION = 1
DEFAULT_OBJECT = "portal/fieldguide-runs.json"

# Defensive caps. A run is a short list of step keys, so these are generous.
MAX_RUNS = int(os.environ.get("GVC_FIELDGUIDE_RUNS_MAX") or "400")
MAX_PAYLOAD_BYTES = int(os.environ.get("GVC_FIELDGUIDE_RUN_MAX_BYTES") or str(64 * 1024))
MAX_CHECKED = 2000
MAX_LABEL_LEN = 200
MAX_NOTE_LEN = 4000
MAX_KEY_LEN = 64

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
# Step keys are generated in the browser from a hash of the procedure id plus the
# step text, so they survive steps being added or reordered. Keep the charset tight.
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,%d}$" % MAX_KEY_LEN)


class RunValidationError(ValueError):
    """Structurally invalid run input (caller maps to HTTP 422)."""


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-tested directly)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_doc() -> dict:
    return {"version": DOC_VERSION, "runs": {}}


def valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.match(run_id or ""))


def clean_checked(raw: Any) -> list[str]:
    """Normalize the checked-step list: strings only, deduped, order preserved,
    length-bounded. Anything unrecognizable is dropped rather than rejected —
    a garbled key should lose one check mark, never fail the whole save."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        k = item.strip()
        if not k or k in seen or not KEY_RE.match(k):
            continue
        seen.add(k)
        out.append(k)
        if len(out) >= MAX_CHECKED:
            break
    return out


def coerce_label(job_name: Optional[str], procedure_name: Optional[str]) -> str:
    """Short human label for the resume list."""
    job = (job_name or "").strip()
    proc = (procedure_name or "").strip()
    label = " — ".join([p for p in (proc, job) if p]) or "Untitled checklist"
    return label[:MAX_LABEL_LEN]


def _payload_size(payload: dict) -> int:
    return len(json.dumps(payload, default=str).encode("utf-8"))


def validate_input(run_id: str, payload: Any, updated_at: Optional[str]) -> None:
    if not valid_run_id(run_id):
        raise RunValidationError(
            "Invalid run id (expected 8–64 chars of [A-Za-z0-9._-])."
        )
    if not isinstance(payload, dict):
        raise RunValidationError("payload must be a JSON object.")
    if not (payload.get("procedure") or "").strip():
        raise RunValidationError("payload.procedure is required.")
    size = _payload_size(payload)
    if size > MAX_PAYLOAD_BYTES:
        raise RunValidationError(
            f"Run payload is too large ({size} bytes > {MAX_PAYLOAD_BYTES})."
        )
    if updated_at is not None and not isinstance(updated_at, str):
        raise RunValidationError("updated_at must be an ISO-8601 string.")


def _shape_record(payload: dict, *, existing: Optional[dict], actor: str,
                  run_id: str, updated_at: Optional[str]) -> dict:
    """Build the stored record. `started_by` and `created_at` are set once and
    never overwritten, so the resume list can always say who began it."""
    ex = existing or {}
    return {
        "run_id": run_id,
        "job_id": str(payload.get("job_id") or ex.get("job_id") or "").strip()[:64],
        "job_name": str(payload.get("job_name") or ex.get("job_name") or "").strip()[:MAX_LABEL_LEN],
        "procedure": str(payload.get("procedure") or "").strip()[:64],
        "procedure_name": str(payload.get("procedure_name") or "").strip()[:MAX_LABEL_LEN],
        "checked": clean_checked(payload.get("checked")),
        "note": str(payload.get("note") or "")[:MAX_NOTE_LEN],
        "done": bool(payload.get("done")),
        "started_by": ex.get("started_by") or actor,
        "updated_by": actor,
        "created_at": ex.get("created_at") or _now_iso(),
        "updated_at": (updated_at or _now_iso()),
    }


def apply_upsert(
    doc: dict,
    *,
    run_id: str,
    payload: dict,
    updated_at: Optional[str],
    actor: str,
    max_runs: int = MAX_RUNS,
) -> tuple[dict, dict, bool]:
    """Returns (new_doc, record, stale).

    `stale` is True when the incoming updated_at is older than what's stored —
    the caller turns that into a no-op so a phone that was offline for an hour
    can't roll back a run somebody else has since advanced.
    """
    runs = dict(doc.get("runs") or {})
    existing = runs.get(run_id)

    if existing and updated_at:
        prior = str(existing.get("updated_at") or "")
        if prior and str(updated_at) < prior:
            return doc, existing, True

    record = _shape_record(payload, existing=existing, actor=actor,
                           run_id=run_id, updated_at=updated_at)
    runs[run_id] = record

    # Cap the shared object: drop the oldest-touched COMPLETED runs first, then
    # oldest overall. Never evict the run being written.
    if len(runs) > max_runs:
        def sort_key(item):
            rid, rec = item
            return (0 if rec.get("done") else 1, str(rec.get("updated_at") or ""))
        for rid, _rec in sorted(runs.items(), key=sort_key):
            if len(runs) <= max_runs:
                break
            if rid != run_id:
                runs.pop(rid, None)

    new_doc = dict(doc)
    new_doc["version"] = DOC_VERSION
    new_doc["runs"] = runs
    return new_doc, record, False


def apply_remove(doc: dict, *, run_id: str) -> tuple[dict, bool]:
    runs = dict(doc.get("runs") or {})
    existed = runs.pop(run_id, None) is not None
    if not existed:
        return doc, False
    new_doc = dict(doc)
    new_doc["runs"] = runs
    return new_doc, True


def summarize(doc: dict, *, procedure: Optional[str] = None,
              include_done: bool = False) -> list[dict]:
    """Resume-list view, most recently touched first."""
    out = []
    for rid, rec in (doc.get("runs") or {}).items():
        if not isinstance(rec, dict):
            continue
        if procedure and rec.get("procedure") != procedure:
            continue
        if rec.get("done") and not include_done:
            continue
        out.append({
            "run_id": rid,
            "job_id": rec.get("job_id") or "",
            "job_name": rec.get("job_name") or "",
            "procedure": rec.get("procedure") or "",
            "procedure_name": rec.get("procedure_name") or "",
            "label": coerce_label(rec.get("job_name"), rec.get("procedure_name")),
            "checked_count": len(rec.get("checked") or []),
            "note": rec.get("note") or "",
            "done": bool(rec.get("done")),
            "started_by": rec.get("started_by") or "",
            "updated_by": rec.get("updated_by") or "",
            "created_at": rec.get("created_at") or "",
            "updated_at": rec.get("updated_at") or "",
        })
    out.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return out


def get(doc: dict, run_id: str) -> Optional[dict]:
    rec = (doc.get("runs") or {}).get(run_id)
    return rec if isinstance(rec, dict) else None


# ---------------------------------------------------------------------------
# I/O (mirrors subsystems/estimate/drafts.py — reuses portal_store._blob)
# ---------------------------------------------------------------------------

def _object_name() -> str:
    return os.environ.get("GVC_FIELDGUIDE_RUNS_OBJECT") or DEFAULT_OBJECT


def _read() -> tuple[dict, int]:
    """Return (doc, generation). Missing object → (empty_doc(), 0)."""
    from google.api_core.exceptions import NotFound

    blob = portal_store._blob(_object_name())
    try:
        blob.reload()
        raw = blob.download_as_text()
    except NotFound:
        return empty_doc(), 0
    try:
        doc = json.loads(raw) if raw.strip() else empty_doc()
    except json.JSONDecodeError:
        raise PortalStoreNotConfigured(f"{_object_name()} is not valid JSON.")
    doc.setdefault("runs", {})
    return doc, int(blob.generation or 0)


def _write(doc: dict, *, if_generation_match: int) -> int:
    blob = portal_store._blob(_object_name())
    blob.upload_from_string(
        json.dumps(doc, indent=2, sort_keys=True),
        content_type="application/json",
        if_generation_match=if_generation_match,
    )
    blob.reload()
    return int(blob.generation or 0)


def _commit(mutator):
    """Load → mutator(doc) → guarded write, retry once on precondition fail."""
    from google.api_core.exceptions import PreconditionFailed

    doc, gen = _read()
    new_doc, result = mutator(doc)
    if new_doc is doc:  # no-op (stale write or nothing to remove)
        return result
    try:
        _write(new_doc, if_generation_match=gen)
    except PreconditionFailed:
        doc, gen = _read()
        new_doc, result = mutator(doc)
        if new_doc is not doc:
            _write(new_doc, if_generation_match=gen)
    return result


# ---------------------------------------------------------------------------
# Public API (used by the /ui/api/fieldguide/runs routes)
# ---------------------------------------------------------------------------

def list_runs(*, procedure: Optional[str] = None,
              include_done: bool = False) -> list[dict]:
    doc, _ = _read()
    return summarize(doc, procedure=procedure, include_done=include_done)


def get_run(run_id: str) -> Optional[dict]:
    if not valid_run_id(run_id):
        raise RunValidationError("Invalid run id.")
    doc, _ = _read()
    return get(doc, run_id)


def upsert_run(run_id: str, *, payload: dict, updated_at: Optional[str],
               actor: str) -> tuple[dict, bool]:
    """Add or update a run. Returns (stored_record, stale)."""
    validate_input(run_id, payload, updated_at)

    def mut(doc: dict):
        new_doc, record, stale = apply_upsert(
            doc, run_id=run_id, payload=payload,
            updated_at=updated_at, actor=actor,
        )
        if stale:
            return doc, (record, True)
        return new_doc, (record, False)

    return _commit(mut)


def remove_run(run_id: str) -> bool:
    if not valid_run_id(run_id):
        raise RunValidationError("Invalid run id.")

    def mut(doc: dict):
        new_doc, existed = apply_remove(doc, run_id=run_id)
        return new_doc, existed

    return _commit(mut)
