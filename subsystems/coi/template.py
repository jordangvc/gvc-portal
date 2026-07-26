"""
COI template store — the current blank ACORD 25, in the portal state bucket.
=========================================================================
The blank COI renews ANNUALLY (agent reissues with new policy dates), so it
must be swappable without a deploy. Two objects in the portal state bucket
(GVC_PORTAL_STATE_BUCKET, falling back to the preview bucket — same resolution
as portal_store; the state bucket has versioning ON and NO lifecycle rule per
the 2026-07-02 standing rule):

    portal/coi/template.pdf        the blank itself (normalized, decrypted)
    portal/coi/template-meta.json  {expiry_label, uploaded_by, uploaded_at,
                                    size_bytes, page_count}

Admins replace it from the COI page (or scripts/seed_coi_template.py). Uploads
are validated + NORMALIZED here: parsed with pypdf, empty-password decrypted,
and rewritten, so downstream stamping never deals with encryption quirks.

Reuses portal_store's GCS plumbing (bucket/creds/blob) — one place knows how
to talk to the bucket. Template reads are cached per instance for a short TTL
(it changes about once a year).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

TEMPLATE_OBJECT_DEFAULT = "portal/coi/template.pdf"
META_OBJECT_DEFAULT = "portal/coi/template-meta.json"
MAX_TEMPLATE_BYTES = 5 * 1024 * 1024  # a 1-page ACORD is ~300KB; 5MB is generous


class CoiTemplateMissing(Exception):
    """No blank COI has been uploaded yet — the generator can't run."""


class CoiTemplateInvalid(ValueError):
    """The uploaded file isn't a usable PDF template."""


def _template_object() -> str:
    return os.environ.get("GVC_COI_TEMPLATE_OBJECT") or TEMPLATE_OBJECT_DEFAULT


def _meta_object() -> str:
    return os.environ.get("GVC_COI_TEMPLATE_META_OBJECT") or META_OBJECT_DEFAULT


# ---------------------------------------------------------------------------
# Pure-ish validation / normalization (no GCS; pypdf imported lazily)
# ---------------------------------------------------------------------------

def normalize_template_bytes(data: bytes) -> tuple[bytes, int]:
    """
    Validate an uploaded blank and return (normalized_bytes, page_count).
    Raises CoiTemplateInvalid with an office-readable message. Normalization
    = parse + empty-password decrypt + rewrite via pypdf, so the stored
    template is always directly stampable.
    """
    if not data:
        raise CoiTemplateInvalid("The uploaded file is empty.")
    if len(data) > MAX_TEMPLATE_BYTES:
        raise CoiTemplateInvalid(
            f"File is {len(data) // 1024}KB — larger than the "
            f"{MAX_TEMPLATE_BYTES // (1024 * 1024)}MB template cap."
        )
    if not data.lstrip()[:5].startswith(b"%PDF-"):
        raise CoiTemplateInvalid("That file isn't a PDF (missing %PDF header).")

    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        page_count = len(reader.pages)
        if page_count < 1:
            raise CoiTemplateInvalid("The PDF has no pages.")
        writer = PdfWriter()
        writer.append(reader)
        out = BytesIO()
        writer.write(out)
    except CoiTemplateInvalid:
        raise
    except Exception as e:  # noqa: BLE001 — surface as a clean validation error
        raise CoiTemplateInvalid(
            f"Couldn't read that PDF ({type(e).__name__}: {e}). "
            "Re-export it from the agent's original and try again."
        )
    return out.getvalue(), page_count


def build_meta(*, expiry_label: Optional[str], actor: str,
               size_bytes: int, page_count: int) -> dict:
    """PURE: the metadata record stored beside the template."""
    label = (expiry_label or "").strip() or None
    return {
        "expiry_label": label,
        "uploaded_by": (actor or "").strip().lower() or "unknown",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": int(size_bytes),
        "page_count": int(page_count),
    }


# ---------------------------------------------------------------------------
# GCS I/O (reuses portal_store._blob for bucket/creds)
# ---------------------------------------------------------------------------

_CACHE: dict[str, Any] = {"bytes": None, "meta": None, "at": 0.0}


def _cache_ttl() -> float:
    try:
        return float(os.environ.get("GVC_COI_TEMPLATE_CACHE_TTL") or "60")
    except ValueError:
        return 60.0


def invalidate_cache() -> None:
    _CACHE.update({"bytes": None, "meta": None, "at": 0.0})


def template_info() -> Optional[dict]:
    """The stored metadata, or None when no template has been uploaded.
    Raises PortalStoreNotConfigured when the bucket/creds are absent."""
    from google.api_core.exceptions import NotFound

    blob = portal_store._blob(_meta_object())
    try:
        raw = blob.download_as_text()
    except NotFound:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None  # corrupt meta ≠ missing template; get_template still works


def get_template() -> tuple[bytes, dict]:
    """
    Return (template_bytes, meta). Served from a short per-instance cache.
    Raises CoiTemplateMissing when nothing has been uploaded yet, and
    PortalStoreNotConfigured when the bucket/creds are absent.
    """
    now = time.time()
    if _CACHE["bytes"] is not None and (now - _CACHE["at"]) < _cache_ttl():
        return _CACHE["bytes"], _CACHE["meta"]

    from google.api_core.exceptions import NotFound

    blob = portal_store._blob(_template_object())
    try:
        data = blob.download_as_bytes()
    except NotFound:
        raise CoiTemplateMissing(
            f"No blank COI template at {_template_object()} — upload the "
            "current agent-issued blank on the COI page (admin) or run "
            "scripts/seed_coi_template.py."
        )
    meta = template_info() or {}
    _CACHE.update({"bytes": data, "meta": meta, "at": now})
    return data, meta


def put_template(data: bytes, *, expiry_label: Optional[str], actor: str) -> dict:
    """
    Validate, normalize, and store a new blank template + its metadata.
    Overwrites in place (the bucket's object versioning keeps history).
    Returns the stored meta. Raises CoiTemplateInvalid /
    PortalStoreNotConfigured.
    """
    normalized, page_count = normalize_template_bytes(data)
    meta = build_meta(expiry_label=expiry_label, actor=actor,
                      size_bytes=len(normalized), page_count=page_count)

    blob = portal_store._blob(_template_object())
    blob.upload_from_string(normalized, content_type="application/pdf")
    meta_blob = portal_store._blob(_meta_object())
    meta_blob.upload_from_string(
        json.dumps(meta, indent=2, sort_keys=True),
        content_type="application/json",
    )
    invalidate_cache()
    return meta
