"""
Morning Brief route service — private starting locations + the daily editable
stop list (docs/MORNING_BRIEF_BUILD_SPEC.md "Stop List and Route Planning").
=========================================================================
Two separate GCS objects, because they have different privacy and retention
shapes:

  portal/morning/origins.json  — ONE record per employee, no history. "Home/
      start locations are private and must never be written to Monday. Only
      the employee and narrowly authorized management can access stored
      home-start data." This module never enforces WHO may call get_origin —
      that's the API layer's job (require the caller to be the employee
      themself or a role check via shared.access.morning_role); this module
      simply never returns more than the one email it was asked for.

  portal/morning/routes.json    — per-employee, per-workday. Stops the
      employee can reorder/complete; a boolean "overridden" flag per day
      tracks whether the employee's final order differs from what was
      offered, which is the spec's planning signal ("Three overrides within
      ten scheduled workdays creates a private General Manager planning
      alert") — this module only computes and returns that signal; sending
      the alert is the caller's job (Slack notice, later slice).

Origin kinds: home | office | current | custom. "Office" defaults to Green
Valley's own address when an employee hasn't set anything yet — nobody
should see a blank starting point on their first day.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

from shared import portal_store as portal_store
from subsystems.morning import prep as morning_prep
from subsystems.morning import store as morning_store

PortalStoreNotConfigured = morning_store.PortalStoreNotConfigured

DOC_VERSION = 1
ORIGINS_OBJECT = f"{morning_store.PREFIX}origins.json"
ROUTES_OBJECT = f"{morning_store.PREFIX}routes.json"

_WORKDATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ORIGIN_KINDS = ("home", "office", "current", "custom")

# Spec: "Green Valley office" — the default starting point until an employee
# picks their own. No lat/lng shipped here (geocoding is a later concern);
# the label + address are enough for `maps_url` to resolve via Google's own
# search, and enough for the UI to show something meaningful.
OFFICE_ORIGIN: dict = {
    "kind": "office",
    "label": "Green Valley office",
    "address": "Green Valley Contractors, Cincinnati, OH area",
    # Approx Cincinnati — enough for Open-Meteo + Maps when no personal origin.
    "lat": 39.1031,
    "lng": -84.5120,
}

# Route overrides: "Three overrides within ten scheduled workdays" (spec).
OVERRIDE_WINDOW_WORKDAYS = 10
OVERRIDE_ALERT_THRESHOLD = 3


class RouteValidationError(ValueError):
    """Structurally invalid route/origin input (caller maps to HTTP 422)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_email(email: str) -> str:
    return portal_store.normalize_email(email)


def _validate_workdate(workdate: str) -> str:
    if not isinstance(workdate, str) or not _WORKDATE_RE.match(workdate):
        raise RouteValidationError(f"workdate must be YYYY-MM-DD, got {workdate!r}.")
    return workdate


# ---------------------------------------------------------------------------
# Origins — pure helpers
# ---------------------------------------------------------------------------

def apply_set_origin(doc: dict, *, email: str, kind: str, label: Optional[str],
                      lat: Optional[float], lng: Optional[float],
                      address: Optional[str], actor: Optional[str]) -> tuple[dict, dict]:
    """PURE. Replace one employee's stored origin. Returns (new_doc, record)."""
    if kind not in ORIGIN_KINDS:
        raise RouteValidationError(f"Unknown origin kind {kind!r} (expected one of {ORIGIN_KINDS}).")
    by_user = dict(doc.get("by_user") or {})
    record = {
        "kind": kind,
        "label": (label or "").strip() or None,
        "lat": lat,
        "lng": lng,
        "address": (address or "").strip() or None,
        "updated_at": _now_iso(),
        "updated_by": _norm_email(actor) if actor else None,
    }
    by_user[email] = record
    return {**doc, "version": DOC_VERSION, "by_user": by_user}, record


def _origins_object() -> str:
    return os.environ.get("GVC_MORNING_ORIGINS_OBJECT") or ORIGINS_OBJECT


