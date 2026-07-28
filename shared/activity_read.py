"""
Portal activity READ layer.
=========================================================================
The companion to activity.py. activity.py emits one structured JSON line per
portal action to stdout, which Cloud Run captures into Cloud Logging as
`jsonPayload`. This module reads those events BACK for the in-app admin
"Activity" view (docs/portal-access-and-people-architecture.md §9).

Source of truth stays Cloud Logging — we do NOT dual-write to GCS. We query
`jsonPayload.event="portal_activity"` with optional actor/action/result/time
filters and page through results.

Permissions: the service's service account (the same SA JSON used by
portal_store for GCS) needs `roles/logging.viewer` on the project. Without it
the Cloud Logging API returns PermissionDenied, which we surface as a clean
ActivityReadNotConfigured so the UI can show an actionable message.

Design rules mirror activity.py:
- The network call is isolated in fetch_events(); everything else is pure and
  unit-tested (filter building, range parsing, entry normalization, CSV).
- Never leak secrets; we only read back the flat fields activity.py wrote.
"""
from __future__ import annotations

from shared import paths
import csv
import io
import itertools
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

EVENT_MARKER = "portal_activity"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# Safety ceiling for a single month export so a runaway query can't pull an
# unbounded result set into memory. A month of portal activity is far below
# this; if we ever hit it the export is flagged truncated rather than silently
# dropping data.
MAX_EXPORT_EVENTS = 100_000

# Time-range presets the UI exposes → lookback window. "all" = no lower bound.
RANGE_WINDOWS: dict[str, Optional[timedelta]] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "all": None,
}

# Columns for CSV export + the canonical normalized order.
CORE_FIELDS = ("ts", "action", "actor", "target", "result", "severity")


class ActivityReadNotConfigured(RuntimeError):
    """Raised when the Cloud Logging dep, credentials, or IAM perms are missing."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested — no network)
# ---------------------------------------------------------------------------

def clamp_page_size(value: Any) -> int:
    """Coerce a requested page size into [1, MAX_PAGE_SIZE], default on junk."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if n < 1:
        return DEFAULT_PAGE_SIZE
    return min(n, MAX_PAGE_SIZE)


def range_start(range_key: Optional[str], *, now: Optional[datetime] = None) -> Optional[datetime]:
    """
    Map a range preset to a UTC lower-bound timestamp. Unknown keys fall back to
    the 7d default. "all" returns None (no lower bound).
    """
    now = now or datetime.now(timezone.utc)
    if range_key not in RANGE_WINDOWS:
        range_key = "7d"
    window = RANGE_WINDOWS[range_key]
    if window is None:
        return None
    return now - window


