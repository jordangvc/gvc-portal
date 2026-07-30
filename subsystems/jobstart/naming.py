"""
GVC job-naming standard — the pipe format.
=========================================================================
Jordan, 2026-07-29: "We prefer the -Pipe- | it looks so much better."

The standard is Jake's, from his Estimating Pipelines Reference ("Project Title"
pipeline), and his doc says it "applies everywhere: Monday.com, Drive, estimates,
invoices, photos, internal docs":

    [Street Name/Number] | [Builder/Client]

    9195 Silva | Willow Creek
    Lemon Tree | King
    3776 Susanna | Martin
    CO_9195 Silva | Willow Creek          (change order)

Rules, verbatim from his doc:
  • Pipe (|) separator, not — or -.
  • Only street/number + builder/client in the name. City, state, ZIP and
    description live in the record fields — not the title.
  • Simplify long names down to the two required pieces.
  • Missing piece → ASK, don't guess.

That last rule is why `to_standard()` never invents a builder. When it can't
find one it returns what it has and says so, and the UI asks Jake — a guessed
builder name propagates into Monday, Drive and invoices.

⚠ His doc's own worked example keeps THREE parts:
    ❌ 1254 Main Street, Cincinnati, OH 45202 – ABC Apartments – Premier Builders LLC
    ✅ 1254 Main Street | ABC Apartments | Premier Builders LLC
So a client AND a builder may both appear. Two or three parts are both valid;
what's never valid is city/state/ZIP or a job-type descriptor in the title.

WHY THIS MODULE ALSO OWNS MATCHING: adopt-or-create finds existing Projects and
Operations items BY NAME. Every item created before today is named in one of the
older conventions ("…_Warwick_Commercial", "… - Bryant - Jent Construction - New
House"). Renaming the standard without token-based matching would fail to find
those items and create duplicates — precisely the failure mode Joe's copy-pasted
automation caused. `match_score()` is the bridge across the convention change.
"""
from __future__ import annotations

import re
from typing import Optional

SEPARATOR = " | "

# Prefixes GVC already uses. Preserved verbatim on the front of a name.
PREFIX_RE = re.compile(r"^\s*(CO[_-]|STU-|PL-|WAR-)\s*", re.IGNORECASE)

# Any separator a legacy name might use, including the underscore style the
# Bid/Projects boards are full of.
_SPLIT_RE = re.compile(r"\s*(?:\||_|—|–|(?<=\s)-(?=\s))\s*|_+")

_STATES = {
    "oh", "ohio", "in", "indiana", "ky", "kentucky", "mi", "michigan",
    "wv", "pa", "tn", "il",
}

# Job-type / description words that belong in a field, never in the title.
_DESCRIPTORS = {
    "new house", "newhouse", "remodel", "addition", "multifamily", "commercial",
    "residential", "punch", "punch list", "touch up", "touchup", "hang only",
    "finish only", "whole house remodel", "basement", "finish basement",
    "drywall", "paint", "patching", "restoration", "service", "warranty",
    "multiple locations", "personal basement", "detached garage", "home office",
}

# Words that identify nothing when matching one job to another.
_NOISE = {
    "the", "and", "inc", "llc", "ltd", "co", "company", "corp", "contractors",
    "contracting", "construction", "builders", "builder", "development",
    "developers", "properties", "residence", "project", "job", "copy",
    "st", "street", "rd", "road", "ave", "avenue", "dr", "drive", "ln", "lane",
    "ct", "court", "way", "blvd", "cir", "circle", "pl", "place", "trl", "trail",
    "suite", "ste", "unit", "apt", "lot", "usa", "new", "house", "home",
    "remodel", "addition", "commercial", "residential", "basement", "garage",
}

_CITIES = {
    "cincinnati", "lawrenceburg", "brookville", "columbus", "loveland",
    "hamilton", "newport", "covington", "erlanger", "hebron", "mason",
    "westchester", "west", "chester", "harrison", "oldenburg", "moores", "hill",
    "north", "bend", "blueash", "ash", "montgomery", "lebanon", "dayton",
    "forest", "park", "milford", "glendale", "springfield", "montvale",
    "chicago", "kentucky", "indiana", "ohio",
}


def _is_geography(part: str) -> bool:
    """True when a name part is only city / state / ZIP."""
    words = re.findall(r"[A-Za-z]+|\d{5}", part.lower())
    if not words:
        return False
    for w in words:
        if w.isdigit() and len(w) == 5:
            continue
        if w in _STATES or w in _CITIES:
            continue
        return False
    return True