def get_origin(email: str) -> dict:
    """
    The stored origin for `email`, or a copy of OFFICE_ORIGIN (tagged
    `is_default: True`) when nothing has been set yet. Callers MUST scope
    `email` to the authenticated caller (or a role-checked management view)
    — this function has no notion of "whose session is this", it just
    returns whatever is on file for the email it's given.
    """
    email_n = _norm_email(email)
    doc, _ = morning_store.read_doc(_origins_object())
    rec = (doc.get("by_user") or {}).get(email_n)
    if rec:
        return dict(rec)
    return {**OFFICE_ORIGIN, "is_default": True}


def set_origin(email: str, *, kind: str, label: Optional[str] = None,
                lat: Optional[float] = None, lng: Optional[float] = None,
                address: Optional[str] = None, actor: Optional[str] = None) -> dict:
    """Set (replace) `email`'s private starting location. Returns the stored
    record. Never written to Monday — GCS only, by design."""
    email_n = _norm_email(email)
    if not email_n:
        raise RouteValidationError("email is required.")

    def fn(doc: dict):
        return apply_set_origin(doc, email=email_n, kind=kind, label=label,
                                 lat=lat, lng=lng, address=address, actor=actor)

    return morning_store.mutate(_origins_object(), fn)


# ---------------------------------------------------------------------------
# Google Maps directions link — pure
# ---------------------------------------------------------------------------

def _waypoint_text(place: Optional[dict]) -> Optional[str]:
    """PURE. A stop/origin dict -> the text Google Maps wants for a
    waypoint: "lat,lng" when both are present (most precise), else an
    address, else a label. None when the place has nothing usable — the
    caller skips it rather than sending Maps a blank waypoint."""
    if not place:
        return None
    lat, lng = place.get("lat"), place.get("lng")
    if lat is not None and lng is not None:
        return f"{lat},{lng}"
    for key in ("address", "location", "label"):
        v = place.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return None


def maps_url(stops: list, origin: Optional[dict] = None) -> str:
    """
    PURE. Build a Google Maps directions URL (the `?api=1` deep-link format,
    which opens the Maps app on a phone) through `stops` in the given order:
    origin → stop1 → stop2 → … → destination (last stop).

    Stops without a resolvable location (no lat/lng, address, or location
    text) are skipped — a stop the portal couldn't place shouldn't break
    navigation to the ones it could. When no stop resolves, returns the bare
    Maps URL so the button still does *something* sensible rather than 404.
    No Distance Matrix / API key required — Maps resolves the addresses itself.
    """
    base = "https://www.google.com/maps/dir/?api=1"
    # Incomplete stops stay on the sheet but don't break the multi-stop URL.
    active = [s for s in (stops or []) if not s.get("completed")]
    ordered = active or list(stops or [])
    stop_texts = [t for t in (_waypoint_text(s) for s in ordered) if t]
    if not stop_texts:
        origin_only = _waypoint_text(origin)
        if origin_only:
            return f"{base}&origin={quote(origin_only, safe=',')}&travelmode=driving"
        return base
    parts = [("destination", stop_texts[-1]), ("travelmode", "driving")]
    if len(stop_texts) > 1:
        # Google Maps dir API: waypoints joined by | (URL-encoded as %7C).
        parts.append(("waypoints", "|".join(stop_texts[:-1])))
    origin_text = _waypoint_text(origin)
    if origin_text:
        parts.append(("origin", origin_text))
    query = "&".join(f"{k}={quote(v, safe=',')}" for k, v in parts)
    return f"{base}&{query}"


# ---------------------------------------------------------------------------
# Daily routes — pure helpers
# ---------------------------------------------------------------------------

def _empty_day() -> dict:
    return {"origin_ref": None, "stops": [], "optimized": False,
            "optimized_order": None, "overridden": False,
            "override_count_window": 0}


def apply_save_route(doc: dict, *, email: str, workdate: str, stops: list,
                      origin_ref: Optional[dict], actor: Optional[str]) -> tuple[dict, dict]:
    """PURE. Store the SYSTEM-OFFERED route (the optimizer's proposed order)
    for one employee/workday. Resets the override flag — a fresh optimize
    is not itself an override. Returns (new_doc, day)."""
    by_user = dict(doc.get("by_user") or {})
    user = dict(by_user.get(email) or {})
    workdates = dict(user.get("workdates") or {})
    ordered = [dict(s) for s in (stops or [])]
    for i, s in enumerate(ordered):
        s["sequence"] = i
        s.setdefault("completed", False)
        s.setdefault("note", None)
    day = {
        "origin_ref": origin_ref,
        "stops": ordered,
        "optimized": True,
        "optimized_order": [s.get("item_id") for s in ordered],
        "overridden": False,
        "override_count_window": (workdates.get(workdate) or {}).get("override_count_window", 0),
        "updated_at": _now_iso(),
        "updated_by": _norm_email(actor) if actor else None,
    }
    workdates[workdate] = day
    user["workdates"] = workdates
    by_user[email] = user
    return {**doc, "version": DOC_VERSION, "by_user": by_user}, day


