"""
Durable Monday list snapshots in GCS (survives Cloud Run scale-to-zero).
=========================================================================
The in-process cache in adapters/monday/cache.py is L1 — fast, but empty on
every new Cloud Run instance. That made Job Start / Job Check / Morning Brief
feel slow after idle: the next person waited on a full Monday board walk.

This module is L2. Scheduler / hub warm writes JSON snapshots; cold instances
hydrate L1 from GCS in ~100ms and refresh Monday in the background.

Reuses the same bucket + service-account as portal_store (GVC_PORTAL_STATE_BUCKET
or GVC_GCS_PREVIEW_BUCKET). Soft-fails when GCS isn't configured so local tests
and unit runs keep working without credentials.

Env:
  GVC_MONDAY_SNAPSHOT_PREFIX   object prefix (default monday-cache/)
  GVC_MONDAY_SNAPSHOT_MAX_AGE  seconds a snapshot stays servable (default 7200)
"""
from __future__ import annotations

from shared import paths
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


class SnapshotNotConfigured(Exception):
    """GCS bucket / credentials missing — callers treat as cache miss."""


# Injected by tests (no real GCS).
_LOAD_HOOK: Optional[Callable[[str], Optional[tuple[Any, float]]]] = None
_SAVE_HOOK: Optional[Callable[[str, Any], None]] = None
_DELETE_HOOK: Optional[Callable[[str], None]] = None


def set_hooks(
    *,
    load: Optional[Callable[[str], Optional[tuple[Any, float]]]] = None,
    save: Optional[Callable[[str, Any], None]] = None,
    delete: Optional[Callable[[str], None]] = None,
) -> None:
    """Test helper: swap I/O. Pass None to clear a hook."""
    global _LOAD_HOOK, _SAVE_HOOK, _DELETE_HOOK
    _LOAD_HOOK = load
    _SAVE_HOOK = save
    _DELETE_HOOK = delete


def clear_hooks() -> None:
    set_hooks(load=None, save=None, delete=None)


def max_age() -> float:
    try:
        return float(os.environ.get("GVC_MONDAY_SNAPSHOT_MAX_AGE") or "7200")
    except ValueError:
        return 7200.0


def _prefix() -> str:
    raw = (os.environ.get("GVC_MONDAY_SNAPSHOT_PREFIX") or "monday-cache/").strip()
    if not raw.endswith("/"):
        raw += "/"
    return raw


def _safe_key(key: str) -> str:
    # Keep filesystem-friendly; keys look like list:jobstart:bids
    return re.sub(r"[^A-Za-z0-9._:-]+", "_", key)


def object_name(key: str) -> str:
    return f"{_prefix()}{_safe_key(key)}.json"


def _bucket_name() -> str:
    name = os.environ.get("GVC_PORTAL_STATE_BUCKET") or os.environ.get(
        "GVC_GCS_PREVIEW_BUCKET"
    )
    if not name:
        raise SnapshotNotConfigured(
            "Set GVC_PORTAL_STATE_BUCKET (or GVC_GCS_PREVIEW_BUCKET) for Monday snapshots."
        )
    return name


def _creds_path() -> Path:
    p = Path(
        os.environ.get("GVC_DRIVE_CREDENTIALS")
        or paths.DEFAULT_SA_PATH
    )
    if not p.exists():
        raise SnapshotNotConfigured(f"Service account JSON not found at {p}.")
    return p


def _blob(name: str):
    from google.cloud import storage
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(str(_creds_path()))
    client = storage.Client(credentials=creds, project=creds.project_id)
    return client.bucket(_bucket_name()).blob(name)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(key: str) -> Optional[tuple[Any, float]]:
    """
    Return (value, written_at_epoch) if a usable snapshot exists, else None.

    Snapshots older than max_age() are ignored (treated as miss) so we don't
    serve day-old board data forever if the warm job dies.
    """
    if _LOAD_HOOK is not None:
        return _LOAD_HOOK(key)

    try:
        blob = _blob(object_name(key))
        raw = blob.download_as_text()
    except SnapshotNotConfigured:
        return None
    except Exception as exc:  # noqa: BLE001 — L2 miss must never break requests
        # NotFound and network errors both become soft misses.
        if type(exc).__name__ == "NotFound":
            return None
        print(
            f"[monday:snapshot] load failed for {key}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return None

    try:
        doc = json.loads(raw) if raw and raw.strip() else {}
    except json.JSONDecodeError:
        print(f"[monday:snapshot] corrupt JSON for {key}", flush=True)
        return None

    written_at = doc.get("written_at_epoch")
    try:
        written_at_f = float(written_at)
    except (TypeError, ValueError):
        return None
    if (time.time() - written_at_f) > max_age():
        return None
    if "value" not in doc:
        return None
    return doc["value"], written_at_f


def save(key: str, value: Any) -> bool:
    """Persist value. Returns True on success. Soft-fails on GCS errors."""
    if _SAVE_HOOK is not None:
        _SAVE_HOOK(key, value)
        return True

    payload = {
        "key": key,
        "written_at": _now_iso(),
        "written_at_epoch": time.time(),
        "value": value,
    }
    try:
        blob = _blob(object_name(key))
        blob.upload_from_string(
            json.dumps(payload, separators=(",", ":"), default=str),
            content_type="application/json",
        )
        return True
    except SnapshotNotConfigured:
        return False
    except Exception as exc:  # noqa: BLE001
        print(
            f"[monday:snapshot] save failed for {key}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return False


def delete(key: str) -> None:
    """Best-effort delete (used on write-path invalidate)."""
    if _DELETE_HOOK is not None:
        _DELETE_HOOK(key)
        return
    try:
        blob = _blob(object_name(key))
        blob.delete()
    except SnapshotNotConfigured:
        return
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "NotFound":
            return
        print(
            f"[monday:snapshot] delete failed for {key}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


def delete_many(*keys: str) -> None:
    for key in keys:
        if key:
            delete(key)
