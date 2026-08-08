"""
Owner Pulse — the exception-only view for Jordan (docs/MORNING_BRIEF_BUILD_SPEC.md
"Owner Pulse" + "Governing Principle: Remove Work From Jordan").
=========================================================================
This module does no I/O and computes nothing on its own — every input is
handed to it already computed by the caller (prep percentages from
subsystems.morning.prep, safety stops / owner decisions from whatever
Monday read surfaces them, huddle outcome from subsystems.morning.meeting,
planning signals from subsystems.morning.route.count_overrides). Its ONLY
job is assembly + the one rule the spec cares about most:

    "The Owner Pulse must not become a disguised task list."

That rule is enforced by construction here, not by convention: this
function never emits a field that reads as an assignment. Preparation
alerts are informational (`preparation_alerts`, not "things to fix");
huddle actions are visible only as a COUNT (`actions_assigned`), never as
the action list itself — the action owners already have that list. Owner
decisions and safety stops are the only line items with real content,
because those are the two categories where owner authority or attention is
actually required.

Two calling shapes are both accepted (and both key spellings are always
present on the return value), because this function has two honest
callers with different needs: `orchestrators/morning_flow.py` hands a
single already-filtered payload dict positionally; direct/pure callers
(tests, a future admin tool) pass raw, unfiltered inputs as keyword
arguments and expect THIS function to do the 3/5-tier + planning-signal
filtering. Both are legitimate — filtering here as well as at the call
site is idempotent, not a conflict.
"""
from __future__ import annotations

from typing import Any, Optional

# Alert "level" values that count as 3-day/5-day tier (visible to the
# owner) — the spec's 1-day tier is a private employee warning only and
# must never surface here. Both int and the string spellings the flow's
# own tolerant check uses are accepted (see morning_flow.build_owner_pulse).
_HIGH_TIER_LEVELS = {3, 5, "3", "5", "visibility", "coaching"}


def _is_high_tier(alert: dict) -> bool:
    return (alert or {}).get("level") in _HIGH_TIER_LEVELS


def _is_planning_alert(signal: dict) -> bool:
    """A route-override signal counts as an exception once it's already
    flagged by its own producer, OR crosses the >=3-in-window threshold
    itself (belt-and-suspenders if a caller forwards raw counts)."""
    signal = signal or {}
    if signal.get("alert") or signal.get("flag"):
        return True
    try:
        return int(signal.get("count") or 0) >= 3
    except (TypeError, ValueError):
        return False


def normalize_owner_decision(raw: Any) -> Optional[dict]:
    """PURE. Coerce a decision blob into {kind,title,detail,href,action}.

    Rejects empties. Never invents a fake job — missing href falls back to
    GM Huddle (where parked/owner items live), not a fabricated Job Check.
    """
    if not isinstance(raw, dict):
        return None
    title = (raw.get("title") or raw.get("name") or raw.get("project_name")
             or raw.get("topic") or "").strip()
    if not title:
        return None
    detail = (raw.get("detail") or raw.get("blocked") or raw.get("need")
              or raw.get("message") or raw.get("follow_up") or "").strip()
    href = (raw.get("href") or "").strip()
    item_id = str(raw.get("item_id") or "").strip()
    if not href and item_id:
        href = f"/ui/jobcheck?item={item_id}"
    if not href:
        href = (raw.get("fallback_href") or "/ui/morning-gm").strip()
    action = (raw.get("action") or "Open").strip() or "Open"
    kind = (raw.get("kind") or "Decision").strip() or "Decision"
    return {
        "kind": kind[:40],
        "title": title[:120],
        "detail": detail[:200],
        "href": href,
        "action": action[:40],
    }


def _parking_needs_owner(row: dict) -> bool:
    owner = (row.get("owner") or "").strip().lower()
    if not owner:
        return False
    if row.get("needs_owner"):
        return True
    return any(tok in owner for tok in ("jordan", "owner", "jfaulkner"))