def _is_descriptor(part: str) -> bool:
    """True when a name part is a job-type description, not an identifier."""
    p = re.sub(r"[^a-z ]", "", part.lower()).strip()
    p = re.sub(r"\s+", " ", p)
    return p in _DESCRIPTORS


def _looks_like_street(part: str) -> bool:
    """A street part leads with a number, or ends in a street suffix."""
    s = part.strip()
    if re.match(r"^\d{2,6}\b", s):
        return True
    return bool(re.search(
        r"\b(st|street|rd|road|ave|avenue|dr|drive|ln|lane|ct|court|way|blvd|"
        r"cir|circle|pl|place|trl|trail|pike|hwy|highway)\b\.?$", s, re.I))


def simplify_street(part: str) -> str:
    """
    Street part → the short distinctive form Jake writes.

    His examples drop the street suffix and everything after it: "9195 Silva",
    "3776 Susanna". City / state / ZIP are stripped, since those live in fields.
    "9761 Gertrude Lane, Cincinnati OH 45231" → "9761 Gertrude".
    """
    s = part.strip().strip(",")
    s = re.split(r"\s*,\s*", s)[0]                      # drop ", Cincinnati OH"
    s = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", s)          # trailing ZIP
    s = re.sub(
        r"\s+(st|street|rd|road|ave|avenue|dr|drive|ln|lane|ct|court|way|blvd|"
        r"cir|circle|pl|place|trl|trail|pike|hwy|highway)\b\.?.*$", "", s,
        flags=re.IGNORECASE)
    # A trailing bare state abbreviation survives the comma split sometimes.
    s = re.sub(r"\s+(?:oh|in|ky|mi|wv|pa|tn|il)\b\.?$", "", s, flags=re.I)
    # A trailing city with no comma before it — "937 Madison Ridge Lawrenceburg".
    # Peel repeatedly so "…North Bend" goes too, but never strip the whole thing.
    for _ in range(3):
        m = re.search(r"\s+([A-Za-z]+)$", s)
        if m and m.group(1).lower() in _CITIES and len(s.split()) > 2:
            s = s[:m.start()]
        else:
            break
    return re.sub(r"\s{2,}", " ", s).strip(" ,-–—")


def _strip_descriptor_tail(part: str) -> str:
    """
    Remove a job-type descriptor stuck on the end of a part without a clean
    separator — "Steele Properties -New House" arrives as one part because the
    dash has no trailing space.
    """
    s = part.strip(" ,-–—_")
    for _ in range(2):
        m = re.search(r"[\s\-–—_]+([A-Za-z][A-Za-z ]{2,24})$", s)
        if not m:
            break
        tail = re.sub(r"\s+", " ", m.group(1).strip().lower())
        if tail in _DESCRIPTORS and len(s[:m.start()].strip()) >= 3:
            s = s[:m.start()].strip(" ,-–—_")
        else:
            break
    return s


def parse_parts(raw: str) -> list[str]:
    """PURE. Any legacy name → its meaningful parts, geography and job-type
    descriptors removed, order preserved."""
    body = PREFIX_RE.sub("", raw or "")
    parts = [_strip_descriptor_tail(p) for p in _SPLIT_RE.split(body)]
    return [p for p in parts
            if p and not _is_geography(p) and not _is_descriptor(p)]


def is_standard(name: str) -> bool:
    """True when `name` already follows the pipe standard."""
    if not name or "|" not in name:
        return False
    body = PREFIX_RE.sub("", name)
    parts = [p.strip() for p in body.split("|")]
    if not (2 <= len(parts) <= 3) or any(not p for p in parts):
        return False
    # City/state/ZIP or a descriptor in the title means it isn't standard yet.
    return not any(_is_geography(p) or _is_descriptor(p) for p in parts)


