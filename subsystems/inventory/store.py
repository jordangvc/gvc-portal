"""Inventory state store — generation-guarded GCS JSON docs.

Same contract as subsystems/morning/store.py (read/write/mutate with one
retry on precondition failure); duplicated rather than imported because
subsystems must not reach into each other's internals. The whole feature's
GCS footprint is the OBJECTS table below.

Concurrency model (invariants 6/7/17): every posting path is a single
`mutate()` on LEDGER — events, balances, asset custody, kit state, and the
idempotency record all change inside one compare-and-swap, so a concurrent
writer can never interleave (it gets a 412 and re-applies on fresh state,
where the idempotency/stock checks re-run).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from shared import portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

PREFIX = "portal/inventory/"

CATALOG = PREFIX + "catalog.json"       # items / aliases / units / rules
LOCATIONS = PREFIX + "locations.json"   # location tree + scan tokens
LEDGER = PREFIX + "ledger.json"         # events + balances + assets + kits
COUNTS = PREFIX + "counts.json"         # count / audit sessions
ATTENTION = PREFIX + "attention.json"   # needs-review queue
IMPORTS = PREFIX + "imports.json"       # import job history


def read_doc(object_name: str) -> tuple[dict, int]:
    """(doc, generation); missing object → ({}, 0). Corrupt JSON raises —
    never silently treated as empty (the grants-wipe lesson)."""
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
        raise PortalStoreNotConfigured(
            f"{object_name} did not contain a JSON object.")
    return doc, int(blob.generation or 0)


def write_doc(object_name: str, doc: dict, *, generation: int) -> int:
    blob = portal_store._blob(object_name)
    blob.upload_from_string(
        json.dumps(doc, indent=1, sort_keys=True),
        content_type="application/json",
        if_generation_match=generation,
    )
    blob.reload()
    return int(blob.generation or 0)


def mutate(object_name: str, fn: Callable[[dict], tuple[dict, Any]]) -> Any:
    """Load → fn(doc) → guarded write; retry once on a concurrent write.
    fn returning the SAME doc object (identity) means no-op: nothing is
    written — how idempotent replays avoid churning the object."""
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
