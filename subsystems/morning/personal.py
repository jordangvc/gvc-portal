"""Private notes + Fireflies stub (proposals when API configured)."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from subsystems.morning import store as mstore

_ET = ZoneInfo("America/New_York")
NOTES_OBJECT = "portal/morning/notes.json"
FIREFLIES_OBJECT = "portal/morning/fireflies-proposals.json"


def get_notes(email: str) -> str:
    email = (email or "").strip().lower()
    try:
        doc, _ = mstore.read_doc(NOTES_OBJECT)
        return str(((doc.get("by_user") or {}).get(email) or {}).get("text") or "")
    except mstore.PortalStoreNotConfigured:
        return ""


def set_notes(email: str, text: str) -> dict:
    email = (email or "").strip().lower()
    text = (text or "")[:8000]

    def _mut(doc: dict):
        u = doc.setdefault("by_user", {}).setdefault(email, {})
        u["text"] = text
        u["updated_at"] = datetime.now(_ET).isoformat()
        return {"text": text, "updated_at": u["updated_at"]}

    try:
        return mstore.mutate(NOTES_OBJECT, _mut)
    except mstore.PortalStoreNotConfigured:
        return {"text": text, "updated_at": datetime.now(_ET).isoformat()}


def fireflies_configured() -> bool:
    return bool((os.environ.get("GVC_FIREFLIES_API_KEY") or "").strip())


def list_proposals(*, for_email: Optional[str] = None,
                   pending_only: bool = True) -> list[dict]:
    """GCS-stored proposals (ingest job writes here when Fireflies is wired)."""
    try:
        doc, _ = mstore.read_doc(FIREFLIES_OBJECT)
    except mstore.PortalStoreNotConfigured:
        return []
    rows = list((doc.get("proposals") or {}).values())
    if pending_only:
        rows = [r for r in rows if (r.get("status") or "pending") == "pending"]
    if for_email:
        em = for_email.strip().lower()
        rows = [r for r in rows
                if em in (r.get("relevant_emails") or [])
                or not r.get("relevant_emails")]
    return rows


def approve_proposal(proposal_id: str, *, actor_email: str) -> dict:
    def _mut(doc: dict):
        p = (doc.get("proposals") or {}).get(proposal_id)
        if not p:
            raise KeyError(proposal_id)
        p["status"] = "approved"
        p["approved_by"] = (actor_email or "").strip().lower()
        p["approved_at"] = datetime.now(_ET).isoformat()
        return dict(p)

    return mstore.mutate(FIREFLIES_OBJECT, _mut)
