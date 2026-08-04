"""
Morning Brief state store — thin, generic GCS JSON-object helpers shared by
every subsystems/morning/*.py module.
=========================================================================
Why a shared helper instead of each module rolling its own `_read`/`_write`
(the pattern subsystems/estimate/drafts.py and subsystems/jobstart/drafts.py
each duplicate): the Morning Brief slice adds FIVE separate objects under
`portal/morning/` (prep, origins, routes, action-requests, meetings) in one
build. Rather than copy the same generation-guarded read/write/retry loop
five times, this module holds it once; each caller supplies its own object
name and its own document shape (this module knows nothing about what's
inside — same separation portal_store.py keeps from access.py).

Reuses portal_store's GCS plumbing (bucket / service-account / blob handle)
so there's one place that knows how to talk to the bucket.

Concurrency: `mutate()` loads the doc with its generation, applies the
caller's function, and writes with `if_generation_match`. On a precondition
failure (someone else wrote in between) it reloads and re-applies the
function once before giving up — the same one-retry contract every other
GCS-backed store in this repo uses.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

# Object-name prefix every morning subsystem module builds its own path under
# (e.g. f"{PREFIX}prep.json"). Kept here so the whole feature's GCS footprint
# is visible from one grep.
PREFIX = "portal/morning/"


def read_doc(object_name: str) -> tuple[dict, int]:
    """
    Return (doc, generation). A missing object is NOT an error — every
    morning subsystem starts empty until the first write — so this returns
    ({}, 0) rather than raising. Raises PortalStoreNotConfigured only when
    the store itself (bucket / service-account JSON) isn't configured, or
    when the object exists but isn't valid JSON (a corrupt object is never
    silently treated as empty).
    """
    from google.api_core.exceptions import NotFound

    blob = portal_store._blob(object_name)
    try:
        blob.reload()  # populate .generation
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
    """Write `doc` guarded by `generation` (0 == "create, must not exist yet").
    Returns the new generation. Raises google.api_core.exceptions.
    PreconditionFailed on a concurrent write — callers use `mutate()` below
    rather than calling this directly unless they want to handle that
    themselves."""
    blob = portal_store._blob(object_name)
    blob.upload_from_string(
        json.dumps(doc, indent=2, sort_keys=True),
        content_type="application/json",
        if_generation_match=generation,
    )
    blob.reload()
    return int(blob.generation or 0)


def mutate(object_name: str, fn: Callable[[dict], tuple[dict, Any]]) -> Any:
    """
    Load -> fn(doc) -> guarded write, retry once on precondition failure.

    `fn` receives the current doc and must return (new_doc, result). If
    `new_doc` is the SAME OBJECT as the doc it was handed (an identity
    check, not equality) the mutation is treated as a no-op and the blob is
    never written — the convention every drafts-style store in this repo
    uses to short-circuit a stale write without churning the object.
    """
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