def apply_reorder(doc: dict, *, email: str, workdate: str,
                   ordered_item_ids: list, actor: Optional[str]) -> tuple[dict, dict]:
    """
    PURE. Re-sequence the day's stops to `ordered_item_ids` (the employee's
    final order — "Accept the employee's final order without requiring a
    reason"). Marks `overridden = True` when this differs from the last
    system-offered `optimized_order`. Returns (new_doc, day). Raises
    RouteValidationError if no route exists yet for this workday.
    """
    by_user = dict(doc.get("by_user") or {})
    user = dict(by_user.get(email) or {})
    workdates = dict(user.get("workdates") or {})
    day = workdates.get(workdate)
    if day is None:
        raise RouteValidationError("No route exists for this workday yet.")
    day = dict(day)
    by_id = {s.get("item_id"): dict(s) for s in (day.get("stops") or [])}
    new_stops = []
    for i, item_id in enumerate(ordered_item_ids or []):
        s = by_id.get(item_id, {"item_id": item_id, "completed": False, "note": None})
        s["sequence"] = i
        new_stops.append(s)
    day["stops"] = new_stops
    optimized_order = day.get("optimized_order")
    if optimized_order is not None and list(ordered_item_ids or []) != list(optimized_order):
        day["overridden"] = True
    day["updated_at"] = _now_iso()
    day["updated_by"] = _norm_email(actor) if actor else None
    workdates[workdate] = day
    user["workdates"] = workdates
    by_user[email] = user
    return {**doc, "version": DOC_VERSION, "by_user": by_user}, day


def apply_complete_stop(doc: dict, *, email: str, workdate: str, item_id: Any,
                         note: Optional[str], actor: Optional[str]) -> tuple[dict, dict]:
    """PURE. Mark one stop complete (spec: lightweight — check + optional
    note, no GM approval, no automatic Stage change). Returns (new_doc, day).
    Raises RouteValidationError if the workday or stop doesn't exist."""
    by_user = dict(doc.get("by_user") or {})
    user = dict(by_user.get(email) or {})
    workdates = dict(user.get("workdates") or {})
    day = workdates.get(workdate)
    if day is None:
        raise RouteValidationError("No route exists for this workday yet.")
    day = dict(day)
    stops = [dict(s) for s in (day.get("stops") or [])]
    found = False
    for s in stops:
        if s.get("item_id") == item_id:
            s["completed"] = True
            s["completed_at"] = _now_iso()
            if note is not None:
                s["note"] = note
            found = True
            break
    if not found:
        raise RouteValidationError(f"Stop {item_id!r} is not on this workday's route.")
    day["stops"] = stops
    day["updated_at"] = _now_iso()
    day["updated_by"] = _norm_email(actor) if actor else None
    workdates[workdate] = day
    user["workdates"] = workdates
    by_user[email] = user
    return {**doc, "version": DOC_VERSION, "by_user": by_user}, day


def apply_set_override_window(doc: dict, *, email: str, workdate: str, count: int) -> tuple[dict, dict]:
    """PURE. Stash the freshly-computed rolling override count onto a day's
    record, per the spec's stored shape. Purely a cache — `count_overrides`
    always recomputes from the underlying `overridden` flags, never from
    this cached number."""
    by_user = dict(doc.get("by_user") or {})
    user = dict(by_user.get(email) or {})
    workdates = dict(user.get("workdates") or {})
    day = workdates.get(workdate)
    if day is None:
        return doc, {}
    day = dict(day)
    day["override_count_window"] = count
    workdates[workdate] = day
    user["workdates"] = workdates
    by_user[email] = user
    return {**doc, "version": DOC_VERSION, "by_user": by_user}, day


