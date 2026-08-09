"""PURE non-negotiables logic — no I/O, no clock (callers pass ``today``).

Source of truth for the rules is Jordan's tracker doc (Dan Martell's 5 daily
non-negotiables, start Monday 2026-08-10, 365 days):

  - The five: review goals / sweat 45 / read 10 pages / post 3 / send 5.
  - Every day counts, weekends included.
  - A day is "perfect" only at 5/5.
  - ANY missed day breaks the streak — reset to zero.

Streak semantics: today never breaks a streak while it's still in progress —
the current streak counts consecutive perfect days ending YESTERDAY, plus
today once today hits 5/5. Yesterday imperfect => streak is (1 if today is
perfect else 0).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

START_DATE = "2026-08-10"
CHALLENGE_DAYS = 365

# (key, label, sublabel) — display order everywhere.
HABITS: tuple[tuple[str, str, str], ...] = (
    ("goals", "Review goals", "12-month goals, out loud"),
    ("sweat", "Sweat 45", "45 min continuous — not split"),
    ("read", "Read 10 pages", "non-fiction"),
    ("post", "Post 3", "story-style, face to camera"),
    ("send", "Send 5 opens", "genuine outbound messages"),
)
HABIT_KEYS = tuple(k for k, _, _ in HABITS)


def blank_doc() -> dict:
    return {
        "goals": ["", "", "", "", ""],
        "goals_last_review": "",
        "start_date": START_DATE,
        "days": {},
    }


def _iso(d: date) -> str:
    return d.isoformat()


def _parse(s: str) -> Optional[date]:
    try:
        return date.fromisoformat((s or "").strip())
    except ValueError:
        return None


def day_score(day: Optional[dict]) -> int:
    if not isinstance(day, dict):
        return 0
    return sum(1 for k in HABIT_KEYS if day.get(k) is True)


def is_perfect(day: Optional[dict]) -> bool:
    return day_score(day) == len(HABIT_KEYS)


def toggle(doc: dict, day_iso: str, key: str, done: bool, *,
           today: date) -> dict:
    """Set one habit on one day. Raises ValueError on bad input.

    Editable window: start date through today. Future days are not tappable
    ("plan better" is the doc's rule, not pre-crediting tomorrow); days
    before the challenge started don't exist.
    """
    if key not in HABIT_KEYS:
        raise ValueError(f"unknown habit {key!r}")
    d = _parse(day_iso)
    if d is None:
        raise ValueError("date must be YYYY-MM-DD")
    start = _parse(doc.get("start_date") or START_DATE) or _parse(START_DATE)
    if d < start:
        raise ValueError(f"the challenge starts {_iso(start)}")
    if d > today:
        raise ValueError("that day hasn't happened yet")
    days = dict(doc.get("days") or {})
    entry = dict(days.get(day_iso) or {})
    entry[key] = bool(done)
    days[day_iso] = entry
    out = dict(doc)
    out["days"] = days
    # Ticking "Review goals" IS the goal review — stamp it.
    if key == "goals" and done and day_iso >= (out.get("goals_last_review") or ""):
        out["goals_last_review"] = day_iso
    return out


def set_goals(doc: dict, goals: list) -> dict:
    if not isinstance(goals, list) or len(goals) > 5:
        raise ValueError("goals must be a list of at most 5 strings")
    cleaned = [str(g or "").strip()[:500] for g in goals]
    cleaned += [""] * (5 - len(cleaned))
    out = dict(doc)
    out["goals"] = cleaned
    return out


def compute_stats(doc: dict, *, today: date) -> dict:
    """Everything the page renders, computed in one pass."""
    days = doc.get("days") or {}
    start = _parse(doc.get("start_date") or START_DATE) or _parse(START_DATE)
    started = today >= start

    def perfect(d: date) -> bool:
        return is_perfect(days.get(_iso(d)))

    # Current streak: consecutive perfect days ending yesterday, + today if 5/5.
    current = 0
    if started:
        cursor = today - timedelta(days=1)
        while cursor >= start and perfect(cursor):
            current += 1
            cursor -= timedelta(days=1)
        if perfect(today):
            current += 1

    # Longest streak + totals + per-habit miss counts over elapsed days.
    longest = run = 0
    perfect_days = 0
    misses = {k: 0 for k in HABIT_KEYS}
    if started:
        cursor = start
        while cursor <= today:
            entry = days.get(_iso(cursor))
            if is_perfect(entry):
                perfect_days += 1
                run += 1
                longest = max(longest, run)
            else:
                # Today-in-progress doesn't count as a miss yet.
                if cursor != today:
                    for k in HABIT_KEYS:
                        if not (isinstance(entry, dict) and entry.get(k) is True):
                            misses[k] += 1
                run = 0
            cursor += timedelta(days=1)
        longest = max(longest, current)

    # This week, Monday-first, matching the doc's grid.
    week_start = today - timedelta(days=today.weekday())
    week = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        iso = _iso(d)
        week.append({
            "date": iso,
            "dow": d.strftime("%a"),
            "score": day_score(days.get(iso)),
            "is_today": d == today,
            "in_challenge": start <= d <= today,
        })

    day_number = (today - start).days + 1 if started else 0
    worst = max(misses, key=lambda k: misses[k]) if any(misses.values()) else ""
    return {
        "started": started,
        "starts_in_days": max(0, (start - today).days),
        "start_date": _iso(start),
        "day_number": min(day_number, CHALLENGE_DAYS),
        "challenge_days": CHALLENGE_DAYS,
        "today": _iso(today),
        "today_score": day_score(days.get(_iso(today))),
        "current_streak": current,
        "longest_streak": longest,
        "perfect_days": perfect_days,
        "week": week,
        "most_missed": worst,
        "misses": misses,
    }
