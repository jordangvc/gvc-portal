"""
Lien alert sent-markers — dedup so T-14/7/3/1 pings fire once per mark.
=========================================================================
Keys include item + deadline kind + mark (days_remaining) + due_date so a
corrected clock (new due date) can ping again, while a same-day re-run of
send_lien_alerts() is silent.

GCS object (state bucket): portal/lien-alert-sent.json
  { "version": 1, "sent": { "<key>": "ISO-8601 …" } }

Soft-fail when the portal store isn't configured: fall back to an
in-process map (same Cloud Run instance won't double-ping; cold starts
might). Real multi-instance dedup needs the state bucket — same rule as
grants / jobstart drafts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

DOC_VERSION = 1
DEFAULT_OBJECT = "portal/lien-alert-sent.json"
# Keep markers long enough to cover every statutory window we track.
MAX_AGE_DAYS = int(os.environ.get("GVC_LIEN_ALERT_SENT_MAX_AGE_DAYS") or "180")

# Process-local fallback when GCS isn't wired (dev / misconfigured prod).
_MEMORY: dict[str, str] = {}


def empty_doc() -> dict:
    return {"version": DOC_VERSION, "sent": {}}


def alert_key(item_id: Any, kind: str, days_remaining: Any,
              due_date: Any = None) -> str:
    """Stable id for one (job, deadline kind, T-mark, due date) ping."""
    due = (str(due_date or "").strip()[:10]) or "-"
    return f"{item_id}:{kind}:{days_remaining}:{due}"


def prune_sent(doc: dict, *, now: Optional[datetime] = None,
               max_age_days: int = MAX_AGE_DAYS) -> dict:
    """PURE. Drop markers older than max_age_days."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    sent = dict(doc.get("sent") or {})
    kept: dict[str, str] = {}
    for key, when in sent.items():
        raw = (when or "").strip()
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            kept[key] = when
    return {"version": DOC_VERSION, "sent": kept}


def already_sent(doc: dict, key: str) -> bool:
    return bool((doc.get("sent") or {}).get(key))


def with_sent(doc: dict, key: str, *, when: Optional[str] = None) -> dict:
    """PURE. Return a new doc with key marked sent (ISO timestamp)."""
    when = when or datetime.now(timezone.utc).isoformat()
    sent = dict(doc.get("sent") or {})
    sent[key] = when
    return prune_sent({"version": DOC_VERSION, "sent": sent})


def _object_name() -> str:
    return os.environ.get("GVC_LIEN_ALERT_SENT_OBJECT") or DEFAULT_OBJECT


def _read() -> tuple[dict, int, str]:
    """Return (doc, generation, backend). backend is 'gcs' | 'memory'."""
    try:
        from google.api_core.exceptions import NotFound
        blob = portal_store._blob(_object_name())
        try:
            blob.reload()
            raw = blob.download_as_text()
        except NotFound:
            return empty_doc(), 0, "gcs"
        try:
            doc = json.loads(raw) if raw.strip() else empty_doc()
        except json.JSONDecodeError:
            raise PortalStoreNotConfigured(
                f"{_object_name()} is not valid JSON.")
        doc.setdefault("sent", {})
        return prune_sent(doc), int(blob.generation or 0), "gcs"
    except Exception:  # noqa: BLE001 — store missing / ADC / network
        return {"version": DOC_VERSION, "sent": dict(_MEMORY)}, 0, "memory"


def _write(doc: dict, *, if_generation_match: int, backend: str) -> None:
    if backend == "memory":
        _MEMORY.clear()
        _MEMORY.update(doc.get("sent") or {})
        return
    blob = portal_store._blob(_object_name())
    blob.upload_from_string(
        json.dumps(doc, indent=2, sort_keys=True),
        content_type="application/json",
        if_generation_match=if_generation_match,
    )


def load_sent_doc() -> tuple[dict, str]:
    """Public: (doc, backend) for a sweep. Never raises."""
    doc, _gen, backend = _read()
    return doc, backend


def remember_sent(key: str) -> str:
    """Mark one key sent. Returns backend used. Best-effort, never raises."""
    try:
        from google.api_core.exceptions import PreconditionFailed
    except Exception:  # noqa: BLE001
        PreconditionFailed = Exception  # type: ignore[misc,assignment]

    try:
        doc, gen, backend = _read()
        if already_sent(doc, key):
            return backend
        new_doc = with_sent(doc, key)
        try:
            _write(new_doc, if_generation_match=gen, backend=backend)
        except PreconditionFailed:
            doc, gen, backend = _read()
            if not already_sent(doc, key):
                _write(with_sent(doc, key), if_generation_match=gen,
                       backend=backend)
        return backend
    except Exception:  # noqa: BLE001
        _MEMORY[key] = datetime.now(timezone.utc).isoformat()
        return "memory"
