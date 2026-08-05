"""
Pure Job Start metrics from activity events.
================================================================
First-pass acceptance rate and send-back notes are already in the
activity log (jobstart.sent_to_ops / sent_back / accepted); this module
reads them. No I/O — safe to call from tests or a future API route.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

_JOBSTART_PREFIX = "jobstart."


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _jobstart_events(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        action = str(ev.get("action") or "")
        if action.startswith(_JOBSTART_PREFIX):
            out.append(ev)
    return out


def _extra(ev: dict) -> dict:
    extra = ev.get("extra")
    return extra if isinstance(extra, dict) else {}


def first_pass_stats(events: list[dict]) -> dict:
    """
    Summarize Job Start handoff quality for a window of activity events.

    Groups lifecycle events by ``target`` (bid item id). An acceptance counts
    as first-pass when no ``jobstart.sent_back`` occurred earlier for that bid.

    Returns counts, first_pass_rate (0–1 or None), optional average hours from
    first send-to-ops to acceptance, and recent send-back notes (newest first).
    """
    js_events = _jobstart_events(events)

    sent_count = 0
    sent_back_count = 0
    accepted_count = 0

    by_bid: dict[str, list[tuple[Optional[datetime], str, dict]]] = {}

    for ev in js_events:
        action = str(ev.get("action") or "")
        target = str(ev.get("target") or "").strip()
        if not target:
            continue
        ts = _parse_ts(ev.get("ts"))
        by_bid.setdefault(target, []).append((ts, action, ev))

        if action == "jobstart.sent_to_ops":
            sent_count += 1
        elif action == "jobstart.sent_back":
            sent_back_count += 1
        elif action == "jobstart.accepted":
            accepted_count += 1

    for timeline in by_bid.values():
        timeline.sort(key=lambda row: (row[0] is None, row[0] or datetime.min.replace(tzinfo=timezone.utc)))

    first_pass_accepted = 0
    send_to_accept_hours: list[float] = []

    for _bid, timeline in by_bid.items():
        first_sent_ts: Optional[datetime] = None
        for idx, (ts, action, _ev) in enumerate(timeline):
            if action == "jobstart.sent_to_ops" and first_sent_ts is None:
                first_sent_ts = ts
            if action != "jobstart.accepted":
                continue
            prior = timeline[:idx]
            if not any(a == "jobstart.sent_back" for _, a, _ in prior):
                first_pass_accepted += 1
            if first_sent_ts and ts:
                hours = (ts - first_sent_ts).total_seconds() / 3600.0
                if hours >= 0:
                    send_to_accept_hours.append(hours)

    first_pass_rate: Optional[float] = None
    if accepted_count:
        first_pass_rate = first_pass_accepted / accepted_count

    avg_send_to_accept_hours: Optional[float] = None
    if send_to_accept_hours:
        avg_send_to_accept_hours = sum(send_to_accept_hours) / len(send_to_accept_hours)

    recent_send_back_notes: list[dict] = []
    for ev in js_events:
        if ev.get("action") != "jobstart.sent_back":
            continue
        extra = _extra(ev)
        recent_send_back_notes.append({
            "bid": str(ev.get("target") or ""),
            "note": str(extra.get("note") or ""),
            "actor": str(ev.get("actor") or ""),
            "at": str(ev.get("ts") or ""),
            "job": str(extra.get("job") or ""),
        })
    recent_send_back_notes.sort(key=lambda row: row["at"], reverse=True)

    return {
        "sent_count": sent_count,
        "accepted_count": accepted_count,
        "sent_back_count": sent_back_count,
        "first_pass_accepted": first_pass_accepted,
        "first_pass_rate": first_pass_rate,
        "avg_send_to_accept_hours": avg_send_to_accept_hours,
        "recent_send_back_notes": recent_send_back_notes[:10],
    }
