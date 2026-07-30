"""
Scope-review parser — the packet's PRIMARY data source.
=========================================================================
Jordan, 10:43 meeting 2026-07-29:
  "the handoff has to pull the data from somewhere or it becomes an input job
   for you. We don't want an input job for you."
  "your scope review is going to be the most valuable. So ideally, the project
   handoff sheet to the operations initially is built off of your scope review."

Jake's scope review lives in his Drive `completed plans` folder, one per job
folder. It is the document he already writes while estimating, so reading it
instead of asking him to retype it is the whole point.

Jake, same meeting, on why it's the right source:
  "that lists a lot of things that Rob might have minor questions on… it's easy
   for Rob to review it and then he can kind of gather, okay this project calls
   for this, calls for that."

STRUCTURE (verified against real docs 2026-07-29 — "KPMG Cincinnati Renovation
Scope Review" 1s2bmD96…, plus the Schuler Wool Mill / 4270 Glendale Milford PDF
exports of the same template):

    # {PROJECT} — SCOPE REVIEW
    Green Valley Contractors | Prepared: 6/8/2026

    PROJECT INFO
    - Project Name: …
    - Address: …
    - GC / Contractor: CMSquared (5777 Kellogg Ave… — Matt Salee, 513.827.6890)
    - Scope Type: Commercial Tenant Improvement / Interior Renovation
    …
    FRAMING (Light Gauge Metal Framing)
    INSULATION
    DRYWALL
    FRP (Fiberglass Reinforced Panels)
    ACT (Acoustic Ceiling Tile and Grid)
    PLYWOOD
    CEMENT BOARD
    PAINTING
    NOTES                      ← where NIC / "not in GVC scope" / "by others" live
    # Walk Through Notes       ← allowances + decisions from the site walk

THREE extractions, in value order:

1. **[NEEDS CLARIFICATION] lines** — the open questions. This is the single most
   valuable thing in the document and nothing else in GVC's systems holds it.
   These are the items that become change orders when nobody reads them.
2. **Exclusions** — NIC / "not in contract" / "NOT in GVC scope" / "by others" /
   "No X scope found". Exclusions are the packet field that saves us.
3. **Scope** — the trade sections that actually have work in them, so ops reads
   "Framing, Drywall, ACT, Painting" rather than eight headings of prose.

Everything here is PURE (no I/O) so it can be tested against real doc text.
Drive lookup lives in adapters/drive.py; wiring lives in jobstart_flow.
"""
from __future__ import annotations

import re
from typing import Optional

# Trade headings, in the template's own order. Matched case-insensitively at
# line start, optionally followed by a parenthetical ("FRP (Fiberglass…)").
TRADE_SECTIONS: tuple[str, ...] = (
    "FRAMING", "INSULATION", "DRYWALL", "FRP", "ACT", "PLYWOOD",
    "CEMENT BOARD", "PAINTING",
)

# Sections that are not trades but that we read.
INFO_SECTION = "PROJECT INFO"
NOTES_SECTION = "NOTES"
WALKTHROUGH_SECTION = "WALK THROUGH NOTES"

ALL_SECTIONS = TRADE_SECTIONS + (INFO_SECTION, NOTES_SECTION, WALKTHROUGH_SECTION)

CLARIFICATION_MARKER = "[NEEDS CLARIFICATION]"

# A line is an exclusion when it says the work isn't ours. Ordered most- to
# least-specific; the phrasing set came from the real docs, not invented.
EXCLUSION_PATTERNS: tuple[str, ...] = (
    r"\bNIC\b",
    r"not in contract",
    r"not\s+in\s+GVC\s+scope",
    r"NOT\s+GVC",
    r"\bby others\b",
    r"by\s+the\s+(?:GC|owner|painter)",
    r"owner-furnished",
    r"No\s+\w[\w\s/()-]*scope\s+found",
    r"\bexcluded\b",
    r"\bnot\s+by\s+us\b",
)
_EXCLUSION_RE = re.compile("|".join(EXCLUSION_PATTERNS), re.IGNORECASE)

# "No FRP scope found in plans." ⇒ that trade is empty, don't list it as scope.
_EMPTY_TRADE_RE = re.compile(r"^\s*[-•]?\s*No\s+[\w\s/()-]*scope\s+found",
                             re.IGNORECASE)


def _trade_label(name: str) -> str:
    """Display form of a trade heading. ACT and FRP are acronyms and must not
    be title-cased into 'Act' / 'Frp'."""
    return name if name in ("ACT", "FRP") else name.title()


