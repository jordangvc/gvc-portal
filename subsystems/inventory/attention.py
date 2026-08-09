"""Attention queue — inventory records that need a human. PURE doc ops.

Kinds (model.ATTENTION_KINDS): unknown_item, damage, negative_override,
low_stock, count_discrepancy, incomplete_kit, sync_failure, asset_lost.

Doc shape: {"schema":1, "items": {aid: {id, kind, status ("open"|
"acknowledged"|"resolved"), title, detail, payload, raised_by, raised_at,
assigned_to, resolved_by, resolved_at, resolution_note, txn_no}}}
"""
from __future__ import annotations

import copy

from subsystems.inventory.model import (
    ATTENTION_KINDS, new_uuid, now_iso, require,
)


def ensure_shape(doc: dict) -> dict:
    doc.setdefault("schema", 1)
    doc.setdefault("items", {})
    return doc


def raise_item(doc: dict, *, kind: str, title: str, detail: str = "",
               payload: dict | None = None, actor: str = "",
               txn_no: str = "", dedupe_key: str = "") -> tuple[dict, dict]:
    """Create an attention record. `dedupe_key` keeps recurring conditions
    (low stock on the same item+location) from stacking duplicates while
    one is still open."""
    require(kind in ATTENTION_KINDS, "INVALID_INPUT",
            f"kind must be one of {ATTENTION_KINDS}.", field="kind")
    if dedupe_key:
        for existing in (doc.get("items") or {}).values():
            if existing.get("dedupe_key") == dedupe_key \
                    and existing.get("status") != "resolved":
                return doc, existing  # identity → store no-op
    new = copy.deepcopy(ensure_shape(doc))
    aid = f"att-{new_uuid()[:10]}"
    rec = {"id": aid, "kind": kind, "status": "open",
           "title": (title or "").strip()[:200],
           "detail": (detail or "").strip()[:2000],
           "payload": payload or {}, "raised_by": (actor or "").lower(),
           "raised_at": now_iso(), "assigned_to": "", "resolved_by": "",
           "resolved_at": "", "resolution_note": "", "txn_no": txn_no,
           "dedupe_key": dedupe_key}
    new["items"][aid] = rec
    return new, rec


def update_status(doc: dict, aid: str, *, status: str, actor: str,
                  note: str = "", assigned_to: str = "") -> tuple[dict, dict]:
    require(status in ("open", "acknowledged", "resolved"), "INVALID_INPUT",
            "status must be open, acknowledged, or resolved.",
            field="status")
    new = copy.deepcopy(ensure_shape(doc))
    rec = new["items"].get(aid)
    require(rec is not None, "UNKNOWN_ATTENTION",
            f"Attention item '{aid}' does not exist.", field="attention_id")
    rec["status"] = status
    if assigned_to:
        rec["assigned_to"] = assigned_to.lower()
    if status == "resolved":
        require(bool(note.strip()), "INVALID_INPUT",
                "Resolving needs a short note (what was done).",
                field="note")
        rec["resolved_by"] = actor.lower()
        rec["resolved_at"] = now_iso()
        rec["resolution_note"] = note.strip()
    return new, rec


def open_items(doc: dict, *, kind: str = "") -> list[dict]:
    out = [r for r in (doc.get("items") or {}).values()
           if r.get("status") != "resolved"
           and (not kind or r.get("kind") == kind)]
    out.sort(key=lambda r: r.get("raised_at", ""), reverse=True)
    return out
