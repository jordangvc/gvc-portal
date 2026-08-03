"""
Job Start packet drafts — one resumable packet per won bid, shared via GCS.
=========================================================================
Why this exists: Job Start is a HARD GATE (docs/portal-job-start-design.md).
Jordan chose that knowing the flagged risk — a gate strands real won jobs when
the salesperson is in the field and can't complete the packet. This module is
the mitigation that makes the gate acceptable: the packet autosaves on every
change, keyed by bid, and is readable from any device.

Jake can start a packet on his phone in a driveway, lose signal, and finish it
at his desk without retyping. "I can't finish this right now" must never mean
"I lose what I typed."

NOT a source of truth. A draft is pre-handoff scratch; the job becomes real only
when the gate passes and Monday is written. The draft is deleted on a successful
handoff.

Layout — one object, default `portal/jobstart-drafts.json`:

    {
      "version": 1,
      "drafts": {
        "<bid_item_id>": {
          "bid_id": 1926543907,
          "label": "2108 North High St, Columbus OH_Warwick_Commercial",
          "values": {"builder": "Warwick", "lock_box": "1234", ...},
          "job_name": "2108 North High St…",
          "created_at": "2026-07-29T13:00:00+00:00",
          "updated_at": "2026-07-29T13:42:11+00:00",
          "updated_by": "jake@greenvalleycontractors.com"
        }
      }
    }

Keyed by BID ID, not a client-generated id: a bid has exactly one open packet,
so two people opening the same won job collaborate on one draft instead of
racing two. Last-writer-wins by `updated_at`, same rule as estimate drafts —
a stale tab can't overwrite a newer edit made elsewhere.

Reuses portal_store's GCS plumbing (bucket / service-account / blob handle).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

DOC_VERSION = 1
DEFAULT_OBJECT = "portal/jobstart-drafts.json"

MAX_DRAFTS = int(os.environ.get("GVC_JOBSTART_DRAFTS_MAX") or "300")
MAX_PAYLOAD_BYTES = int(os.environ.get("GVC_JOBSTART_DRAFT_MAX_BYTES")
                        or str(64 * 1024))
MAX_LABEL_LEN = 200


class DraftValidationError(ValueError):
    """Structurally invalid draft input (caller maps to HTTP 422)."""


# --- The handoff state machine ---------------------------------------------
# A job belongs to Sales until Operations accepts it. These four states are the
# whole model; Monday items are created ONLY on ACCEPTED (orchestrators/
# jobstart_flow), which is what makes "no acceptance, no job" true rather than
# aspirational.
#
#   draft      sales is still filling it in (autosaves, nobody notified)
#   with_ops   sales sent it; the packet PDF exists; ops needs to act
#   sent_back  ops returned it naming what's missing; sales can edit again
#   accepted   ops accepted; Monday items created, packet filed to Drive
STATUS_DRAFT = "draft"
STATUS_WITH_OPS = "with_ops"
STATUS_SENT_BACK = "sent_back"
STATUS_ACCEPTED = "accepted"
STATUSES = (STATUS_DRAFT, STATUS_WITH_OPS, STATUS_SENT_BACK, STATUS_ACCEPTED)

# States sales may still edit. An accepted packet is a record, not a form.
EDITABLE_STATUSES = frozenset({STATUS_DRAFT, STATUS_SENT_BACK})


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-tested directly)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_doc() -> dict:
    return {"version": DOC_VERSION, "drafts": {}}


def draft_key(bid_id: Any) -> str:
    """Bid item id → the draft's key. Raises on anything non-numeric so a
    malformed id can never create a junk entry in the shared object."""
    try:
        n = int(str(bid_id).strip())
    except (TypeError, ValueError):
        raise DraftValidationError(f"Bid id must be numeric, got {bid_id!r}.")
    if n <= 0:
        raise DraftValidationError("Bid id must be positive.")
    return str(n)


def validate_input(bid_id: Any, values: Any, updated_at: Optional[str]) -> str:
    """Structural checks. Returns the draft key. Does NOT interpret packet
    fields — the gate in jobstart_flow owns meaning; this owns shape."""
    key = draft_key(bid_id)
    if not isinstance(values, dict):
        raise DraftValidationError("Packet values must be an object.")
    size = len(json.dumps(values, default=str).encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise DraftValidationError(
            f"Packet is too large ({size} bytes; max {MAX_PAYLOAD_BYTES}).")
    if updated_at is not None and not isinstance(updated_at, str):
        raise DraftValidationError("updated_at must be an ISO string.")
    return key


def apply_upsert(doc: dict, *, key: str, label: Optional[str], values: dict,
                 job_name: Optional[str], updated_at: Optional[str],
                 actor: str) -> tuple[dict, dict, bool]:
    """
    PURE. Add or update one packet. Returns (new_doc, record, stale).
    `stale` is True when the incoming `updated_at` is older than the stored
    copy — the caller turns that into a no-op so a stale tab can't clobber a
    newer edit made on another device.
    """
    drafts = dict(doc.get("drafts") or {})
    existing = drafts.get(key)

    if existing and updated_at and existing.get("updated_at"):
        if str(updated_at) < str(existing["updated_at"]):
            return doc, existing, True

    if existing is None and len(drafts) >= MAX_DRAFTS:
        raise DraftValidationError(
            f"Too many open packets ({len(drafts)}; max {MAX_DRAFTS}). "
            "Hand off or discard some first.")

    now = _now_iso()
    record = {
        **(existing or {}),                    # keep status + acceptance history
        "bid_id": int(key),
        "label": (label or (existing or {}).get("label") or "")[:MAX_LABEL_LEN],
        "values": values,
        "job_name": job_name or (existing or {}).get("job_name"),
        "status": (existing or {}).get("status") or STATUS_DRAFT,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": updated_at or now,
        "updated_by": actor,
    }
    drafts[key] = record
    return {**doc, "version": DOC_VERSION, "drafts": drafts}, record, False


def apply_status(doc: dict, *, key: str, status: str, actor: str,
                 note: Optional[str] = None,
                 extra: Optional[dict] = None) -> tuple[dict, dict]:
    """
    PURE. Move one packet to a new state, stamping who and when. Returns
    (new_doc, record). Raises DraftValidationError on an unknown status or a
    missing packet — a state change on a packet that doesn't exist is a bug,
    not something to paper over.
    """
    if status not in STATUSES:
        raise DraftValidationError(f"Unknown handoff status {status!r}.")
    drafts = dict(doc.get("drafts") or {})
    existing = drafts.get(key)
    if existing is None:
        raise DraftValidationError("No handoff packet exists for this bid.")

    now = _now_iso()
    record = {**existing, "status": status, "updated_at": now, "updated_by": actor}
    if status == STATUS_WITH_OPS:
        record["sent_at"] = now
        record["sent_by"] = actor
        record["sent_back_note"] = None      # a fresh send clears the last note
    elif status == STATUS_SENT_BACK:
        record["sent_back_at"] = now
        record["sent_back_by"] = actor
        record["sent_back_note"] = (note or "").strip()[:MAX_LABEL_LEN * 4]
    elif status == STATUS_ACCEPTED:
        record["accepted_at"] = now
        record["accepted_by"] = actor
    if extra:
        record.update(extra)

    drafts[key] = record
    return {**doc, "version": DOC_VERSION, "drafts": drafts}, record


def apply_remove(doc: dict, *, key: str) -> tuple[dict, bool]:
    """PURE. Drop one packet. Returns (new_doc, existed)."""
    drafts = dict(doc.get("drafts") or {})
    if key not in drafts:
        return doc, False
    drafts.pop(key)
    return {**doc, "version": DOC_VERSION, "drafts": drafts}, True


def summarize(doc: dict) -> list[dict]:
    """PURE. Packet doc → list rows (no values payload), newest first.
    Packets waiting on ops sort to the top — that's the queue that matters."""
    rows = []
    for rec in (doc.get("drafts") or {}).values():
        rows.append({
            "bid_id": rec.get("bid_id"),
            "label": rec.get("label"),
            "job_name": rec.get("job_name"),
            "status": rec.get("status") or STATUS_DRAFT,
            "filled": len([v for v in (rec.get("values") or {}).values()
                           if str(v or "").strip()]),
            "sent_at": rec.get("sent_at"),
            "sent_by": rec.get("sent_by"),
            "sent_back_note": rec.get("sent_back_note"),
            "accepted_at": rec.get("accepted_at"),
            "accepted_by": rec.get("accepted_by"),
            "packet_url": rec.get("packet_url"),
            "updated_at": rec.get("updated_at"),
            "updated_by": rec.get("updated_by"),
            # GC scope-confirmation state, for the sent-watcher's work list:
            # drafted-but-unconfirmed rows are the ones worth a Gmail search.
            "gc_drafted_at": rec.get("gc_drafted_at"),
            "gc_subject": rec.get("gc_subject"),
            "gc_confirmed_on": ((rec.get("values") or {})
                                .get("gc_confirmed_on") or "").strip() or None,
        })
    order = {STATUS_WITH_OPS: 0, STATUS_SENT_BACK: 1, STATUS_DRAFT: 2,
             STATUS_ACCEPTED: 3}
    rows.sort(key=lambda r: (order.get(r["status"], 9),
                             str(r.get("updated_at") or "")), reverse=False)
    return rows


