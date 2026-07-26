"""
Estimate draft store — shared, resumable in-progress estimates, backed by a
single JSON object in GCS.
=========================================================================
Why this exists: the estimate form (web/estimate.html) is a long form, and a
session can time out (1h cookie TTL) or a connection can drop mid-fill. The
browser autosaves to localStorage so nothing is ever lost locally; this module
is the SHARED server copy so any teammate with the `estimate` grant can see,
resume, or delete a draft from any device — not just the machine it was started
on.

NOT a source of truth for estimates. A draft is pre-submission scratch; an
estimate only becomes real at finalize (Monday write-back, the SoT). Drafts are
deleted on successful finalize.

Layout — one object, default `portal/estimate-drafts.json`:

    {
      "version": 1,
      "drafts": {
        "<draft_id>": {
          "id": "<draft_id>",
          "label": "Maxwell Construction — Lawrenceburg Admin Bldg",
          "payload": { ...canonical estimate JSON the form collected... },
          "created_at": "2026-06-16T13:00:00+00:00",
          "updated_at": "2026-06-16T13:42:11+00:00",   # client clock (ISO)
          "updated_by": "andrea@greenvalleycontractors.com"
        }
      }
    }

Concurrency: reads carry the blob generation; writes pass `if_generation_match`
so two teammates editing different drafts can't clobber each other. On a
precondition failure we reload and re-apply the single-draft mutation once.

Conflict resolution: last-writer-wins by `updated_at`. An upsert whose
`updated_at` is older than the stored copy is treated as STALE and ignored (so a
stale browser tab can't overwrite a newer edit made elsewhere).

Reuses portal_store's GCS plumbing (bucket / service-account / blob handle) so
there's one place that knows how to talk to the bucket. Stores opaque payloads
after structural checks only — it does not interpret estimate fields.
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
DEFAULT_OBJECT = "portal/estimate-drafts.json"

# Defensive caps — drafts are small and transient; refuse pathological input
# rather than let one client bloat the shared object.
MAX_DRAFTS = int(os.environ.get("GVC_ESTIMATE_DRAFTS_MAX") or "200")
MAX_PAYLOAD_BYTES = int(os.environ.get("GVC_ESTIMATE_DRAFT_MAX_BYTES") or str(256 * 1024))
MAX_LABEL_LEN = 200

# A draft id is generated client-side; keep it to a safe charset so it's always
# URL-path-safe and can't be used for traversal or injection.
DRAFT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class DraftValidationError(ValueError):
    """Raised on structurally invalid draft input (caller maps to HTTP 422)."""


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-tested directly)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_doc() -> dict:
    return {"version": DOC_VERSION, "drafts": {}}


def valid_draft_id(draft_id: str) -> bool:
    return bool(DRAFT_ID_RE.match(draft_id or ""))


def _coerce_label(label: Optional[str], payload: dict) -> str:
    """A short human label for the draft list. Falls back to client/job if the
    client didn't supply one. Always non-empty and length-bounded."""
    label = (label or "").strip()
    if not label:
        client = ((payload.get("client") or {}).get("name") or "").strip()
        job = ((payload.get("job") or {}).get("name") or "").strip()
        label = " — ".join([p for p in (client, job) if p]) or "Untitled estimate"
    return label[:MAX_LABEL_LEN]


def _payload_size(payload: dict) -> int:
    return len(json.dumps(payload, default=str).encode("utf-8"))


def validate_input(draft_id: str, payload: Any, updated_at: Optional[str]) -> None:
    """Structural checks shared by the upsert path. Raises DraftValidationError."""
    if not valid_draft_id(draft_id):
        raise DraftValidationError(
            "Invalid draft id (expected 8–64 chars of [A-Za-z0-9._-])."
        )
    if not isinstance(payload, dict):
        raise DraftValidationError("payload must be a JSON object.")
    size = _payload_size(payload)
    if size > MAX_PAYLOAD_BYTES:
        raise DraftValidationError(
            f"Draft payload is too large ({size} bytes > {MAX_PAYLOAD_BYTES})."
        )
    if updated_at is not None and not isinstance(updated_at, str):
        raise DraftValidationError("updated_at must be an ISO-8601 string.")