def _clean_line(raw: str) -> str:
    """
    One raw line → readable text.

    The Google Docs export escapes markdown punctuation with backslashes — a
    bullet arrives as `\\-` and, load-bearingly, the marker arrives as
    `\\[NEEDS CLARIFICATION\\]`. Unescaping has to happen BEFORE anything
    matches on that marker, or every open question is silently missed (it was,
    on the first run against the real doc).
    """
    s = raw.replace("​", "").replace("﻿", "").strip()
    # Markdown unescape: \X → X for the punctuation Docs escapes.
    s = re.sub(r"\\([\[\]()\-*#.!_+|>~`])", r"\1", s)
    s = re.sub(r"^[-•*‣]\s*", "", s)           # bullet glyphs
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)     # **bold**
    s = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", s)
    s = re.sub(r"^#+\s*", "", s)               # markdown headings
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _section_of(line: str) -> Optional[str]:
    """
    Return the canonical section name if this line is a section heading.
    Headings are ALL-CAPS in the template, optionally with a parenthetical and
    optionally wrapped in markdown heading/bold syntax.
    """
    s = _clean_line(line)
    if not s or len(s) > 70:
        return None
    # Drop a trailing parenthetical: "FRP (Fiberglass Reinforced Panels)"
    core = re.sub(r"\s*\(.*?\)\s*$", "", s).strip().rstrip(":").strip()
    upper = core.upper()
    if upper in ALL_SECTIONS:
        return upper
    # "Walk Through Notes" is title-case in the real doc.
    if upper.replace("-", " ") == WALKTHROUGH_SECTION:
        return WALKTHROUGH_SECTION
    # Never treat a normal sentence as a heading: real headings have no
    # lowercase letters outside the parenthetical we already removed.
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """
    PURE. Document text → {SECTION: [clean content lines]}.
    Lines before the first recognised heading are dropped (title block).
    """
    out: dict[str, list[str]] = {}
    current: Optional[str] = None
    for raw in (text or "").splitlines():
        heading = _section_of(raw)
        if heading:
            current = heading
            out.setdefault(current, [])
            continue
        if current is None:
            continue
        line = _clean_line(raw)
        if line:
            out[current].append(line)
    return out


