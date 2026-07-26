"""
Portal state store — grants + employee records, backed by a JSON object in GCS.
=========================================================================
Why GCS and not Firestore: at GVC's scale (~20 employees, 2-3 admins) a single
small JSON object is the fastest correct option and reuses infrastructure that
already works — the same preview bucket, the same service-account JSON, and the
same `google-cloud-storage` dependency the invoice previews use. Firestore is the
documented upgrade target if the data ever outgrows this (see
docs/portal-access-and-people-architecture.md §10).

Layout — one object, default `portal/grants.json`:

    {
      "version": 1,
      "users": {
        "joe@greenvalleycontractors.com": {
          "features": ["*"],
          "person": {"name": "...", "position": "...", "start_date": null,
                     "stop_date": null, "birthday": null, "time_off_url": null},
          "updated_by": "joe@greenvalleycontractors.com",
          "updated_at": "2026-06-15T13:00:00+00:00"
        }
      }
    }

Concurrency: reads carry the blob generation; writes pass `if_generation_match`
so a concurrent admin edit can't silently clobber another. On a precondition
failure we reload and re-apply once before giving up.

Sensitive HR fields (comp, tax, benefits) live in a SEPARATE object
(`portal/hr_private.json`), never mixed into grants — see §2/§9 of the doc. v1
ships the accessor skeleton only.

This module deliberately knows nothing about the *meaning* of feature names
(that's access.py's job) — it stores whatever list of strings it's handed after
structural checks, so there's no import cycle with access.py.
"""
from __future__ import annotations

from shared import paths
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

DOC_VERSION = 1
DEFAULT_OBJECT = "portal/grants.json"
HR_OBJECT = "portal/hr_private.json"

PERSON_FIELDS = ("name", "position", "phone", "start_date", "stop_date", "birthday", "time_off_url")


class PortalStoreNotConfigured(Exception):
    """Raised when no bucket / service-account JSON is available."""


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-tested directly)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def empty_doc() -> dict:
    return {"version": DOC_VERSION, "users": {}}


def _clean_person(person: Optional[dict]) -> dict:
    """Keep only known person fields; coerce blanks to None."""
    person = person or {}
    out: dict[str, Any] = {}
    for f in PERSON_FIELDS:
        v = person.get(f)
        if isinstance(v, str):
            v = v.strip() or None
        out[f] = v
    return out


def apply_upsert(
    doc: dict,
    *,
    email: str,
    features: list[str],
    person: Optional[dict],
    actor: str,
) -> dict:
    """Return a NEW doc with the user added/updated. Pure (caller persists)."""
    email = normalize_email(email)
    if not email:
        raise ValueError("email is required.")
    if not isinstance(features, list) or not all(isinstance(f, str) for f in features):
        raise ValueError("features must be a list of strings.")
    new = {"version": doc.get("version", DOC_VERSION), "users": dict(doc.get("users", {}))}
    existing = new["users"].get(email, {})
    new["users"][email] = {
        "features": sorted({f.strip() for f in features if f.strip()}),
        # merge person: only overwrite fields explicitly provided (non-None)
        "person": _merge_person(existing.get("person"), person),
        "updated_by": normalize_email(actor) or "unknown",
        "updated_at": _now_iso(),
    }
    return new


def _merge_person(existing: Optional[dict], incoming: Optional[dict]) -> dict:
    base = _clean_person(existing)
    if incoming is None:
        return base
    cleaned = _clean_person(incoming)
    # Only overwrite a field when the incoming value is non-None.
    for f in PERSON_FIELDS:
        if cleaned.get(f) is not None:
            base[f] = cleaned[f]
    return base


def apply_remove(doc: dict, *, email: str) -> tuple[dict, bool]:
    """Return (new_doc, existed)."""
    email = normalize_email(email)
    new = {"version": doc.get("version", DOC_VERSION), "users": dict(doc.get("users", {}))}
    existed = email in new["users"]
    new["users"].pop(email, None)
    return new, existed


# ---------------------------------------------------------------------------
# GCS I/O
# ---------------------------------------------------------------------------

def _bucket_name() -> str:
    name = os.environ.get("GVC_PORTAL_STATE_BUCKET") or os.environ.get("GVC_GCS_PREVIEW_BUCKET")
    if not name:
        raise PortalStoreNotConfigured(
            "Set GVC_PORTAL_STATE_BUCKET (or reuse GVC_GCS_PREVIEW_BUCKET) for the portal store."
        )
    return name


def _object_name() -> str:
    return os.environ.get("GVC_PORTAL_STATE_OBJECT") or DEFAULT_OBJECT


def _creds_path() -> Path:
    p = Path(
        os.environ.get("GVC_DRIVE_CREDENTIALS")
        or paths.DEFAULT_SA_PATH
    )
    if not p.exists():
        raise PortalStoreNotConfigured(f"Service account JSON not found at {p}.")
    return p


def _blob(object_name: str):
    """Return a GCS Blob handle (lazy import so the module loads without the dep)."""
    from google.cloud import storage
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(str(_creds_path()))
    client = storage.Client(credentials=creds, project=creds.project_id)
    return client.bucket(_bucket_name()).blob(object_name)


def _read_object(object_name: str) -> tuple[dict, int]:
    """Return (doc, generation). Missing object → (empty_doc(), 0)."""
    import json

    from google.api_core.exceptions import NotFound

    blob = _blob(object_name)
    try:
        blob.reload()  # populate .generation
        raw = blob.download_as_text()
    except NotFound:
        return empty_doc(), 0
    try:
        doc = json.loads(raw) if raw.strip() else empty_doc()
    except json.JSONDecodeError:
        # Corrupt object: do not silently wipe it — surface loudly.
        raise PortalStoreNotConfigured(f"{object_name} is not valid JSON.")
    if "users" not in doc:
        doc.setdefault("users", {})
    return doc, int(blob.generation or 0)