def apply_upsert(
    doc: dict,
    *,
    draft_id: str,
    label: Optional[str],
    payload: dict,
    updated_at: Optional[str],
    actor: str,
    max_drafts: int = MAX_DRAFTS,
) -> tuple[dict, dict, bool]:
    """Return (new_doc, stored_record, stale). Pure — the caller persists.

    `stale` is True when the incoming `updated_at` is older than the stored
    copy; in that case the stored copy is kept unchanged and returned.
    """
    drafts = dict(doc.get("drafts", {}))
    existing = drafts.get(draft_id)
    incoming_ts = (updated_at or "").strip() or _now_iso()

    # Stale-write guard: a strictly older timestamp loses to what's stored.
    if existing and isinstance(existing.get("updated_at"), str):
        if incoming_ts < existing["updated_at"]:
            return doc, existing, True

    record = {
        "id": draft_id,
        "label": _coerce_label(label, payload),
        "payload": payload,
        "created_at": (existing or {}).get("created_at") or incoming_ts,
        "updated_at": incoming_ts,
        "updated_by": portal_store.normalize_email(actor) or "unknown",
    }
    drafts[draft_id] = record

    # Cap growth: when adding a NEW id over the cap, evict the oldest by
    # updated_at (never evicts the draft we just wrote).
    if existing is None and len(drafts) > max_drafts:
        evictable = sorted(
            (k for k in drafts if k != draft_id),
            key=lambda k: drafts[k].get("updated_at") or "",
        )
        for k in evictable[: len(drafts) - max_drafts]:
            drafts.pop(k, None)

    return {"version": doc.get("version", DOC_VERSION), "drafts": drafts}, record, False


def apply_remove(doc: dict, *, draft_id: str) -> tuple[dict, bool]:
    """Return (new_doc, existed)."""
    drafts = dict(doc.get("drafts", {}))
    existed = draft_id in drafts
    drafts.pop(draft_id, None)
    return {"version": doc.get("version", DOC_VERSION), "drafts": drafts}, existed


def summarize(doc: dict) -> list[dict]:
    """List view, newest first. Includes the full payload (drafts are small),
    so the form can resume without a second round-trip."""
    drafts = doc.get("drafts", {})
    rows = list(drafts.values())
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return rows


# ---------------------------------------------------------------------------
# GCS I/O  (own object; reuses portal_store._blob for bucket/creds)
# ---------------------------------------------------------------------------

def _object_name() -> str:
    return os.environ.get("GVC_ESTIMATE_DRAFTS_OBJECT") or DEFAULT_OBJECT


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
    """Load → mutator(doc) → guarded write, retry once on precondition fail.

    mutator returns (new_doc, result); we return result."""
    from google.api_core.exceptions import PreconditionFailed

    doc, gen = _read()
    new_doc, result = mutator(doc)
    if new_doc is doc:  # mutator signalled a no-op (e.g. stale write) — don't touch the blob
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
# Public API (used by the /ui/api/estimate/drafts routes)
# ---------------------------------------------------------------------------

def list_drafts() -> list[dict]:
    doc, _ = _read()
    return summarize(doc)


def upsert_draft(
    draft_id: str,
    *,
    label: Optional[str],
    payload: dict,
    updated_at: Optional[str],
    actor: str,
) -> tuple[dict, bool]:
    """Add or update a draft. Returns (stored_record, stale)."""
    validate_input(draft_id, payload, updated_at)

    def mut(doc: dict):
        new_doc, record, stale = apply_upsert(
            doc, draft_id=draft_id, label=label, payload=payload,
            updated_at=updated_at, actor=actor,
        )
        # Short-circuit a stale write to a no-op so _commit doesn't churn the blob.
        if stale:
            return doc, (record, True)
        return new_doc, (record, False)

    return _commit(mut)


def remove_draft(draft_id: str) -> bool:
    """Delete a draft. Returns True if it existed."""
    if not valid_draft_id(draft_id):
        raise DraftValidationError("Invalid draft id.")

    def mut(doc: dict):
        new_doc, existed = apply_remove(doc, draft_id=draft_id)
        return new_doc, existed

    return _commit(mut)
