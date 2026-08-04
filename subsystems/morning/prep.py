"""
Morning Brief preparation — the six-criterion readiness score + scheduled-
workday miss streaks (docs/MORNING_BRIEF_BUILD_SPEC.md "Brief Generation and
Preparation").
=========================================================================
An employee is "prepared" when all six criteria below are satisfied:

    1. First stop selected or confirmed.               (first_stop)
    2. Today's planned work confirmed.                 (work)
    3. Needed materials/information reviewed.           (materials)
    4. Blockers answered, including an explicit "None". (blockers)
    5. Requests for other people submitted.              (requests)
    6. The brief was opened before 6:45 AM.               (opened)

Preparation cutoff is 6:45 AM America/New_York. Missing-preparation behavior
(spec, "Missing preparation behavior"):

    1 consecutive scheduled workday missed  -> private employee warning.
    3 consecutive scheduled workdays missed -> employee notice + Jordan/GM
                                                high-level visibility.
    5 consecutive scheduled workdays missed -> portal schedules a coaching
                                                call (GM + employee); Jordan
                                                gets the result as information.

"Consecutive" counts scheduled workdays only (Mon-Fri; holidays land later,
per the spec's own parenthetical) — a weekend never breaks a streak, and
never counts toward one either.

Storage — one object, default `portal/morning/prep.json`:

    {
      "version": 1,
      "by_user": {
        "email": {
          "workdates": {
            "YYYY-MM-DD": {
              "criteria": {"<criterion_id>": {"done": true, "at": "...", "meta": {}, "by": "..."}},
              "opened_at": "2026-08-04T10:41:00+00:00"
            }
          }
        }
      }
    }

`opened_at` is stored the FIRST time `record_brief_opened` is called for a
workdate, whenever that happens — "anytime for tracking" per the spec. The
`opened` CRITERION is marked done only when that timestamp falls at or
before the 6:45 AM cutoff for that workdate; opening late is still recorded
(so the employee's actual behavior is visible) but does not retroactively
satisfy criterion 6.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from shared import portal_store as portal_store
from subsystems.morning import store as morning_store

PortalStoreNotConfigured = morning_store.PortalStoreNotConfigured

DOC_VERSION = 1
DEFAULT_OBJECT = f"{morning_store.PREFIX}prep.json"

_ET = ZoneInfo("America/New_York")

# 6:45 AM America/New_York — the spec's preparation cutoff.
PREP_CUTOFF_HOUR = 6
PREP_CUTOFF_MINUTE = 45

_WORKDATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The six criteria, in the order the brief presents them. `label` is shown in
# the UI; ids are the stable keys this module and the UI both key off.
CRITERIA: tuple[dict, ...] = (
    {"id": "opened", "label": "Opened the brief"},
    {"id": "first_stop", "label": "First stop confirmed"},
    {"id": "work", "label": "Today's work confirmed"},
    {"id": "materials", "label": "Materials / info reviewed"},
    {"id": "blockers", "label": "Blockers answered"},
    {"id": "requests", "label": "Requests submitted"},
)
CRITERIA_IDS: tuple[str, ...] = tuple(c["id"] for c in CRITERIA)
CRITERIA_LABELS: dict[str, str] = {c["id"]: c["label"] for c in CRITERIA}


class PrepValidationError(ValueError):
    """Structurally invalid preparation input (caller maps to HTTP 422)."""


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-tested directly)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_email(email: str) -> str:
    return portal_store.normalize_email(email)


def _validate_workdate(workdate: str) -> str:
    if not isinstance(workdate, str) or not _WORKDATE_RE.match(workdate):
        raise PrepValidationError(f"workdate must be YYYY-MM-DD, got {workdate!r}.")
    return workdate


def _parse_workdate(workdate: str) -> date:
    _validate_workdate(workdate)
    return date.fromisoformat(workdate)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_scheduled_workday(d: Any) -> bool:
    """
    PURE. True for Monday-Friday. Holidays are a later slice (spec: "Mon-Fri
    (holidays later)") — this deliberately does not consult a calendar yet.

    Accepts a `date`/`datetime`, or a "YYYY-MM-DD" string.
    """
    if isinstance(d, str):
        d = _parse_workdate(d)
    elif isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        raise PrepValidationError(f"is_scheduled_workday expects a date, got {d!r}.")
    return d.weekday() < 5  # Mon=0 .. Fri=4


def _previous_day(d: date) -> date:
    return d - timedelta(days=1)


def is_before_cutoff(workdate: str, opened_at_iso: str) -> bool:
    """
    PURE. True when `opened_at_iso` falls on `workdate` (America/New_York)
    at or before 6:45 AM. An opened timestamp on a DIFFERENT calendar date
    (e.g. opened after midnight the next day, or backdated) never counts.
    """
    dt = _parse_iso(opened_at_iso)
    if dt is None:
        return False
    local = dt.astimezone(_ET)
    if local.date().isoformat() != workdate:
        return False
    return (local.hour, local.minute, local.second) <= (PREP_CUTOFF_HOUR, PREP_CUTOFF_MINUTE, 59)


def streak_alert_level(streak: int) -> int:
    """
    PURE. Map a consecutive-scheduled-workday miss streak to the spec's
    alert tiers: 0 = none, 1 = private warning, 3 = employee notice +
    Jordan/GM visibility, 5 = coaching call. Returned as an int so callers
    (Owner Pulse filtering, UI badges) can compare/threshold numerically.
    """
    if streak >= 5:
        return 5
    if streak >= 3:
        return 3
    if streak >= 1:
        return 1
    return 0


def consecutive_miss_streak(missed_workdates: Any, *, as_of: Optional[date] = None) -> int:
    """
    PURE. `missed_workdates` is every SCHEDULED workday (YYYY-MM-DD strings,
    any order) on which preparation was NOT completed by cutoff.

    Walk backward from `as_of` (default today, America/New_York) one
    calendar day at a time, skipping weekends (they neither extend nor
    break a streak). `as_of` itself is treated as "not yet decided" until
    it is actually found in the set — a scheduled workday not (yet) in
    `missed_workdates` is simply passed over on the way down, so an
    in-progress today (whose cutoff hasn't passed) never masks a real
    streak that ended yesterday. Once the walk reaches the first missed
    day, it starts counting, and breaks on the first scheduled workday
    after that which is NOT missed — that is the "consecutive" boundary.
    """
    as_of = as_of or datetime.now(_ET).date()
    missed = {str(w) for w in (missed_workdates or [])}
    streak = 0
    started = False
    d = as_of
    # Cap the walk so a corrupt/huge input can't spin forever.
    for _ in range(3660):  # ~10 years of calendar days
        if is_scheduled_workday(d):
            if d.isoformat() in missed:
                streak += 1
                started = True
            elif started:
                break
            # else: haven't hit the first miss yet — keep walking back.
        d = _previous_day(d)
    return streak


def _day_ready(day: dict, workdate: str) -> bool:
    """PURE. True when every one of the 6 criteria is satisfied for `day`."""
    criteria = (day or {}).get("criteria") or {}
    for c in CRITERIA:
        if c["id"] == "opened":
            opened_at = (day or {}).get("opened_at")
            if not (opened_at and is_before_cutoff(workdate, opened_at)):
                return False
        elif not (criteria.get(c["id"]) or {}).get("done"):
            return False
    return True


def score_day(day: dict, workdate: str) -> dict:
    """
    PURE. One workdate's stored record -> {ready, total, label, criteria[]}.
    Does not know about streaks — see `consecutive_miss_streak` for that.
    """
    criteria_state = (day or {}).get("criteria") or {}
    rows = []
    ready = 0
    for c in CRITERIA:
        rec = criteria_state.get(c["id"]) or {}
        if c["id"] == "opened":
            opened_at = (day or {}).get("opened_at")
            done = bool(opened_at) and is_before_cutoff(workdate, opened_at)
            at = opened_at
        else:
            done = bool(rec.get("done"))
            at = rec.get("at")
        if done:
            ready += 1
        rows.append({"id": c["id"], "label": c["label"], "done": done, "at": at})
    total = len(CRITERIA)
    return {"ready": ready, "total": total, "label": f"{ready} of {total} ready", "criteria": rows}


def apply_mark_criterion(doc: dict, *, email: str, workdate: str, criterion_id: str,
                          value: bool, meta: Optional[dict], actor: Optional[str]) -> tuple[dict, dict]:
    """PURE. Set one criterion for one user/workdate. Returns (new_doc, day)."""
    by_user = dict(doc.get("by_user") or {})
    user = dict(by_user.get(email) or {})
    workdates = dict(user.get("workdates") or {})
    day = dict(workdates.get(workdate) or {"criteria": {}, "opened_at": None})
    criteria = dict(day.get("criteria") or {})
    criteria[criterion_id] = {
        "done": bool(value),
        "at": _now_iso(),
        "meta": meta or {},
        "by": _norm_email(actor) if actor else None,
    }
    day["criteria"] = criteria
    workdates[workdate] = day
    user["workdates"] = workdates
    by_user[email] = user
    new_doc = {**doc, "version": DOC_VERSION, "by_user": by_user}
    return new_doc, day


def apply_record_opened(doc: dict, *, email: str, workdate: str, opened_at: str) -> tuple[dict, dict]:
    """PURE. First-open-wins: only sets opened_at if not already present for
    this workdate. Returns (new_doc, day)."""
    by_user = dict(doc.get("by_user") or {})
    user = dict(by_user.get(email) or {})
    workdates = dict(user.get("workdates") or {})
    day = dict(workdates.get(workdate) or {"criteria": {}, "opened_at": None})
    if not day.get("opened_at"):
        day["opened_at"] = opened_at
    workdates[workdate] = day
    user["workdates"] = workdates
    by_user[email] = user
    new_doc = {**doc, "version": DOC_VERSION, "by_user": by_user}
    return new_doc, day


# ---------------------------------------------------------------------------
# Store-touching API
# ---------------------------------------------------------------------------

def _object_name() -> str:
    import os
    return os.environ.get("GVC_MORNING_PREP_OBJECT") or DEFAULT_OBJECT


def mark_criterion(email: str, workdate: str, criterion_id: str, *,
                    value: bool = True, meta: Optional[dict] = None,
                    actor: Optional[str] = None) -> dict:
    """Mark one preparation criterion done/undone for `email` on `workdate`.
    Returns the stored day record. Raises PrepValidationError on a bad
    criterion id or workdate shape."""
    if criterion_id not in CRITERIA_IDS:
        raise PrepValidationError(f"Unknown preparation criterion {criterion_id!r}.")
    _validate_workdate(workdate)
    email_n = _norm_email(email)
    if not email_n:
        raise PrepValidationError("email is required.")

    def fn(doc: dict):
        return apply_mark_criterion(doc, email=email_n, workdate=workdate,
                                     criterion_id=criterion_id, value=value,
                                     meta=meta, actor=actor)

    return morning_store.mutate(_object_name(), fn)


def record_brief_opened(email: str, workdate: str, opened_at: Optional[str] = None) -> dict:
    """Record that `email` opened the brief for `workdate`. Always stores the
    timestamp (first call wins) regardless of cutoff — "anytime for
    tracking" per spec; whether it satisfies criterion 6 is computed later
    by `is_before_cutoff` / `get_preparation`, not decided here."""
    _validate_workdate(workdate)
    email_n = _norm_email(email)
    if not email_n:
        raise PrepValidationError("email is required.")
    at = opened_at or _now_iso()

    def fn(doc: dict):
        return apply_record_opened(doc, email=email_n, workdate=workdate, opened_at=at)

    return morning_store.mutate(_object_name(), fn)


def _missed_workdates_before(user: dict, *, before: str, now: datetime) -> list[str]:
    """
    Every scheduled workday from the user's EARLIEST stored workdate through
    `before` (inclusive, once its own cutoff has passed) that was not fully
    ready — including scheduled workdays that have NO stored record at all
    (an employee who never touched the brief that day is exactly the case
    the 5-day coaching-call tier exists for; a day absent from `workdates`
    is not "unknown", it's a miss). Weekends never appear. Bounded at the
    user's own earliest record so a brand-new hire's pre-employment history
    is never fabricated into a false streak.
    """
    workdates = user.get("workdates") or {}
    before_date = _parse_workdate(before)
    known_dates = []
    for wd in workdates:
        if _WORKDATE_RE.match(str(wd)):
            try:
                known_dates.append(date.fromisoformat(wd))
            except ValueError:
                continue
    if not known_dates:
        return []
    earliest = min(known_dates)

    missed: list[str] = []
    d = before_date
    for _ in range(3660):  # bounded walk, mirrors consecutive_miss_streak's cap
        if d < earliest:
            break
        if is_scheduled_workday(d):
            wd = d.isoformat()
            if d == before_date:
                # Only counts as "missed" once its own cutoff has passed.
                cutoff_dt = datetime.combine(
                    d, datetime.min.time(), tzinfo=_ET
                ).replace(hour=PREP_CUTOFF_HOUR, minute=PREP_CUTOFF_MINUTE)
                if now.astimezone(_ET) < cutoff_dt:
                    d = _previous_day(d)
                    continue
            day = workdates.get(wd) or {}
            if not _day_ready(day, wd):
                missed.append(wd)
        d = _previous_day(d)
    return missed


def get_preparation(email: str, workdate: str, *, now: Optional[datetime] = None) -> dict:
    """
    Full preparation payload for `email` on `workdate`:
    {ready, total, label, criteria[], streak, alerts[]}.

    `streak` is the consecutive-scheduled-workday miss streak ENDING at
    `workdate` (i.e. including workdate itself once its cutoff has passed).
    `alerts` is empty unless the streak has reached the 1/3/5 tiers.
    """
    _validate_workdate(workdate)
    email_n = _norm_email(email)
    now = now or datetime.now(timezone.utc)

    doc, _ = morning_store.read_doc(_object_name())
    user = (doc.get("by_user") or {}).get(email_n) or {}
    day = (user.get("workdates") or {}).get(workdate) or {}

    scored = score_day(day, workdate)
    missed = _missed_workdates_before(user, before=workdate, now=now)
    streak = consecutive_miss_streak(missed, as_of=_parse_workdate(workdate))
    level = streak_alert_level(streak)
    alerts = [] if level == 0 else [{"level": level, "streak": streak}]

    return {**scored, "streak": streak, "alerts": alerts}
