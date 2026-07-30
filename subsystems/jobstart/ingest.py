"""
Packet ingest — where the handoff's data actually comes from.
=========================================================================
Jordan, 2026-07-29: "the handoff has to pull the data from somewhere or it
becomes an input job for you. We don't want an input job for you."

His spec for the order of sources, same meeting:
  "ideally the project handoff sheet to the operations initially is built off of
   your scope review. Then it also checks the project updates. If there's an
   operations task already created, it will check the updates there and then
   keep the handoff up to date essentially."

So there are four sources, and they have a strict precedence. Anything a human
typed wins; below that, the most specific source wins:

    1. the saved packet          — what Jake actually typed, never overwritten
    2. the scope review (Drive)  — the richest source, and his own words say primary
    3. Monday board updates      — Project/Ops item updates, so the packet stays current
    4. the Bid Board columns     — the thin fallback

This module is the PURE merge layer. Drive reading lives in adapters/drive.py,
Monday reading in adapters/monday/jobstart.py, and the orchestration that calls
both lives in orchestrators/jobstart_flow.py.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Source labels surfaced to the UI so Jake can see WHERE a prefilled value came
# from — a value he can't trace is a value he'll retype.
SOURCE_PACKET = "packet"
SOURCE_SCOPE_REVIEW = "scope_review"
SOURCE_UPDATES = "updates"
SOURCE_BID = "bid"

PRECEDENCE = (SOURCE_PACKET, SOURCE_SCOPE_REVIEW, SOURCE_UPDATES, SOURCE_BID)


def _has(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def from_scope_review(parsed: dict) -> dict:
    """
    PURE. A scope_review.parse() result → packet field values.

    Only fields the document genuinely carries. Notably it does NOT try to
    invent a lock box or a board count — those aren't in a scope review, and a
    guessed value on a handoff packet is worse than a blank one.
    """
    if not parsed or not parsed.get("found"):
        return {}

    out: dict[str, str] = {}
    if parsed.get("scope"):
        out["scope"] = parsed["scope"]
    if parsed.get("exclusions"):
        out["exclusions"] = parsed["exclusions"]
    if parsed.get("contractor"):
        out["builder"] = parsed["contractor"]
    if parsed.get("contractor_contact"):
        # The scope review's GC contact is a name+phone — exactly what the
        # supervisor field is for when nobody has named a site super yet.
        out["supervisor"] = parsed["contractor_contact"]
    if parsed.get("project_type"):
        out["project_type"] = parsed["project_type"]

    questions = parsed.get("clarifications") or []
    if questions:
        out["open_questions"] = "\n".join(
            f"[{q.get('trade', '?')}] {q.get('question', '')}".strip()
            for q in questions if q.get("question"))
    if parsed.get("walkthrough"):
        out["allowances"] = parsed["walkthrough"]
    return out


# Monday update text → packet field. Ops and Sales write updates in prose, so
# these are deliberately loose leading-label matches ("Lock box: 4417") rather
# than an attempt to understand a paragraph.
_UPDATE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("lock_box", r"lock\s*box"),
    ("board_count", r"board\s*count"),
    ("scaffold", r"scaffold(?:ing)?"),
    ("heater_cans", r"heater|cans"),
    ("shower", r"shower"),
    ("ceiling_finish", r"ceiling\s*finish"),
    ("garage_finish", r"garage\s*finish"),
    ("window_returns", r"window\s*returns?"),
    ("window_type", r"window\s*type"),
    ("supervisor", r"(?:site\s*)?super(?:visor)?|site\s*contact"),
    ("start_date", r"start\s*date"),
)


def from_updates(update_texts: list) -> dict:
    """
    PURE. Monday item update bodies → packet field values.

    Scans newest-first for "Label: value" lines. This is what makes the handoff
    stay CURRENT rather than being a snapshot of the day it was created — the
    Project board update that says "lock box is 4417" reaches the packet without
    anyone re-keying it. First hit wins, so the newest update carries the day.
    """
    out: dict[str, str] = {}
    for text in (update_texts or []):
        if not text:
            continue
        # Monday update bodies are HTML. Block boundaries MUST become newlines
        # before tags are stripped — otherwise "<p>Lock box: 4417</p><p>Board
        # count: 340</p>" collapses onto one line and the first label swallows
        # every later field as part of its value (caught in testing).
        clean = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", str(text))
        clean = re.sub(r"(?i)</\s*(p|div|li|ul|ol|tr|td|h[1-6])\s*>", "\n", clean)
        clean = re.sub(r"(?i)<\s*(p|div|li|tr|h[1-6])\b[^>]*>", "\n", clean)
        clean = re.sub(r"<[^>]+>", " ", clean)
        clean = (clean.replace("&nbsp;", " ").replace("&amp;", "&")
                      .replace("&lt;", "<").replace("&gt;", ">")
                      .replace("&quot;", '"').replace("&#39;", "'"))
        for line in re.split(r"[\n\r]+|(?<=[.;])\s{2,}", clean):
            line = line.strip(" -•\t")
            if not line or ":" not in line:
                continue
            label, _, value = line.partition(":")
            label, value = label.strip(), value.strip()
            if not value or len(label) > 40:
                continue
            for key, pattern in _UPDATE_PATTERNS:
                if key in out:
                    continue
                if re.fullmatch(pattern, label, re.IGNORECASE):
                    out[key] = value[:500]
                    break
    return out


def merge(*, packet: Optional[dict] = None,
          scope_review: Optional[dict] = None,
          updates: Optional[dict] = None,
          bid: Optional[dict] = None) -> tuple[dict, dict]:
    """
    PURE. Apply the precedence and return (values, sources).

    `sources` maps each populated field to where it came from, so the form can
    tell Jake "scope review" next to a value he didn't type. A field the human
    typed is NEVER overwritten by any automatic source — that's the rule that
    keeps this trustworthy.
    """
    layers = (
        (SOURCE_PACKET, packet or {}),
        (SOURCE_SCOPE_REVIEW, scope_review or {}),
        (SOURCE_UPDATES, updates or {}),
        (SOURCE_BID, bid or {}),
    )
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for source, layer in layers:
        for key, value in (layer or {}).items():
            if not _has(value) or key in values:
                continue
            values[key] = value
            sources[key] = source
    return values, sources