def collect_owner_decisions(*, explicit: Optional[list] = None,
                            parking: Optional[list] = None,
                            action_requests: Optional[list] = None,
                            unresolved_risks: Optional[list] = None
                            ) -> list[dict]:
    """PURE. Build the Owner decisions list from known exception sources."""
    out: list[dict] = []
    seen: set[str] = set()

    def _add(raw: dict) -> None:
        n = normalize_owner_decision(raw)
        if not n:
            return
        key = f"{n['kind']}|{n['title']}|{n['href']}"
        if key in seen:
            return
        seen.add(key)
        out.append(n)

    for row in explicit or []:
        _add(row if isinstance(row, dict) else {})
    for risk in unresolved_risks or []:
        if isinstance(risk, dict):
            _add({**risk, "kind": risk.get("kind") or "Risk",
                  "action": risk.get("action") or "Review",
                  "fallback_href": "/ui/morning-gm"})
    for row in parking or []:
        if not isinstance(row, dict) or not _parking_needs_owner(row):
            continue
        _add({
            "kind": "Parked",
            "title": row.get("topic") or "Parked huddle item",
            "detail": row.get("follow_up") or f"Owner: {row.get('owner')}",
            "href": "/ui/morning-gm",
            "action": "Open GM Huddle",
        })
    for req in action_requests or []:
        if not isinstance(req, dict):
            continue
        esc = (req.get("escalation") or "").strip().lower()
        if esc not in ("overdue", "ack_reminder") and not req.get("needs_owner"):
            continue
        _add({
            "kind": "Ask",
            "title": req.get("project_name") or req.get("need") or "Action request",
            "detail": req.get("need") or esc.replace("_", " "),
            "href": "/ui/morning-gm",
            "action": "Open GM Huddle",
        })
    return out


def build_owner_pulse(data: Optional[dict] = None, **kwargs: Any) -> dict:
    """
    PURE. Assemble the Owner Pulse from already-computed inputs. Accepts
    either a single dict (positional) or keyword arguments — both merge
    into the same payload, kwargs winning on overlap:

        prep_pct           0-100, OR {"pct": 0-100, ...} (team_prep_percentage's
                            own shape) — either is accepted.
        safety_stops        [ {...} ]
        owner_decisions     [ {...} ]
        prep_alerts_3_5      OR  prep_alerts   — either key name. If the
                            caller already filtered to the 3/5 tier, pass
                            prep_alerts_3_5 (pre-filtered lists still pass
                            the filter here unchanged); raw/unfiltered
                            alerts should be passed as prep_alerts.
        huddle_outcome       {"projects_covered": int, "actions_assigned": int,
                              "unresolved_owner_risks": [ {...} ]}
        planning_signals     [ {...} ]  — e.g. route-override alerts
        workdate             "YYYY-MM-DD" (echoed back if given)

    Missing keys degrade to empty — this function never invents data to
    fill a gap. Returns a dict carrying BOTH naming conventions the two
    real call sites use (team_prep_pct/team_preparation_pct,
    preparation_alerts/prep_alerts) plus `has_exceptions` (for a calm
    "nothing needs you today" UI state) and `note` (the standing
    exception-only reminder rendered wherever the pulse is shown).
    """
    payload: dict = dict(data or {})
    payload.update(kwargs)

    raw_prep_pct = payload.get("prep_pct")
    prep_pct = raw_prep_pct.get("pct") if isinstance(raw_prep_pct, dict) else raw_prep_pct

    raw_alerts = payload.get("prep_alerts_3_5")
    if raw_alerts is None:
        raw_alerts = payload.get("prep_alerts") or []
    prep_alerts = [a for a in raw_alerts if _is_high_tier(a)]

    raw_signals = payload.get("planning_signals") or []
    planning_signals = [s for s in raw_signals if _is_planning_alert(s)]

    safety_stops: list[dict] = []
    for stop in payload.get("safety_stops") or []:
        if not isinstance(stop, dict):
            continue
        s = dict(stop)
        if s.get("item_id") and not s.get("href"):
            s["href"] = f"/ui/jobcheck?item={s['item_id']}"
        safety_stops.append(s)
    huddle = payload.get("huddle_outcome") or {}
    unresolved_risks = list(huddle.get("unresolved_owner_risks") or [])
    owner_decisions = collect_owner_decisions(
        explicit=payload.get("owner_decisions") or [],
        parking=payload.get("parking") or [],
        action_requests=payload.get("action_requests") or [],
        unresolved_risks=unresolved_risks,
    )

    has_exceptions = bool(
        safety_stops or owner_decisions or prep_alerts or planning_signals or unresolved_risks
    )

    result = {
        "team_prep_pct": prep_pct,
        "team_preparation_pct": prep_pct,
        "safety_stops": safety_stops,
        "owner_decisions": owner_decisions,
        "preparation_alerts": prep_alerts,
        "prep_alerts": prep_alerts,
        "huddle_outcome": {
            "projects_covered": huddle.get("projects_covered"),
            "actions_assigned": huddle.get("actions_assigned"),
            "unresolved_owner_risks": unresolved_risks,
        },
        "planning_signals": planning_signals,
        "has_exceptions": has_exceptions,
        "note": "Exception-only. Routine ops stay with the GM and project owners.",
    }
    if "workdate" in payload:
        result["workdate"] = payload["workdate"]
    return result