# ---------------------------------------------------------------------------
# GCS I/O (own object; reuses portal_store._blob for bucket/creds)
# ---------------------------------------------------------------------------

def _object_name() -> str:
    return os.environ.get("GVC_JOBSTART_DRAFTS_OBJECT") or DEFAULT_OBJECT


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
    doc.setdefault("drafts", {})
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
    if new_doc is doc:          # no-op (stale write) — don't churn the blob
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
# Public API
# ---------------------------------------------------------------------------

def list_drafts() -> list[dict]:
    doc, _ = _read()
    return summarize(doc)


def get_draft(bid_id: Any) -> Optional[dict]:
    key = draft_key(bid_id)
    doc, _ = _read()
    return (doc.get("drafts") or {}).get(key)


def save_draft(bid_id: Any, *, values: dict, label: Optional[str] = None,
               job_name: Optional[str] = None,
               updated_at: Optional[str] = None,
               actor: str = "") -> tuple[dict, bool]:
    """Add or update one bid's packet. Returns (record, stale)."""
    key = validate_input(bid_id, values, updated_at)

    def mut(doc: dict):
        new_doc, record, stale = apply_upsert(
            doc, key=key, label=label, values=values, job_name=job_name,
            updated_at=updated_at, actor=actor)
        if stale:
            return doc, (record, True)
        return new_doc, (record, False)

    return _commit(mut)


def set_status(bid_id: Any, *, status: str, actor: str,
               note: Optional[str] = None,
               extra: Optional[dict] = None) -> dict:
    """Move a packet through the state machine. Returns the stored record."""
    key = draft_key(bid_id)

    def mut(doc: dict):
        new_doc, record = apply_status(doc, key=key, status=status, actor=actor,
                                       note=note, extra=extra)
        return new_doc, record

    return _commit(mut)


def remove_draft(bid_id: Any) -> bool:
    """Delete a packet outright. True if it existed. NOT used by the accept
    path — an accepted packet is kept as the record of what was handed over."""
    key = draft_key(bid_id)

    def mut(doc: dict):
        new_doc, existed = apply_remove(doc, key=key)
        return new_doc, existed

    return _commit(mut)
