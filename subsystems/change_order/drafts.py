"""
Change Order draft store — shared, resumable in-progress COs, backed by a
single JSON object in GCS.
=========================================================================
The exact sibling of estimate/invoice drafts, for the Change Order form
(web/change-order.html). The browser autosaves to localStorage so nothing is
lost locally; this module is the SHARED server copy so any teammate with the
`change_order` grant can resume or delete a draft from any device.

NOT a source of truth — a draft is pre-submission scratch. The CO only becomes
real at finalize (Drive PDF + hello@ draft + Monday CO item). Drafts are
deleted on a successful finalize.

The pure logic (validation, upsert/remove/cap, last-writer-wins) is shared with
estimate drafts; only the GCS object differs (portal/change-order-drafts.json)
so the three stores never collide. Reuses portal_store's bucket/creds plumbing.
"""
from __future__ import annotations

import json
import os

from subsystems.estimate import drafts as _ed
from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

# Reuse the pure helpers verbatim — they're form-agnostic.
DraftValidationError = _ed.DraftValidationError
valid_draft_id = _ed.valid_draft_id
empty_doc = _ed.empty_doc
summarize = _ed.summarize
apply_upsert = _ed.apply_upsert
apply_remove = _ed.apply_remove
validate_input = _ed.validate_input

DEFAULT_OBJECT = "portal/change-order-drafts.json"


def _object_name() -> str:
    return os.environ.get("GVC_CO_DRAFTS_OBJECT") or DEFAULT_OBJECT


# ---------------------------------------------------------------------------
# GCS I/O (own object; reuses portal_store._blob for bucket/creds)
# ---------------------------------------------------------------------------

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
    if new_doc is doc:  # no-op (e.g. stale write) — don't touch the blob
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
# Public API (used by the /ui/api/change-order/drafts routes)
# ---------------------------------------------------------------------------

def list_drafts() -> list[dict]:
    doc, _ = _read()
    return summarize(doc)


def upsert_draft(draft_id, *, label, payload, updated_at, actor):
    """Add or update a draft. Returns (stored_record, stale)."""
    validate_input(draft_id, payload, updated_at)

    def mut(doc: dict):
        new_doc, record, stale = apply_upsert(
            doc, draft_id=draft_id, label=label, payload=payload,
            updated_at=updated_at, actor=actor,
        )
        if stale:
            return doc, (record, True)
        return new_doc, (record, False)

    return _commit(mut)


def remove_draft(draft_id) -> bool:
    """Delete a draft. Returns True if it existed."""
    if not valid_draft_id(draft_id):
        raise DraftValidationError("Invalid draft id.")

    def mut(doc: dict):
        new_doc, existed = apply_remove(doc, draft_id=draft_id)
        return new_doc, existed

    return _commit(mut)
