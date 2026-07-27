"""
Lien Watch deadline math — PURE functions over shared/lien_rules.json.
=========================================================================
Given a job {state, project_type, first_furnishing_date}, compute its
notice/lien/retainage deadline set. No I/O beyond reading the bundled rules
JSON (cached); no Monday, no Slack — that's orchestrators/lien_flow.py.

Design rules (docs/portal-lien-rights-tracker-design.md, P1):
  • The rules JSON is the machine source, but NOTHING here is attorney-blessed
    yet (every entry carries attorney_reviewed: false). The tracker surfaces
    deadlines; humans own them.
  • P1 only knows FIRST furnishing. Deadlines anchored on last furnishing or
    on an event (retainage release) get NO computed due date — they surface
    with severity "unknown" and their statutory clock spelled out, instead of
    a fabricated date. A drywall job's last furnishing is always ≥ first
    furnishing, so inventing due dates from the start date would manufacture
    false "missed" alarms (the exact failure mode Jordan hates — see the
    send-status-truth incident).
  • Unknown project_type ⇒ compute BOTH the residential and commercial
    variants and mark every row ambiguous. Public is never guessed — it only
    applies when the job is explicitly known to be public work.

Severity scale (locked by the P1 spec):
  ok       > 14 days remaining
  warn     4–14 days
  critical ≤ 3 days (including due today)
  missed   < 0 days
  unknown  inputs missing (no state, no clock date, or non-first anchors)
"""
from __future__ import annotations

import calendar
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

RULES_PATH = Path(__file__).resolve().parents[2] / "shared" / "lien_rules.json"

KNOWN_STATES = ("OH", "IN", "KY")
PROJECT_TYPES = ("residential", "commercial", "public")
# Unknown type ⇒ both private variants. Never assume public.
AMBIGUOUS_TYPES = ("residential", "commercial")

SEVERITY_ORDER = ("missed", "critical", "warn", "ok", "unknown")
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

_STATE_PATTERNS = {
    "OH": re.compile(r"(?:\bOhio\b|\bOH\b(?=[\s,.]|\d|$))", re.IGNORECASE),
    "IN": re.compile(r"(?:\bIndiana\b|\bIN\b(?=[\s,.]|\d|$))"),
    "KY": re.compile(r"(?:\bKentucky\b|\bKY\b(?=[\s,.]|\d|$))", re.IGNORECASE),
}

_rules_cache: Optional[dict] = None


def load_rules(path: Optional[Path] = None) -> dict:
    """Load (and cache) the rules pack. Raises on a malformed file — a broken
    deadline table must be loud, never a silent empty tracker."""
    global _rules_cache
    if path is None and _rules_cache is not None:
        return _rules_cache
    p = path or RULES_PATH
    with open(p, encoding="utf-8") as fh:
        rules = json.load(fh)
    if not isinstance(rules.get("entries"), list):
        raise ValueError(f"{p}: rules pack has no 'entries' list")
    if path is None:
        _rules_cache = rules
    return rules


def parse_state(*texts: Optional[str]) -> Optional[str]:
    """
    Best-effort OH/IN/KY extraction from free text (Job Location column first,
    item name as fallback — Monday rows carry the state in both, inconsistently).
    Word-boundary matching; bare 'IN' is only matched case-sensitively so the
    English word "in" can't classify a job. None when nothing matches.
    """
    for text in texts:
        if not text:
            continue
        for state, pattern in _STATE_PATTERNS.items():
            if pattern.search(text):
                return state
    return None


def normalize_project_type(label: Optional[str]) -> str:
    """Map a Monday 'Project Type' status label onto the rule vocabulary.
    The board's labels today: Residential / Commercial / Repair / Standard
    Project(s) / Specialty Project(s) — only the first two map cleanly; there
    is no 'Public' label yet, so public is opt-in via a future column."""
    t = (label or "").strip().lower()
    if not t:
        return "unknown"
    if "residential" in t:
        return "residential"
    if "commercial" in t:
        return "commercial"
    if "public" in t:
        return "public"
    return "unknown"


