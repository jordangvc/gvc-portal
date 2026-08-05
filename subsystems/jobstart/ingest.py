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

So there are six sources, and they have a strict precedence. Anything a human
typed wins; below that, the most specific source wins:

    1. the saved packet          — what Jake actually typed, never overwritten
    2. the scope review (Drive)  — the richest source, and his own words say primary
    3. the estimate sidecar      — as-sent estimate JSON from Drive
    4. Monday board updates      — Project/Ops item updates, so the packet stays current
    5. prior builder packets     — soft finish/spec defaults from accepted handoffs
    6. the Bid Board columns     — the thin fallback

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
SOURCE_ESTIMATE = "estimate"
SOURCE_UPDATES = "updates"
SOURCE_HISTORY = "history"
SOURCE_BID = "bid"

PRECEDENCE = (
    SOURCE_PACKET,
    SOURCE_SCOPE_REVIEW,
    SOURCE_ESTIMATE,
    SOURCE_UPDATES,
    SOURCE_HISTORY,
    SOURCE_BID,
)

# Finish/spec fields that may repeat per builder — safe to inherit from a prior
# accepted packet. Never scope, exclusions, board count, dates, or access codes
# that are job-specific.
HISTORY_SOFT_FIELDS = frozenset({
    "ceiling_finish",
    "garage_finish",
    "window_type",
    "window_returns",
    "scaffold",
    "heater_cans",
    "shower",
    "lock_box",
})


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


def _format_contact(name: str, phone: str) -> str:
    """Name plus phone when the phone isn't already embedded in the name."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name:
        return phone
    if not phone:
        return name
    if phone.replace("(", "").replace(")", "").replace("-", "").replace(" ", "") in (
            name.replace("(", "").replace(")", "").replace("-", "").replace(" ", "")):
        return name
    if "(" in name and ")" in name:
        return name
    return f"{name} ({phone})"


def _flatten_scope(data: dict) -> str:
    """
    Build scope prose from scope_details hierarchy or line-item detail fields
    when no scope_summary exists on the estimate sidecar.
    """
    est = data.get("estimate") or {}
    parts: list[str] = []

    scope_details = est.get("scope_details") or data.get("scope_details")
    if scope_details:
        for trade_grp in scope_details:
            trade = (trade_grp.get("trade") or "").strip()
            for scope in trade_grp.get("scopes") or []:
                title = (scope.get("title") or "").strip()
                bullets = scope.get("bullets") or []
                if title:
                    parts.append(f"{trade} — {title}" if trade else title)
                for bullet in bullets:
                    text = str(bullet or "").strip()
                    if text:
                        parts.append(f"• {text}")

    if not parts:
        for li in est.get("line_items") or []:
            detail = (li.get("scope_detail") or li.get("detail") or "").strip()
            desc = (li.get("description") or "").strip()
            if detail:
                parts.append(f"{desc}: {detail}" if desc else detail)
            elif desc:
                parts.append(f"• {desc}")

    return "\n".join(parts).strip()


def _notes_to_packet_fields(est: dict) -> dict:
    """
    Map estimate notes into open_questions vs allowances.

    Lines that look like questions/clarifications → open_questions; everything
    else from special_notes lands in allowances (Jordan: don't make Sales retype
    what Jake already wrote on the estimate).
    """
    out: dict[str, str] = {}
    special = est.get("special_notes")
    if isinstance(special, str):
        special = [special] if special.strip() else []
    special = list(special or [])

    notes = (est.get("notes") or "").strip()
    questions: list[str] = []
    allowances: list[str] = []

    def _classify(text: str) -> None:
        text = text.strip()
        if not text:
            return
        lower = text.lower()
        if ("?" in text or "clarif" in lower or "question" in lower
                or lower.startswith("open:") or lower.startswith("tbd")):
            questions.append(text)
        else:
            allowances.append(text)

    for note in special:
        _classify(str(note))

    if notes:
        _classify(notes)

    if questions:
        out["open_questions"] = "\n".join(questions)
    if allowances:
        out["allowances"] = "\n".join(allowances)
    elif special and not questions:
        out["allowances"] = "\n".join(str(s).strip() for s in special if str(s).strip())

    return out


def from_estimate(data: dict) -> dict:
    """
    PURE. As-sent estimate JSON (example_estimate.json / sidecar shape) → packet
    field values. Only fields the estimate genuinely carries — never invent
    exclusions or customer emails that aren't in the data.
    """
    if not data:
        return {}

    out: dict[str, str] = {}
    job = data.get("job") or {}
    est = data.get("estimate") or {}
    client = data.get("client") or {}

    scope = (job.get("scope_summary") or est.get("scope_summary") or "").strip()
    if not scope:
        scope = _flatten_scope(data)
    if scope:
        out["scope"] = scope

    builder = (client.get("name") or "").strip()
    if builder:
        out["builder"] = builder

    contact = (client.get("contact_name") or "").strip()
    phone = (client.get("phone") or "").strip()
    if contact:
        out["supervisor"] = _format_contact(contact, phone)
        out["gc_pm"] = contact

    email = (client.get("email") or "").strip()
    if email:
        out["gc_email"] = email

    lot = (job.get("lot") or est.get("lot") or "").strip()
    if lot:
        out["lot"] = lot

    # Only map dates when the sidecar names them as start/finish — estimate issue
    # date is NOT a mobilization date.
    for container, key, target in (
        (job, "start_date", "start_date"),
        (job, "expected_finish", "expected_finish"),
        (est, "start_date", "start_date"),
        (est, "expected_finish", "expected_finish"),
    ):
        val = (container.get(key) or "").strip()
        if val and target not in out:
            out[target] = val

    expiry = (est.get("expiry_date") or "").strip()
    if expiry and "expected_finish" not in out:
        out["expected_finish"] = expiry

    for container in (est, job, data):
        excl = (container.get("exclusions") or "").strip()
        if excl:
            out["exclusions"] = excl
            break

    out.update(_notes_to_packet_fields(est))

    return out


def from_history(prior: dict) -> dict:
    """
    PURE. Soft defaults from a prior accepted packet for the same builder.

    Only finish/spec fields that tend to repeat per GC — never scope, exclusions,
    board count, start date, or other job-unique required fields.
    """
    if not prior:
        return {}
    out: dict[str, str] = {}
    for key in HISTORY_SOFT_FIELDS:
        val = prior.get(key)
        if _has(val):
            out[key] = str(val).strip()
    return out


def merge(*, packet: Optional[dict] = None,
          scope_review: Optional[dict] = None,
          estimate: Optional[dict] = None,
          updates: Optional[dict] = None,
          history: Optional[dict] = None,
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
        (SOURCE_ESTIMATE, estimate or {}),
        (SOURCE_UPDATES, updates or {}),
        (SOURCE_HISTORY, history or {}),
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