def parse_project_info(lines: list[str]) -> dict[str, str]:
    """
    PURE. PROJECT INFO lines → {label: value}. Labels keep their document
    spelling ("GC / Contractor") so nothing is silently renamed; callers look
    them up through the helpers below.
    """
    info: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^([A-Za-z][A-Za-z /&#-]{2,40}?)\s*:\s*(.+)$", line)
        if m:
            info[m.group(1).strip()] = m.group(2).strip()
    return info


def _info_get(info: dict[str, str], *candidates: str) -> Optional[str]:
    """Case/spacing-insensitive lookup across several possible labels."""
    norm = {re.sub(r"[^a-z]", "", k.lower()): v for k, v in info.items()}
    for cand in candidates:
        hit = norm.get(re.sub(r"[^a-z]", "", cand.lower()))
        if hit:
            return hit
    return None


def contractor(info: dict[str, str]) -> Optional[str]:
    """The GC/builder. 'CMSquared (5777 Kellogg… — Matt Salee, 513.827.6890)'
    → 'CMSquared' — the parenthetical is address+contact, not the name."""
    raw = _info_get(info, "GC / Contractor", "GC/Contractor", "Contractor",
                    "General Contractor", "Builder", "GC")
    if not raw:
        return None
    return re.sub(r"\s*\(.*$", "", raw).strip() or None


def contractor_contact(info: dict[str, str]) -> Optional[str]:
    """The name + phone inside the contractor parenthetical, when present —
    'Matt Salee, 513.827.6890'. This is who ops actually calls."""
    raw = _info_get(info, "GC / Contractor", "GC/Contractor", "Contractor",
                    "General Contractor", "Builder", "GC")
    if not raw:
        return None
    # GREEDY to the last ")" on purpose: a contact's own phone number carries
    # parentheses — "Dave K (513) 555-0142" — and a non-greedy match stops
    # inside the area code, handing ops "Dave K (513".
    m = re.search(r"\((.*)\)", raw)
    if not m:
        return None
    inner = m.group(1)
    # The template separates address from contact with a spaced dash. The real
    # docs use an em-dash, but these are hand-written, so a plain hyphen counts
    # too. Prefer whichever part carries a phone number — an address can contain
    # a spaced hyphen ("Suite 100 - Bldg A") and taking the tail blindly would
    # hand ops a fragment of the street address instead of a person to call.
    parts = [p.strip() for p in re.split(r"\s+[—–-]\s+", inner) if p.strip()]
    if not parts:
        return None
    phone = re.compile(r"\(?\d{3}\)?[.\s-]?\d{3}[.\s-]?\d{4}")
    for part in reversed(parts):
        if phone.search(part):
            return part
    return parts[-1] or None


def project_type(info: dict[str, str]) -> Optional[str]:
    """Map the doc's Scope Type onto the Monday Project Type labels."""
    raw = (_info_get(info, "Scope Type", "Project Type", "Type of Work") or "").lower()
    if not raw:
        return None
    if "commercial" in raw or "tenant" in raw or "office" in raw:
        return "Commercial"
    if "residential" in raw or "house" in raw or "home" in raw:
        return "Residential"
    return None


def clarifications(sections: dict[str, list[str]]) -> list[dict]:
    """
    PURE. Every [NEEDS CLARIFICATION] line, tagged with the trade it came from.
    THE highest-value extraction: these are the open questions that turn into
    change orders when they reach the field unread.
    """
    out: list[dict] = []
    for name, lines in sections.items():
        for line in lines:
            if CLARIFICATION_MARKER.lower() in line.lower():
                text = re.sub(re.escape(CLARIFICATION_MARKER), "", line,
                              flags=re.IGNORECASE).strip(" -—:")
                if text:
                    out.append({"trade": name.title(), "question": text})
    return out


def exclusions(sections: dict[str, list[str]]) -> list[str]:
    """
    PURE. Lines that say work is NOT ours, from anywhere in the document.
    NOTES carries most of them, but "washrooms are NIC" shows up inline under
    DRYWALL too, so every section is swept. Deduped, document order preserved.
    """
    out: list[str] = []
    seen: set[str] = set()
    order = [INFO_SECTION, *TRADE_SECTIONS, NOTES_SECTION, WALKTHROUGH_SECTION]
    for name in order:
        for line in sections.get(name, []):
            if CLARIFICATION_MARKER.lower() in line.lower():
                continue                       # a question, not an exclusion
            if not _EXCLUSION_RE.search(line):
                continue
            key = re.sub(r"\W+", "", line.lower())[:80]
            if key and key not in seen:
                seen.add(key)
                out.append(line)
    return out


def trades_in_scope(sections: dict[str, list[str]]) -> list[str]:
    """
    PURE. Trade sections that actually contain work. A section whose only
    substantive line is "No FRP scope found in plans" is NOT in scope.
    """
    live: list[str] = []
    for name in TRADE_SECTIONS:
        lines = sections.get(name)
        if not lines:
            continue
        substantive = [l for l in lines
                       if not _EMPTY_TRADE_RE.match(l)
                       and CLARIFICATION_MARKER.lower() not in l.lower()]
        if substantive:
            live.append(_trade_label(name))
    return live


def scope_summary(sections: dict[str, list[str]], *, max_lines: int = 4) -> str:
    """
    PURE. A readable scope paragraph for the packet: the live trades, then the
    leading detail line from each. Deliberately short — the full document is
    always one link away, and ops reads this from a phone.
    """
    live = trades_in_scope(sections)
    if not live:
        return ""
    parts = [f"Trades in scope: {', '.join(live)}."]
    for name in TRADE_SECTIONS:
        if _trade_label(name) not in live:
            continue
        for line in sections.get(name, []):
            if _EMPTY_TRADE_RE.match(line):
                continue
            if CLARIFICATION_MARKER.lower() in line.lower():
                continue
            parts.append(f"{_trade_label(name)}: {line}")
            break
        if len(parts) > max_lines:
            break
    return "\n".join(parts)


def parse(text: str) -> dict:
    """
    PURE. The whole document → the structure jobstart_flow prefills from.

    Returns {found, sections, project_info, scope, exclusions, clarifications,
             trades, contractor, contractor_contact, project_type, address,
             project_name, walkthrough}
    `found` is False for text that doesn't look like a scope review at all, so
    the caller can fall back to the Bid Board rather than prefill nonsense.
    """
    sections = split_sections(text or "")
    info = parse_project_info(sections.get(INFO_SECTION, []))
    trades = trades_in_scope(sections)
    found = bool(info or trades)

    excl = exclusions(sections)
    return {
        "found": found,
        "sections": sections,
        "project_info": info,
        "project_name": _info_get(info, "Project Name", "Project"),
        "address": _info_get(info, "Address", "Job Address", "Project Address"),
        "contractor": contractor(info),
        "contractor_contact": contractor_contact(info),
        "project_type": project_type(info),
        "trades": trades,
        "scope": scope_summary(sections),
        "exclusions": "\n".join(f"- {e}" for e in excl) if excl else "",
        "exclusion_lines": excl,
        "clarifications": clarifications(sections),
        "walkthrough": "\n".join(sections.get(WALKTHROUGH_SECTION, [])),
    }