def _add_months(d: date, months: int) -> date:
    """Calendar-month addition, clamped to month end (Jan 31 + 1mo = Feb 28/29)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _due_date(anchor_date: date, spec: Any) -> date:
    """spec is an int day-count or {"months": n} (KY's 6-month filing)."""
    if isinstance(spec, dict):
        return _add_months(anchor_date, int(spec["months"]))
    return anchor_date + timedelta(days=int(spec))


def _spec_text(spec: Any) -> str:
    if isinstance(spec, dict):
        n = int(spec["months"])
        return f"{n} month{'s' if n != 1 else ''}"
    return f"{int(spec)} days"


def severity_for(days_remaining: Optional[int]) -> str:
    if days_remaining is None:
        return "unknown"
    if days_remaining < 0:
        return "missed"
    if days_remaining <= 3:
        return "critical"
    if days_remaining <= 14:
        return "warn"
    return "ok"


def worst_severity(severities) -> str:
    """The most urgent severity in the set ('unknown' when empty)."""
    ranked = [s for s in severities if s in _SEVERITY_RANK]
    if not ranked:
        return "unknown"
    return min(ranked, key=_SEVERITY_RANK.__getitem__)


def _anchor_text(computed: dict) -> str:
    anchor = computed.get("anchor")
    if anchor == "first_furnishing":
        return "first furnishing"
    if anchor == "last_furnishing":
        return "last furnishing"
    return computed.get("event") or "triggering event"


def _rows_for_type(entry: dict, computed: dict, project_type: str,
                   first_furnishing: Optional[date], today: date,
                   ambiguous: bool) -> list[dict]:
    spec = (computed.get("days") or {}).get(project_type)
    if spec is None:
        return []
    row = {
        "kind": computed["kind"],
        "label": computed.get("label") or computed["kind"],
        "rule_id": entry["id"],
        "statute": entry.get("statute"),
        "source_url": (entry.get("sources") or [{}])[0].get("url"),
        "anchor": computed.get("anchor"),
        "anchor_text": _anchor_text(computed),
        "window": _spec_text(spec),
        "project_type_variant": project_type,
        "ambiguous": ambiguous,
        "attorney_reviewed": bool(entry.get("attorney_reviewed", False)),
        "note": computed.get("note"),
        "due_date": None,
        "days_remaining": None,
    }
    if computed.get("anchor") == "first_furnishing" and first_furnishing is not None:
        due = _due_date(first_furnishing, spec)
        row["due_date"] = due.isoformat()
        row["days_remaining"] = (due - today).days
    row["severity"] = severity_for(row["days_remaining"])
    return [row]


def compute_deadlines(state: Optional[str], project_type: str,
                      first_furnishing: Optional[date], *,
                      today: Optional[date] = None,
                      rules: Optional[dict] = None) -> dict:
    """
    The deadline set for one job. Returns:
      {deadlines: [row…], advisories: [str…], max_severity, state_known: bool}

    Rows are sorted most-urgent first (dated rows by days_remaining, then the
    undated/unknown rows grouped by anchor so the page reads sensibly).
    Unknown project_type computes BOTH private variants, each marked ambiguous.
    """
    today = today or date.today()
    rules = rules or load_rules()
    state = (state or "").strip().upper() or None

    if state not in KNOWN_STATES:
        return {"deadlines": [], "advisories": [], "max_severity": "unknown",
                "state_known": False}

    variants: tuple[str, ...]
    ambiguous = project_type not in PROJECT_TYPES
    variants = AMBIGUOUS_TYPES if ambiguous else (project_type,)

    deadlines: list[dict] = []
    advisories: list[str] = []
    seen: dict[tuple, dict] = {}
    for entry in rules["entries"]:
        if entry.get("state") != state:
            continue
        for computed in entry.get("computed") or []:
            for variant in variants:
                for row in _rows_for_type(entry, computed, variant,
                                          first_furnishing, today, ambiguous):
                    # Two variants with the same window collapse to one row
                    # (the variant no longer matters when the math agrees).
                    key = (row["kind"], row["window"], row["due_date"])
                    if key in seen:
                        seen[key]["project_type_variant"] = "either"
                        continue
                    seen[key] = row
                    deadlines.append(row)
        advisory = entry.get("advisory")
        if advisory and any(v in (advisory.get("applies_to") or []) for v in variants):
            advisories.append(advisory["note"])

    def _sort_key(row: dict):
        dated = row["days_remaining"] is not None
        return (0 if dated else 1,
                row["days_remaining"] if dated else 10**6,
                0 if row["anchor"] == "last_furnishing" else 1,
                row["kind"])

    deadlines.sort(key=_sort_key)
    return {
        "deadlines": deadlines,
        "advisories": advisories,
        "max_severity": worst_severity(r["severity"] for r in deadlines),
        "state_known": True,
    }