def to_standard(raw: str, *, builder_hint: Optional[str] = None,
                customer_hint: Optional[str] = None) -> dict:
    """
    PURE. Messy job name → the pipe standard.

    Returns {name, ok, street, builder, note}. `ok` is False when a required
    piece is missing — per Jake's rule the caller ASKS rather than guessing, and
    `note` says what's missing.

    Hints come from the bid's own Customer link, which is a recorded fact rather
    than a guess, so it's allowed to supply the builder when the title doesn't.
    """
    raw = (raw or "").strip()
    if not raw:
        return {"name": "", "ok": False, "street": None, "builder": None,
                "note": "No job name to work from."}

    prefix_m = PREFIX_RE.match(raw)
    prefix = ""
    if prefix_m:
        p = prefix_m.group(1).upper()
        prefix = "CO_" if p.startswith("CO") else p

    if is_standard(raw):
        return {"name": raw.strip(), "ok": True,
                "street": None, "builder": None, "note": ""}

    parts = parse_parts(raw)

    street = next((p for p in parts if _looks_like_street(p)), None)
    rest = [p for p in parts if p is not street]

    # Builder: the title's remaining part, else the bid's linked customer.
    builder = rest[0] if rest else None
    extra = rest[1] if len(rest) > 1 else None
    if not builder:
        builder = (builder_hint or customer_hint or "").strip() or None

    if street:
        street = simplify_street(street)
    elif parts:
        # No street number anywhere — Jake's "Lemon Tree | King" shape. First
        # part stands in as the identifier.
        street = simplify_street(parts[0])
        rest2 = parts[1:]
        builder = (rest2[0] if rest2 else
                   (builder_hint or customer_hint or "").strip() or None)
        extra = rest2[1] if len(rest2) > 1 else None

    pieces = [p for p in (street, builder, extra) if p]
    name = prefix + SEPARATOR.join(pieces)

    if not street:
        return {"name": name, "ok": False, "street": None, "builder": builder,
                "note": "Couldn't find a street/number in the name."}
    if not builder:
        return {"name": name, "ok": False, "street": street, "builder": None,
                "note": "No builder/client found — add one, don't let it guess."}
    return {"name": name, "ok": True, "street": street, "builder": builder,
            "note": ""}


# ---------------------------------------------------------------------------
# Matching across the convention change — the duplicate safeguard
# ---------------------------------------------------------------------------

def tokens(name: str) -> set:
    """
    PURE. Name → distinctive lowercase tokens. Street numbers and builder names
    survive; separators, geography, street suffixes and boilerplate do not — so
    a legacy underscore name and its new pipe form yield the SAME token set.
    """
    text = PREFIX_RE.sub("", name or "").lower()
    raw = re.findall(r"[a-z0-9]+", text)
    out = set()
    for t in raw:
        if t.isdigit():
            if len(t) == 5:          # ZIP — geography, not an identifier
                continue
            out.add(t)
        elif len(t) > 2 and t not in _NOISE and t not in _CITIES and t not in _STATES:
            out.add(t)
    return out


def match_score(a: str, b: str) -> float:
    """
    PURE. 0.0–1.0 similarity between two job names, ignoring convention.

    Jaccard over distinctive tokens, with a strong boost when both names share a
    street number — that number is the single most identifying thing in a GVC
    job name, and two jobs almost never share one.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    score = len(inter) / len(ta | tb)
    nums_a = {t for t in ta if t.isdigit()}
    nums_b = {t for t in tb if t.isdigit()}
    if nums_a & nums_b:
        score = min(1.0, score + 0.35)
    return score


# Above this, two names are the same job. Tuned so a legacy name and its pipe
# form match (they share the street number plus the builder token) while two
# different jobs for the same builder do NOT.
MATCH_THRESHOLD = 0.5


def best_match(name: str, candidates: list) -> Optional[dict]:
    """
    PURE. Pick the candidate that is the same job as `name`, or None.

    `candidates` are dicts with at least {id, name}. An exact name match always
    wins; otherwise the highest scorer above MATCH_THRESHOLD, and only when it
    beats the runner-up — an ambiguous match is treated as no match, because
    adopting the wrong item is worse than creating a new one.
    """
    if not name or not candidates:
        return None
    target = name.strip().casefold()
    for c in candidates:
        if (c.get("name") or "").strip().casefold() == target:
            return {**c, "score": 1.0, "how": "exact"}

    scored = sorted(
        ({**c, "score": match_score(name, c.get("name") or "")}
         for c in candidates),
        key=lambda c: c["score"], reverse=True)
    if not scored or scored[0]["score"] < MATCH_THRESHOLD:
        return None
    if len(scored) > 1 and scored[1]["score"] >= scored[0]["score"] - 0.05:
        return None                      # ambiguous ⇒ don't adopt
    return {**scored[0], "how": "tokens"}
