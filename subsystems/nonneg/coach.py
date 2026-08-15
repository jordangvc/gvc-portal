"""PURE coach logic — prompt build + response validation. No network.

The coach turns Jordan's own notes into ammunition for the two non-negotiables
people actually run dry on: POST 3 (content ideas) and SEND 5 (who to reach
out to). Grounding rule: every suggestion must trace to his notes, goals, or
streak data — the prompt forbids invented names and generic filler, and
parse_tips() enforces shape so a malformed model response can never corrupt
the stored doc.

The adapter call happens in app/service.py; this module never imports it.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from subsystems.nonneg import tracker

TASK = "nonneg_coach"

# Street addresses are deterministically detectable — scrub them from public
# post copy in code rather than trusting the model (same philosophy as the
# Job Start GC-email SF scrub). "312 Elm Street" -> "the job site".
import re as _re
STREET_RE = _re.compile(
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Z][A-Za-z.]*(?:\s+[A-Z][A-Za-z.]*)?\s+"
    r"(?:Street|St|Avenue|Ave|Drive|Dr|Road|Rd|Lane|Ln|Boulevard|Blvd|Way|"
    r"Court|Ct|Pike|Place|Pl|Trail|Trl|Circle|Cir|Parkway|Pkwy)\.?\b")


def scrub_street_addresses(text: str) -> str:
    return STREET_RE.sub("the job site", text or "")
NOTE_MAX = 2000
MIN_REFRESH_INTERVAL_S = 60          # button mashing guard
NOTES_WINDOW_DAYS = 14
MAX_IDEAS = 5


def clean_note(text: Any) -> str:
    return str(text or "").strip()[:NOTE_MAX]


def recent_notes(doc: dict, *, today: date,
                 window: int = NOTES_WINDOW_DAYS) -> list[tuple[str, str]]:
    """[(iso_date, note)] newest first, within the window, non-empty only."""
    days = doc.get("days") or {}
    out: list[tuple[str, str]] = []
    for i in range(window):
        d = (today - timedelta(days=i)).isoformat()
        note = (days.get(d) or {}).get("note") or ""
        if note.strip():
            out.append((d, note.strip()))
    return out


def build_prompt(doc: dict, stats: dict, *, today: date) -> str:
    goals = [g for g in (doc.get("goals") or []) if g.strip()]
    goal_notes = doc.get("goal_notes") or []
    notes = recent_notes(doc, today=today)
    prior = (doc.get("coach") or {})
    prior_posts = [p.get("idea", "") for p in prior.get("post_ideas") or []]
    prior_reach = [p.get("who", "") for p in prior.get("outreach") or []]

    lines = [
        "You are the accountability coach inside Jordan's 5 Daily "
        "Non-Negotiables tracker (Dan Martell's five: review goals, sweat 45, "
        "read 10 pages, post 3 story-style pieces of content, send 5 genuine "
        "outbound messages). Jordan owns Green Valley Contractors.",
        "",
        "Your job: turn HIS OWN notes and goals into concrete ammunition for "
        "POST 3 and SEND 5, plus one focus line.",
        "",
        "HARD RULES:",
        "- Ground every suggestion in the notes/goals below. If a person is "
        "named in the notes, you may name them; otherwise describe the person "
        "by role (\"the GC super from the Tuesday walk\") — NEVER invent a name.",
        "- Post ideas are story-style / behind-the-scenes / face-to-camera "
        "prompts drawn from what actually happened in the notes — not generic "
        "content-marketing filler.",
        "- Short, direct, plainspoken. No corporate speak.",
        "- Do not repeat these recent suggestions: "
        + (("posts: " + "; ".join(prior_posts[:5]) + " | outreach: "
            + "; ".join(prior_reach[:5])) if (prior_posts or prior_reach)
           else "(none yet)"),
        "",
        "12-MONTH GOALS:",
    ]
    if goals:
        for i, g in enumerate(goals):
            lines.append(f"{i + 1}. {g}")
            note = goal_notes[i] if i < len(goal_notes) else ""
            if str(note).strip():
                lines.append(f"   note: {str(note).strip()}")
    else:
        lines.append("(not written yet — say so in the focus line)")
    lines += [
        "",
        f"STREAK: day {stats.get('day_number')} of 365, current streak "
        f"{stats.get('current_streak')}, most-missed habit: "
        f"{stats.get('most_missed') or 'none yet'}.",
        "",
        f"DAILY NOTES (newest first, last {NOTES_WINDOW_DAYS} days):",
    ]
    if notes:
        for d, n in notes:
            lines.append(f"[{d}] {n}")
    else:
        lines.append("(no notes yet — base suggestions on the goals, and say "
                     "in the focus line that daily notes will sharpen these)")
    lines += [
        "",
        "GVC MEDIA RULES — company policy (2026-07-28 admin meeting). The "
        "\"idea\" field is draft POST COPY and posts are public, so every "
        "idea must obey these; the \"why\" field and outreach are private "
        "to Jordan, names allowed there:",
        "- NEVER name a customer, builder, GC, or job in an idea. Rewrite: "
        "\"the KPMG job\" → \"a corporate office build-out in Cincinnati\".",
        "- Location in an idea: city and state only, never a street address.",
        "- No crew members' faces unless a note says they agreed. Nothing "
        "showing unsafe work.",
        "FINAL CHECK before you answer: re-read every post_ideas \"idea\"; "
        "if one contains a company, client, GC, person, or job name, rewrite "
        "it without the name. This check is mandatory.",
        "",
        "Return ONLY a JSON object, no prose, exactly this shape:",
        '{"focus": "one sentence — the single most useful push for tomorrow",',
        f' "post_ideas": [{{"idea": "the content piece", "why": "which note/goal it comes from"}}] (max {MAX_IDEAS}),',
        f' "outreach": [{{"who": "name from notes, or role description", "why": "why now", "opener": "a first message in Jordan\'s plain voice"}}] (max {MAX_IDEAS})}}',
    ]
    return "\n".join(lines)


def parse_tips(raw: dict, *, generated_at: str, through: str) -> dict:
    """Validate/shape a model response. Raises ValueError if unusable."""
    if not isinstance(raw, dict):
        raise ValueError("coach response was not an object")

    def _items(key: str, fields: tuple[str, ...]) -> list[dict]:
        out = []
        for item in (raw.get(key) or [])[:MAX_IDEAS]:
            if not isinstance(item, dict):
                continue
            shaped = {f: str(item.get(f) or "").strip()[:500] for f in fields}
            if key == "post_ideas" and "idea" in shaped:
                shaped["idea"] = scrub_street_addresses(shaped["idea"])
            if shaped[fields[0]]:
                out.append(shaped)
        return out

    posts = _items("post_ideas", ("idea", "why"))
    reach = _items("outreach", ("who", "why", "opener"))
    focus = str(raw.get("focus") or "").strip()[:500]
    if not posts and not reach and not focus:
        raise ValueError("coach response had no usable content")
    return {
        "generated_at": generated_at,
        "through": through,
        "focus": focus,
        "post_ideas": posts,
        "outreach": reach,
        "model": str(raw.get("_model") or "")[:80],
        "source": str(raw.get("_source") or "")[:20],
    }


def seconds_since(iso_ts: Optional[str], *, now_ts: float) -> float:
    """For the refresh-interval guard; malformed timestamp = long ago."""
    from datetime import datetime
    try:
        then = datetime.fromisoformat(iso_ts or "")
        return max(0.0, now_ts - then.timestamp())
    except ValueError:
        return 1e9


def apply_tips(doc: dict, tips: dict) -> dict:
    out = dict(doc)
    out["coach"] = tips
    return out


def set_day_note(doc: dict, day_iso: str, note: str, *, today: date) -> dict:
    """Attach/replace the note on a day. Same window rules as toggling."""
    d = None
    try:
        d = date.fromisoformat((day_iso or "").strip())
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    start = date.fromisoformat(doc.get("start_date") or tracker.START_DATE)
    if d < start - timedelta(days=1):  # allow a day-before warm-up note
        raise ValueError(f"the challenge starts {start.isoformat()}")
    if d > today:
        raise ValueError("that day hasn't happened yet")
    days = dict(doc.get("days") or {})
    entry = dict(days.get(day_iso) or {})
    entry["note"] = clean_note(note)
    days[day_iso] = entry
    out = dict(doc)
    out["days"] = days
    return out


def set_goal_notes(doc: dict, notes: list) -> dict:
    if not isinstance(notes, list) or len(notes) > 5:
        raise ValueError("goal_notes must be a list of at most 5 strings")
    cleaned = [clean_note(n) for n in notes]
    cleaned += [""] * (5 - len(cleaned))
    out = dict(doc)
    out["goal_notes"] = cleaned
    return out
