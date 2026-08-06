"""
Hub pinned jobs — per-user list in the portal state bucket.
=========================================================================
Soft-fail everywhere: missing store / GCS errors return [] or no-op so the
home screen never blanks. Pure validate helpers are unit-tested.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

DOC_VERSION = 1
DEFAULT_OBJECT = "portal/hub-pins.json"
MAX_PINS = int(os.environ.get("GVC_HUB_PINS_MAX") or "20")
MAX_NAME = 200
MAX_SUB = 200
MAX_HREF = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _object_name() -> str:
    return (os.environ.get("GVC_HUB_PINS_OBJECT") or DEFAULT_OBJECT).strip()


def normalize_pin(raw: Any) -> Optional[dict]:
    """Return a clean pin dict or None if unusable."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:MAX_NAME]
    href = str(raw.get("href") or "").strip()[:MAX_HREF]
    if not name or not href or href == "#":
        return None
    sub = str(raw.get("sub") or "").strip()[:MAX_SUB]
    pid = str(raw.get("id") or href).strip()[:MAX_HREF]
    return {"id": pid, "name": name, "sub": sub, "href": href}


def validate_items(raw: Any) -> list[dict]:
    """Pure: coerce a list of pins, dedupe by id/href, cap MAX_PINS."""
    if not isinstance(raw, list):
        raise ValueError("items must be a list")
    out: list[dict] = []
    seen: set[str] = set()
    for row in raw:
        pin = normalize_pin(row)
        if not pin:
            continue
        key = pin["id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(pin)
        if len(out) >= MAX_PINS:
            break
    return out


def _empty_doc() -> dict:
    return {"version": DOC_VERSION, "users": {}}


def list_for(email: str) -> list[dict]:
    """Best-effort read. Never raises."""
    email = portal_store.normalize_email(email)
    if not email:
        return []
    try:
        doc, _ = portal_store._read_object(_object_name())  # noqa: SLF001
    except PortalStoreNotConfigured:
        return []
    except Exception as exc:  # noqa: BLE001
        print(f"[hub.pins] list skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []
    users = doc.get("users") or {}
    rec = users.get(email) or {}
    items = rec.get("items") if isinstance(rec, dict) else []
    try:
        return validate_items(items or [])
    except ValueError:
        return []


def put_for(email: str, items: Any, *, actor: str) -> tuple[list[dict], bool]:
    """
    Replace the user's pin list. Returns (saved_list, persisted).
    Raises ValueError on bad input; soft-fails store errors → returns validated
    list with persisted=False (optimistic UI can still show them in-session).
    """
    email = portal_store.normalize_email(email)
    actor = portal_store.normalize_email(actor) or email
    clean = validate_items(items)
    if not email:
        return clean, False

    def mut(doc: dict) -> dict:
        users = dict(doc.get("users") or {})
        users[email] = {
            "items": clean,
            "updated_at": _now_iso(),
            "updated_by": actor,
        }
        return {"version": doc.get("version", DOC_VERSION), "users": users}

    try:
        doc, gen = portal_store._read_object(_object_name())  # noqa: SLF001
        if not doc:
            doc = _empty_doc()
        new_doc = mut(doc)
        from google.api_core.exceptions import PreconditionFailed

        try:
            portal_store._write_object(  # noqa: SLF001
                _object_name(), new_doc, if_generation_match=gen)
        except PreconditionFailed:
            doc, gen = portal_store._read_object(_object_name())  # noqa: SLF001
            new_doc = mut(doc or _empty_doc())
            portal_store._write_object(  # noqa: SLF001
                _object_name(), new_doc, if_generation_match=gen)
        return clean, True
    except PortalStoreNotConfigured:
        return clean, False
    except Exception as exc:  # noqa: BLE001
        print(f"[hub.pins] put soft-fail: {type(exc).__name__}: {exc}", file=sys.stderr)
        return clean, False
