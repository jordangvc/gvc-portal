"""Stage Takeoff exports as shared portal estimate drafts.

This flow has exactly one side effect: an estimate-draft upsert.  It never
renders, finalizes, writes Monday, creates Gmail, or posts Slack.
"""
from __future__ import annotations

from typing import Any

from subsystems.estimate import drafts as estimate_drafts
from subsystems.estimate import takeoff_import


def import_takeoff_as_draft(raw: Any, actor: str) -> dict:
    """Normalize, validate, and persist one Takeoff estimate draft."""
    data = takeoff_import.normalize_takeoff_payload(raw)
    errors = takeoff_import.validate_takeoff_payload(data)
    if errors:
        raise takeoff_import.TakeoffPayloadInvalid(errors)

    pending = takeoff_import.build_draft_record(data, actor)
    warnings = takeoff_import.normalization_warnings(raw, data)
    try:
        stored, stale = estimate_drafts.upsert_draft(
            pending["id"],
            label=pending["label"],
            payload=pending["payload"],
            updated_at=pending["updated_at"],
            actor=pending["actor"],
        )
    except estimate_drafts.PortalStoreNotConfigured as exc:
        return {
            "ok": False,
            "code": "STORE_NOT_CONFIGURED",
            "detail": str(exc),
            "advice": (
                "Ask an admin to configure GVC_PORTAL_STATE_BUCKET and the "
                "portal service-account credentials."
            ),
            "draft": None,
            "warnings": warnings,
        }

    if stale:
        warnings.append(
            "The draft store kept a newer copy instead of this import."
        )
    return {"ok": True, "draft": stored, "warnings": warnings}
