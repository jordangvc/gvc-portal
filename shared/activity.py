"""
Portal activity logging.
=========================================================================
A single helper that emits one structured JSON line per meaningful portal
action (sign-in, tool open, estimate/invoice finalize, admin grant change,
access denial). On Cloud Run, stdout is captured by Cloud Logging and JSON
lines are parsed into `jsonPayload`, so these become queryable events with
zero extra infrastructure (see docs/portal-access-and-people-architecture.md
§9).

Design rules:
- Log identifiers and actions, never secrets, request bodies, or HR-private
  values. `actor`/`target` should be emails or short identifiers.
- Never raise: logging must not break the request it's describing.
- One flat shape so downstream queries are simple.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional


def log_event(
    action: str,
    *,
    actor: Optional[str] = None,
    target: Optional[str] = None,
    result: str = "ok",
    severity: str = "INFO",
    **extra: Any,
) -> None:
    """
    Emit one structured activity event to stdout (Cloud Logging picks it up).

    `action`  — verb-ish identifier, e.g. "signin", "tool.open", "estimate.finalize",
                "admin.grant.update", "access.denied".
    `actor`   — who did it (email).
    `target`  — what it acted on (email, invoice id, feature name…).
    `result`  — "ok" | "denied" | "error" | …
    `severity`— Cloud Logging severity string (INFO/WARNING/ERROR).
    `extra`   — extra flat scalar fields; keep it free of secrets/PII bodies.
    """
    try:
        record = {
            "severity": severity,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "portal_activity",
            "action": action,
            "actor": actor,
            "target": target,
            "result": result,
        }
        for key, value in extra.items():
            # Keep the payload flat + JSON-safe; stringify anything exotic.
            record[key] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        print(json.dumps(record, default=str), file=sys.stdout, flush=True)
    except Exception:  # noqa: BLE001 — logging must never break the caller
        pass