def _escape(value: str) -> str:
    r"""Escape a value for use inside a double-quoted Cloud Logging filter literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_filter(
    *,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    service_name: Optional[str] = None,
) -> str:
    """
    Build the Cloud Logging advanced-filter string. Always scopes to our portal
    activity events on the Cloud Run service; appends any supplied filters.

    `start` is an inclusive lower bound (timestamp>=); `end` is an exclusive
    upper bound (timestamp<) so adjacent month windows never double-count an
    event on the boundary.
    """
    clauses = [
        'resource.type="cloud_run_revision"',
        f'jsonPayload.event="{EVENT_MARKER}"',
    ]
    if service_name:
        clauses.append(f'resource.labels.service_name="{_escape(service_name)}"')
    if actor:
        clauses.append(f'jsonPayload.actor="{_escape(actor.strip())}"')
    if action:
        clauses.append(f'jsonPayload.action="{_escape(action.strip())}"')
    if result:
        clauses.append(f'jsonPayload.result="{_escape(result.strip())}"')
    if start is not None:
        # RFC3339 / Zulu; Cloud Logging compares timestamps lexically-safe in UTC.
        clauses.append(f'timestamp>="{start.astimezone(timezone.utc).isoformat()}"')
    if end is not None:
        clauses.append(f'timestamp<"{end.astimezone(timezone.utc).isoformat()}"')
    return " AND ".join(clauses)


def normalize_payload(
    payload: dict,
    *,
    timestamp: Optional[datetime] = None,
    severity: Optional[str] = None,
) -> dict:
    """
    Flatten one Cloud Logging jsonPayload (what activity.log_event wrote) into a
    stable event dict for the UI: the core fields plus an `extra` dict holding any
    additional flat fields the action attached. Resilient to missing keys.
    """
    payload = payload or {}
    ts = payload.get("ts")
    if not ts and timestamp is not None:
        ts = timestamp.astimezone(timezone.utc).isoformat()
    event = {
        "ts": ts,
        "action": payload.get("action"),
        "actor": payload.get("actor"),
        "target": payload.get("target"),
        "result": payload.get("result"),
        "severity": payload.get("severity") or severity,
    }
    skip = set(CORE_FIELDS) | {"event"}
    extra = {k: v for k, v in payload.items() if k not in skip}
    event["extra"] = extra
    return event


# Business fields promoted to their own CSV columns so the export opens in Excel
# as a usable report (sort by customer, total the amounts) instead of one opaque
# JSON blob. Written by shared/activity_detail.summarize(); older events simply
# leave them blank. `extra` still carries EVERYTHING, so nothing is lost.
REPORT_FIELDS = ("customer", "job", "amount", "sent_to", "cc", "due", "mode",
                 "gmail", "drive", "monday", "stripe_invoice_id", "error")


def to_csv(events: list[dict]) -> str:
    """Render normalized events as CSV: core + business columns, then the raw extras."""
    import json

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([*CORE_FIELDS, *REPORT_FIELDS, "extra"])
    for ev in events:
        extra = ev.get("extra") or {}
        writer.writerow([
            ev.get("ts") or "",
            ev.get("action") or "",
            ev.get("actor") or "",
            ev.get("target") or "",
            ev.get("result") or "",
            ev.get("severity") or "",
            *[extra.get(f, "") if extra.get(f) is not None else "" for f in REPORT_FIELDS],
            json.dumps(extra, default=str) if extra else "",
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Monthly backup helpers (pure — unit-tested)
# ---------------------------------------------------------------------------

def month_bounds(month_key: str) -> tuple[datetime, datetime]:
    """
    Map a 'YYYY-MM' string to the UTC [start, end) datetimes spanning that
    calendar month (end is the first instant of the next month, exclusive).
    Raises ValueError on anything that isn't a real year-month.
    """
    parts = str(month_key).strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"month_key must be 'YYYY-MM', got {month_key!r}")
    year, mon = int(parts[0]), int(parts[1])
    if not (1 <= mon <= 12):
        raise ValueError(f"month out of range in {month_key!r}")
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    if mon == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, mon + 1, 1, tzinfo=timezone.utc)
    return start, end


def previous_month_key(now: Optional[datetime] = None) -> str:
    """Return the 'YYYY-MM' of the month before `now` (UTC). Default target for
    the monthly backup job, which runs on the 1st and archives the month that
    just closed."""
    now = now or datetime.now(timezone.utc)
    year, mon = now.year, now.month
    if mon == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{mon - 1:02d}"


def month_file_stub(month_key: str) -> str:
    """'YYYY-MM' -> 'MMYYYY' (e.g. '2026-06' -> '062026') for the backup filename."""
    start, _ = month_bounds(month_key)  # validates
    return f"{start.month:02d}{start.year:04d}"


def to_json(
    events: list[dict],
    *,
    month_key: Optional[str] = None,
    generated_at: Optional[datetime] = None,
) -> str:
    """Render an export document: metadata envelope + the lossless event list."""
    import json

    gen = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    doc = {
        "export": "portal_activity",
        "month": month_key,
        "generated_at": gen.isoformat(),
        "count": len(events),
        "events": events,
    }
    return json.dumps(doc, indent=2, default=str)


# ---------------------------------------------------------------------------
# Credentials (reuse the portal SA JSON, same resolution as portal_store)
# ---------------------------------------------------------------------------

def _creds_path() -> Path:
    p = Path(
        os.environ.get("GVC_DRIVE_CREDENTIALS")
        or paths.DEFAULT_SA_PATH
    )
    if not p.exists():
        raise ActivityReadNotConfigured(
            f"Service account JSON not found at {p} "
            "(set GVC_DRIVE_CREDENTIALS or place .google-service-account.json)."
        )
    return p


def _service_name() -> str:
    return os.environ.get("GVC_SERVICE_NAME") or "gvc-invoice"


# ---------------------------------------------------------------------------
# Network call (isolated, not unit-tested)
# ---------------------------------------------------------------------------

def fetch_events(
    *,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    range_key: Optional[str] = "7d",
    page_size: int = DEFAULT_PAGE_SIZE,
    page_token: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """
    Query Cloud Logging for portal activity events, newest first.

    Returns {"events": [normalized...], "next_page_token": str|None, "filter": str}.
    Raises ActivityReadNotConfigured if the dependency, credentials, or IAM
    permission (roles/logging.viewer) is missing.
    """
    page_size = clamp_page_size(page_size)
    start = range_start(range_key, now=now)
    filt = build_filter(
        actor=actor, action=action, result=result,
        start=start, service_name=_service_name(),
    )

    try:
        import google.cloud.logging as gcloud_logging
        from google.cloud.logging_v2 import DESCENDING
    except Exception as e:  # noqa: BLE001 — dep not installed
        raise ActivityReadNotConfigured(
            "google-cloud-logging is not installed in this image."
        ) from e

    try:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(str(_creds_path()))
        client = gcloud_logging.Client(credentials=creds, project=creds.project_id)
    except ActivityReadNotConfigured:
        raise
    except Exception as e:  # noqa: BLE001
        raise ActivityReadNotConfigured(f"Could not build the Cloud Logging client: {e}") from e

    try:
        iterator = client.list_entries(
            filter_=filt,
            order_by=DESCENDING,
            page_size=page_size,
            page_token=page_token or None,
        )
        # google-cloud-logging's Client.list_entries returns a google-api-core
        # page iterator on some versions (exposes .pages / .next_page_token) but a
        # plain generator on others (the gapic-backed 3.x path — what's deployed,
        # and why `.pages` raised "'generator' object has no attribute 'pages'").
        # Handle both: prefer the page iterator (keeps token-based "Load more");
        # otherwise take one page_size slice off the generator so the view loads.
        pages = getattr(iterator, "pages", None)
        if pages is not None:
            entries = list(next(pages, []))
        else:
            entries = list(itertools.islice(iterator, page_size))
        next_token = getattr(iterator, "next_page_token", None) or None
    except Exception as e:  # noqa: BLE001 — most commonly PermissionDenied
        msg = str(e)
        if "permission" in msg.lower() or "denied" in msg.lower():
            raise ActivityReadNotConfigured(
                "The service account lacks roles/logging.viewer on the project. "
                "Grant it (see deploy notes) and reload."
            ) from e
        raise ActivityReadNotConfigured(f"Cloud Logging query failed: {e}") from e

    events = []
    for entry in entries:
        payload = getattr(entry, "payload", None)
        if not isinstance(payload, dict):
            continue
        events.append(normalize_payload(
            payload,
            timestamp=getattr(entry, "timestamp", None),
            severity=str(getattr(entry, "severity", "") or "") or None,
        ))
    return {"events": events, "next_page_token": next_token, "filter": filt}


def fetch_all_in_range(
    start: datetime,
    end: datetime,
    *,
    service_name: Optional[str] = None,
    max_events: int = MAX_EXPORT_EVENTS,
) -> dict:
    """
    Page through EVERY portal_activity event in [start, end), oldest-first, for
    the monthly backup. Unlike fetch_events (one UI page, newest-first) this
    walks the whole iterator and accumulates, capped at max_events.

    Returns {"events": [normalized...], "filter": str, "truncated": bool}.
    Raises ActivityReadNotConfigured if the dep, credentials, or IAM permission
    (roles/logging.viewer) is missing.
    """
    filt = build_filter(start=start, end=end, service_name=service_name or _service_name())

    try:
        import google.cloud.logging as gcloud_logging
        from google.cloud.logging_v2 import ASCENDING
    except Exception as e:  # noqa: BLE001 — dep not installed
        raise ActivityReadNotConfigured(
            "google-cloud-logging is not installed in this image."
        ) from e

    try:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(str(_creds_path()))
        client = gcloud_logging.Client(credentials=creds, project=creds.project_id)
    except ActivityReadNotConfigured:
        raise
    except Exception as e:  # noqa: BLE001
        raise ActivityReadNotConfigured(f"Could not build the Cloud Logging client: {e}") from e

    events: list[dict] = []
    truncated = False
    try:
        # Iterating the entries object transparently follows page tokens.
        iterator = client.list_entries(
            filter_=filt, order_by=ASCENDING, page_size=MAX_PAGE_SIZE
        )
        for entry in iterator:
            payload = getattr(entry, "payload", None)
            if not isinstance(payload, dict):
                continue
            events.append(normalize_payload(
                payload,
                timestamp=getattr(entry, "timestamp", None),
                severity=str(getattr(entry, "severity", "") or "") or None,
            ))
            if len(events) >= max_events:
                truncated = True
                break
    except Exception as e:  # noqa: BLE001 — most commonly PermissionDenied
        msg = str(e)
        if "permission" in msg.lower() or "denied" in msg.lower():
            raise ActivityReadNotConfigured(
                "The service account lacks roles/logging.viewer on the project. "
                "Grant it (see deploy notes) and reload."
            ) from e
        raise ActivityReadNotConfigured(f"Cloud Logging query failed: {e}") from e

    return {"events": events, "filter": filt, "truncated": truncated}
