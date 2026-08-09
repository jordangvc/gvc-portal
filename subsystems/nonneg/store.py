"""Non-negotiables GCS store — one JSON doc per owner email.

Same thin generation-guarded read/mutate contract as the other doc stores
(subsystems/morning/store.py documents the pattern; layering keeps each
subsystem's copy local rather than importing another app's internals).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from shared import portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

PREFIX = "portal/nonneg/"


def object_for(email: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", (email or "").strip().lower())
    return f"{PREFIX}{slug}.json"


def read_doc(object_name: str) -> tuple[dict, int]:
    """(doc, generation); missing object -> ({}, 0), corrupt object raises."""
    from google.api_core.exceptions import NotFound

    blob = portal_store._blob(object_name)
    try:
        blob.reload()
        raw = blob.download_as_text()
    except NotFound:
        return {}, 0
    try:
        doc = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        raise PortalStoreNotConfigured(f"{object_name} is not valid JSON.")
    if not isinstance(doc, dict):
        raise PortalStoreNotConfigured(f"{object_name} did not contain a JSON object.")
    return doc, int(blob.generation or 0)


def write_doc(object_name: str, doc: dict, *, generation: int) -> int:
    blob = portal_store._blob(object_name)
    blob.upload_from_string(
        json.dumps(doc, indent=2, sort_keys=True),
        content_type="application/json",
        if_generation_match=generation,
    )
    blob.reload()
    return int(blob.generation or 0)


def mutate(object_name: str, fn: Callable[[dict], tuple[dict, Any]]) -> Any:
    """Load -> fn(doc) -> guarded write; one retry on a concurrent write.
    fn returning the SAME object it was handed means no-op (never written)."""
    from google.api_core.exceptions import PreconditionFailed

    doc, gen = read_doc(object_name)
    new_doc, result = fn(doc)
    if new_doc is doc:
        return result
    try:
        write_doc(object_name, new_doc, generation=gen)
    except PreconditionFailed:
        doc, gen = read_doc(object_name)
        new_doc, result = fn(doc)
        if new_doc is not doc:
            write_doc(object_name, new_doc, generation=gen)
    return result
