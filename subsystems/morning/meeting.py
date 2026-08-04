"""
Operations Huddle — live meeting state for the General Manager's run sheet
(docs/MORNING_BRIEF_BUILD_SPEC.md "Operations Huddle").
=========================================================================
One object, keyed by workdate, rather than a file per day
(`portal/morning/meetings/{workdate}.json`) — kept consistent with every
other Morning Brief store (prep/origins/routes/action-requests all use a
single object) and with the repo's stated rationale for GCS-JSON-object
state (portal_store.py's docstring: one small object is the fastest correct
option at GVC's scale). A meeting run is small; there is no benefit to a
blob-per-day here and it would mean five different sharding strategies
across one feature.

MeetingRun fields (spec's Minimum Data Objects): date, facilitator_email,
started_at, ended_at, ordered_item_ids, parking (topic/owner/follow_up),
actions (text/owner/due), scorecard.

"Every discussion must create a decision, action, risk response, or
coordination change. Otherwise assign an owner and follow-up time and park
it." — `add_parking` and `add_action` are the two possible outcomes of a
discussion; `end_run` closes with whatever scorecard the caller computed
(count of projects covered / actions assigned / parked items — the caller's
job, this module just stores it).

Storage — one object, default `portal/morning/meetings.json`:

    {
      "version": 1,
      "by_workdate": {
        "YYYY-MM-DD": {
          "date": "YYYY-MM-DD", "facilitator_email": "...",
          "started_at": "...", "ended_at": null,
          "ordered_item_ids": [123, 456],
          "parking": [{"topic": "...", "owner": "...", "follow_up": "..."}],
          "actions": [{"text": "...", "owner": "...", "due": "..."}],
          "scorecard": null
        }
      }
    }
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from shared import portal_store as portal_store
from subsystems.morning import store as morning_store

PortalStoreNotConfigured = morning_store.PortalStoreNotConfigured

DOC_VERSION = 1
DEFAULT_OBJECT = f"{morning_store.PREFIX}meetings.json"

_WORKDATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MeetingValidationError(ValueError):
    """Structurally invalid meeting input (caller maps to HTTP 422)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_email(email: str) -> str:
    return portal_store.normalize_email(email)


def _validate_workdate(workdate: str) -> str:
    if not isinstance(workdate, str) or not _WORKDATE_RE.match(workdate):
        raise MeetingValidationError(f"workdate must be YYYY-MM-DD, got {workdate!r}.")
    return workdate


def _empty_run(workdate: str) -> dict:
    return {"date": workdate, "facilitator_email": None, "started_at": None,
            "ended_at": None, "ordered_item_ids": [], "parking": [],
            "actions": [], "scorecard": None}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def apply_start(doc: dict, *, workdate: str, facilitator_email: str,
                 ordered_item_ids: Optional[list]) -> tuple[dict, dict]:
    """PURE. Start (or resume) the huddle for `workdate`. Idempotent — a
    second start on an already-started run keeps the original `started_at`
    but refreshes the facilitator and, when given, the item order (the GM's
    run sheet can be re-sequenced right up to the moment discussion starts).
    Returns (new_doc, run)."""
    by_workdate = dict(doc.get("by_workdate") or {})
    run = dict(by_workdate.get(workdate) or _empty_run(workdate))
    run["facilitator_email"] = _norm_email(facilitator_email)
    if not run.get("started_at"):
        run["started_at"] = _now_iso()
    if ordered_item_ids is not None:
        run["ordered_item_ids"] = list(ordered_item_ids)
    by_workdate[workdate] = run
    return {**doc, "version": DOC_VERSION, "by_workdate": by_workdate}, run


def apply_end(doc: dict, *, workdate: str, scorecard: Optional[dict]) -> tuple[dict, dict]:
    """PURE. Close out the huddle. Raises if it was never started."""
    by_workdate = dict(doc.get("by_workdate") or {})
    run = by_workdate.get(workdate)
    if run is None or not run.get("started_at"):
        raise MeetingValidationError("This workday's huddle was never started.")
    run = dict(run)
    run["ended_at"] = _now_iso()
    if scorecard is not None:
        run["scorecard"] = scorecard
    by_workdate[workdate] = run
    return {**doc, "version": DOC_VERSION, "by_workdate": by_workdate}, run


def apply_add_parking(doc: dict, *, workdate: str, topic: str,
                       owner: Optional[str], follow_up: Optional[str]) -> tuple[dict, dict]:
    """PURE. Park a topic that didn't resolve to a decision/action on the
    spot (spec: "assign an owner and follow-up time and park it")."""
    if not (topic or "").strip():
        raise MeetingValidationError("A parked topic needs a topic.")
    by_workdate = dict(doc.get("by_workdate") or {})
    run = dict(by_workdate.get(workdate) or _empty_run(workdate))
    parking = list(run.get("parking") or [])
    parking.append({"topic": topic.strip(), "owner": owner, "follow_up": follow_up})
    run["parking"] = parking
    by_workdate[workdate] = run
    return {**doc, "version": DOC_VERSION, "by_workdate": by_workdate}, run


def apply_add_action(doc: dict, *, workdate: str, text: str,
                      owner: Optional[str], due: Optional[str]) -> tuple[dict, dict]:
    """PURE. Record one assigned action out of the huddle."""
    if not (text or "").strip():
        raise MeetingValidationError("An action needs text.")
    by_workdate = dict(doc.get("by_workdate") or {})
    run = dict(by_workdate.get(workdate) or _empty_run(workdate))
    actions = list(run.get("actions") or [])
    actions.append({"text": text.strip(), "owner": owner, "due": due})
    run["actions"] = actions
    by_workdate[workdate] = run
    return {**doc, "version": DOC_VERSION, "by_workdate": by_workdate}, run


# ---------------------------------------------------------------------------
# Store-touching API
# ---------------------------------------------------------------------------

def _object_name() -> str:
    return os.environ.get("GVC_MORNING_MEETINGS_OBJECT") or DEFAULT_OBJECT


def get_run(workdate: str) -> dict:
    _validate_workdate(workdate)
    doc, _ = morning_store.read_doc(_object_name())
    return dict((doc.get("by_workdate") or {}).get(workdate) or _empty_run(workdate))


def start(workdate: str, facilitator_email: str, *,
          ordered_item_ids: Optional[list] = None) -> dict:
    """Start (or resume/re-sequence) the huddle for `workdate`."""
    _validate_workdate(workdate)

    def fn(doc: dict):
        return apply_start(doc, workdate=workdate, facilitator_email=facilitator_email,
                            ordered_item_ids=ordered_item_ids)

    return morning_store.mutate(_object_name(), fn)


def end(workdate: str, *, scorecard: Optional[dict] = None) -> dict:
    """Close out the huddle for `workdate`."""
    _validate_workdate(workdate)

    def fn(doc: dict):
        return apply_end(doc, workdate=workdate, scorecard=scorecard)

    return morning_store.mutate(_object_name(), fn)


def add_parking(workdate: str, *, topic: str, owner: Optional[str] = None,
                 follow_up: Optional[str] = None) -> dict:
    _validate_workdate(workdate)

    def fn(doc: dict):
        return apply_add_parking(doc, workdate=workdate, topic=topic,
                                  owner=owner, follow_up=follow_up)

    return morning_store.mutate(_object_name(), fn)


def add_action(workdate: str, *, text: str, owner: Optional[str] = None,
                due: Optional[str] = None) -> dict:
    _validate_workdate(workdate)

    def fn(doc: dict):
        return apply_add_action(doc, workdate=workdate, text=text, owner=owner, due=due)

    return morning_store.mutate(_object_name(), fn)