def _write_object(object_name: str, doc: dict, *, if_generation_match: int) -> int:
    import json

    blob = _blob(object_name)
    blob.upload_from_string(
        json.dumps(doc, indent=2, sort_keys=True),
        content_type="application/json",
        if_generation_match=if_generation_match,
    )
    blob.reload()
    return int(blob.generation or 0)


# ---------------------------------------------------------------------------
# Missing-store tripwire (2026-07-02 incident: a bucket lifecycle rule deleted
# portal/grants.json → silent org-wide lockout). shared/ may not import
# adapters/ (layering), so the app layer registers a callback here at import
# time (app.service wires it to slack_notify.post_failure, gated on gcs
# backend). Fired — throttled — whenever a FRESH read finds NO grants object.
# A missing object in gcs mode is never normal: it means every non-superadmin
# resolves to "no access".
# ---------------------------------------------------------------------------

MISSING_STORE_HOOK: Optional[Callable[[str], None]] = None
_MISSING_ALERT_AT: dict[str, float] = {"t": 0.0}


def _alert_interval() -> float:
    try:
        return float(os.environ.get("GVC_PORTAL_STORE_ALERT_INTERVAL") or "3600")
    except ValueError:
        return 3600.0


def store_object_missing() -> bool:
    """True when the most recent grants read found no object at all (gen 0)."""
    return _CACHE["doc"] is not None and _CACHE["gen"] == 0


def _fire_missing_hook() -> None:
    hook = MISSING_STORE_HOOK
    if hook is None:
        return
    now = time.time()
    if now - _MISSING_ALERT_AT["t"] < _alert_interval():
        return
    _MISSING_ALERT_AT["t"] = now
    try:
        hook(_object_name())
    except Exception:  # noqa: BLE001 — alerting must never break access reads
        pass


# ---------------------------------------------------------------------------
# Read-through cache (per-instance; TTL bounds staleness across instances)
# ---------------------------------------------------------------------------

_CACHE: dict[str, Any] = {"doc": None, "gen": None, "at": 0.0}


def _cache_ttl() -> float:
    try:
        return float(os.environ.get("GVC_PORTAL_STATE_CACHE_TTL") or "10")
    except ValueError:
        return 10.0


def invalidate_cache() -> None:
    _CACHE.update({"doc": None, "gen": None, "at": 0.0})


def load(force: bool = False) -> tuple[dict, int]:
    """Return (doc, generation), served from a short per-instance cache."""
    now = time.time()
    if not force and _CACHE["doc"] is not None and (now - _CACHE["at"]) < _cache_ttl():
        return _CACHE["doc"], _CACHE["gen"]
    doc, gen = _read_object(_object_name())
    _CACHE.update({"doc": doc, "gen": gen, "at": now})
    if gen == 0:
        _fire_missing_hook()
    return doc, gen


# ---------------------------------------------------------------------------
# Public API (used by access.py + the admin routes)
# ---------------------------------------------------------------------------

def list_users() -> dict:
    return dict(load()[0].get("users", {}))


def get_user(email: str) -> Optional[dict]:
    return load()[0].get("users", {}).get(normalize_email(email))


def has_user(email: str) -> bool:
    return normalize_email(email) in load()[0].get("users", {})


def _commit(mutator) -> dict:
    """Load → apply mutator(doc) → write with generation guard, retry once."""
    doc, gen = load(force=True)
    new_doc = mutator(doc)
    from google.api_core.exceptions import PreconditionFailed

    try:
        _write_object(_object_name(), new_doc, if_generation_match=gen)
    except PreconditionFailed:
        doc, gen = load(force=True)          # someone else wrote; re-apply on top
        new_doc = mutator(doc)
        _write_object(_object_name(), new_doc, if_generation_match=gen)
    invalidate_cache()
    return new_doc


def upsert_user(
    email: str,
    *,
    features: list[str],
    person: Optional[dict] = None,
    actor: str,
) -> dict:
    """Add or update a user's grants/person record. Returns the new doc."""
    return _commit(lambda d: apply_upsert(d, email=email, features=features,
                                          person=person, actor=actor))


def remove_user(email: str, *, actor: str) -> bool:
    """Remove a user. Returns True if they existed."""
    existed = {"v": False}

    def mut(d: dict) -> dict:
        nd, ex = apply_remove(d, email=email)
        existed["v"] = ex
        return nd

    _commit(mut)
    return existed["v"]


# ---------------------------------------------------------------------------
# Sensitive HR — skeleton only (admin-gated; not wired into the UI in v1)
# ---------------------------------------------------------------------------

def get_hr_private(email: str) -> Optional[dict]:
    doc, _ = _read_object(HR_OBJECT)
    return doc.get("users", {}).get(normalize_email(email))


def set_hr_private(email: str, *, fields: dict, actor: str) -> dict:
    def mut(doc: dict) -> dict:
        users = dict(doc.get("users", {}))
        rec = dict(users.get(normalize_email(email), {}))
        rec.update(fields)
        rec["updated_by"] = normalize_email(actor) or "unknown"
        rec["updated_at"] = _now_iso()
        users[normalize_email(email)] = rec
        return {"version": doc.get("version", DOC_VERSION), "users": users}

    doc, gen = _read_object(HR_OBJECT)
    new_doc = mut(doc)
    from google.api_core.exceptions import PreconditionFailed

    try:
        _write_object(HR_OBJECT, new_doc, if_generation_match=gen)
    except PreconditionFailed:
        doc, gen = _read_object(HR_OBJECT)
        new_doc = mut(doc)
        _write_object(HR_OBJECT, new_doc, if_generation_match=gen)
    return new_doc