def count_overrides(user_workdates: dict, *, as_of: Optional[date] = None) -> dict:
    """
    PURE. `user_workdates` is the raw `{"YYYY-MM-DD": {...day...}}` map for
    one employee. Counts how many of the last OVERRIDE_WINDOW_WORKDAYS
    SCHEDULED workdays (ending at `as_of`, default today ET) had
    `overridden = True`. Returns {"count": n, "flag": n >= 3} — a signal
    for the caller to act on (Slack the GM), never sent from here.
    """
    as_of = as_of or datetime.now(timezone.utc).date()
    count = 0
    seen = 0
    d = as_of
    for _ in range(3660):
        if morning_prep.is_scheduled_workday(d):
            seen += 1
            day = user_workdates.get(d.isoformat())
            if day and day.get("overridden"):
                count += 1
            if seen >= OVERRIDE_WINDOW_WORKDAYS:
                break
        d = d - timedelta(days=1)
    return {"count": count, "flag": count >= OVERRIDE_ALERT_THRESHOLD}


# ---------------------------------------------------------------------------
# Store-touching API
# ---------------------------------------------------------------------------

def _routes_object() -> str:
    return os.environ.get("GVC_MORNING_ROUTES_OBJECT") or ROUTES_OBJECT


def get_route(email: str, workdate: str) -> dict:
    """The stored route for `email`/`workdate`, or an empty skeleton."""
    _validate_workdate(workdate)
    email_n = _norm_email(email)
    doc, _ = morning_store.read_doc(_routes_object())
    user = (doc.get("by_user") or {}).get(email_n) or {}
    return dict((user.get("workdates") or {}).get(workdate) or _empty_day())


def save_route(email: str, workdate: str, *, stops: list,
                origin_ref: Optional[dict] = None, actor: Optional[str] = None) -> dict:
    """Store the system-offered route for `email`/`workdate`. Returns the
    stored day record."""
    _validate_workdate(workdate)
    email_n = _norm_email(email)
    if not email_n:
        raise RouteValidationError("email is required.")

    def fn(doc: dict):
        return apply_save_route(doc, email=email_n, workdate=workdate,
                                 stops=stops, origin_ref=origin_ref, actor=actor)

    return morning_store.mutate(_routes_object(), fn)


def reorder_stops(email: str, workdate: str, ordered_item_ids: list, *,
                   actor: Optional[str] = None) -> dict:
    """
    Apply the employee's final stop order. Returns
    {"route": <day record>, "override_signal": {"count", "flag"}} so the
    caller can decide whether to alert the GM — this function never Slacks.
    """
    _validate_workdate(workdate)
    email_n = _norm_email(email)
    if not email_n:
        raise RouteValidationError("email is required.")

    def fn(doc: dict):
        return apply_reorder(doc, email=email_n, workdate=workdate,
                              ordered_item_ids=ordered_item_ids, actor=actor)

    day = morning_store.mutate(_routes_object(), fn)
    signal = count_overrides_for(email_n, as_of=_parse_workdate(workdate))
    if day:
        def stash(doc: dict):
            return apply_set_override_window(doc, email=email_n, workdate=workdate,
                                              count=signal["count"])
        morning_store.mutate(_routes_object(), stash)
    return {"route": day, "override_signal": signal}


def complete_stop(email: str, workdate: str, item_id: Any, *,
                   note: Optional[str] = None, actor: Optional[str] = None) -> dict:
    """Mark one stop complete for `email`/`workdate`. Returns the stored
    day record."""
    _validate_workdate(workdate)
    email_n = _norm_email(email)
    if not email_n:
        raise RouteValidationError("email is required.")

    def fn(doc: dict):
        return apply_complete_stop(doc, email=email_n, workdate=workdate,
                                    item_id=item_id, note=note, actor=actor)

    return morning_store.mutate(_routes_object(), fn)


def count_overrides_for(email: str, *, as_of: Optional[date] = None) -> dict:
    """Convenience wrapper: load `email`'s stored workdates and run
    `count_overrides` over them."""
    email_n = _norm_email(email)
    doc, _ = morning_store.read_doc(_routes_object())
    user = (doc.get("by_user") or {}).get(email_n) or {}
    return count_overrides(user.get("workdates") or {}, as_of=as_of)


def _parse_workdate(workdate: str) -> date:
    _validate_workdate(workdate)
    return date.fromisoformat(workdate)
