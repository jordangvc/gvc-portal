"""
GVC job-naming standard — 3-part pipe with Job Title (Jordan 2026-08-06).
=========================================================================
Jordan, 2026-07-29: "We prefer the -Pipe- | it looks so much better."
Jordan, 2026-08-05: city / state / ZIP MUST be in the title — GVC works in
three states and many share the same road names. Job type still stays out.
Jordan, 2026-08-06: Job Title is a REQUIRED third pipe segment.

Applies everywhere: Monday.com, Drive, estimates, invoices, CRM, photos,
internal docs:

    [Street Number Name], [City], [ST] [ZIP] | [Builder] | [Job Title]

    9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence
    100 Main Street, Cincinnati, OH 45202 | ABC Builders | First Financial Bank
    CO_9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence

Rules:
  • Pipe (|) separators — not — or -. Exactly three non-empty parts.
  • Left: street + city + state + ZIP. Middle: builder. Right: job title.
  • Commercial job title = business name. Residential = "{Last} residence".
  • Job-type descriptors ("New House", "Remodel") stay in record fields —
    never in the title.
  • Missing builder, city/state/ZIP, or job title → ASK, don't guess.

That last rule is why `to_standard()` never invents a builder, city, or job
title. When a piece is missing it returns what it has and says so, and the
UI asks — a guessed piece propagates into Monday, Drive, CRM and invoices.

WHY THIS MODULE ALSO OWNS MATCHING: adopt-or-create finds existing Projects
and Operations items BY NAME. Every item created before today is named in
one of the older conventions (short "Street | Builder", 2-part with geo,
underscore, dashes). Renaming the standard without token-based matching
would fail to find those items and create duplicates. `match_score()` is
the bridge across the convention change — tokens intentionally IGNORE city
/ state / ZIP and the word "residence" so a 2-part legacy name and its
3-part form still score as the same job.
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

_STATE_TO_ABBR = {
    "oh": "OH", "ohio": "OH",
    "in": "IN", "indiana": "IN",
    "ky": "KY", "kentucky": "KY",
    "mi": "MI", "michigan": "MI",
    "wv": "WV", "westvirginia": "WV", "west": "WV",  # "west virginia" handled below
    "pa": "PA", "pennsylvania": "PA",
    "tn": "TN", "tennessee": "TN",
    "il": "IL", "illinois": "IL",
}

_STATES = set(_STATE_TO_ABBR.keys()) | {
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
    "chicago", "kentucky", "indiana", "ohio", "florence", "independence",
    "burlington", "union", "fort", "thomas", "highland", "heights",
    "alexandria", "batavia", "goshen", "maineville", "morrow", "south",
    "lebanon", "deerfield", "township", "symmes", "sycamore", "sharonville",
    "evendale", "reading", "norwood", "wyoming", "lockland", "elmwood",
    "place", "cheviot", "delhi", "green", "aurora", "batesville",
}


def _is_geography(part: str) -> bool:
    """True when a name part is only city / state / ZIP (no street)."""
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


def _normalize_state(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = re.sub(r"[^a-z]", "", raw.lower())
    if key == "westvirginia":
        return "WV"
    return _STATE_TO_ABBR.get(key)


def parse_location(text: str) -> dict:
    """
    PURE. Free-text address blob → {street, city, state, zip}.

    Tolerates common Monday shapes:
      "9761 Gertrude Lane, Cincinnati, OH 45231"
      "9761 Gertrude Lane, Cincinnati OH 45231"
      "9761 Gertrude Lane Cincinnati OH 45231"
      "Cincinnati, OH 45231"          (city-only hint from location5)
    Missing pieces come back as None — callers ask rather than invent.
    """
    s = re.sub(r"\s+", " ", (text or "").strip().strip(" ,-–—_|"))
    if not s:
        return {"street": None, "city": None, "state": None, "zip": None}

    # ZIP: prefer a trailing 5-digit code. Never steal a leading street number
    # like "21435 Abbys…" (also five digits).
    zip_code = None
    zm = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", s)
    if zm and not (zm.start() == 0 and re.match(r"^\d{5}\s+[A-Za-z]", s)):
        zip_code = zm.group(1)
        s = s[:zm.start()].strip(" ,-–—")
    else:
        for m in re.finditer(r"\b(\d{5})(?:-\d{4})?\b", s):
            if m.start() == 0:
                continue  # leading street number
            zip_code = m.group(1)
            s = (s[:m.start()] + " " + s[m.end():]).strip(" ,-–—")
            break

    state = None
    # Prefer an explicit 2-letter abbr near the end; else a full state name.
    sm = re.search(
        r"(?:,\s*)?\b(OH|IN|KY|MI|WV|PA|TN|IL|Ohio|Indiana|Kentucky|"
        r"Michigan|Pennsylvania|Tennessee|Illinois|West\s+Virginia)\b\.?\s*$",
        s, re.I)
    if sm:
        state = _normalize_state(sm.group(1))
        s = s[:sm.start()].strip(" ,-–—")

    city = None
    street = None
    if "," in s:
        # Only split on the FIRST comma: "Street, City" (state/ZIP already gone).
        left, right = s.split(",", 1)
        left, right = left.strip(" ,-–—"), right.strip(" ,-–—")
        # Drop any leftover commas inside the city blob.
        right = right.split(",")[0].strip(" ,-–—") if right else right
        if _looks_like_street(left) or re.match(r"^\d{2,6}\b", left):
            street = left
            city = right or None
        elif not left:
            city = right or None
        else:
            if _is_geography(left) or (not re.search(r"\d", left)):
                city = left
                if right and _looks_like_street(right):
                    street = right
            else:
                street = left
                city = right or None
    else:
        # No comma: try to peel a known trailing city word(s).
        street = s
        for n in (3, 2, 1):
            words = street.split()
            if len(words) <= n + 1:
                continue
            tail_tokens = [w.lower() for w in words[-n:]]
            if all(t in _CITIES for t in tail_tokens):
                city = " ".join(words[-n:])
                street = " ".join(words[:-n]).strip(" ,-–—")
                break

    if street:
        street = re.sub(r"\s{2,}", " ", street).strip(" ,-–—")
    if city:
        city = re.sub(r"\s{2,}", " ", city).strip(" ,-–—")
        city = " ".join(
            w if w.isupper() and len(w) <= 2 else w.capitalize()
            for w in city.split())

    # If the entire blob was geography (location hint), street stays None.
    if street and _is_geography(street) and not re.search(r"\d", street):
        if not city:
            city = street
        street = None

    return {"street": street or None, "city": city or None,
            "state": state, "zip": zip_code}


def format_location(street: Optional[str] = None, city: Optional[str] = None,
                    state: Optional[str] = None, zip_code: Optional[str] = None,
                    *, raw: Optional[str] = None) -> str:
    """
    PURE. Pieces (or a raw blob) → canonical left-side location string.

    `Street, City, ST ZIP` — city/state/ZIP omitted when unknown (caller
    decides whether that's ok via `location_complete`).
    """
    if raw and not any((street, city, state, zip_code)):
        parsed = parse_location(raw)
        street, city, state, zip_code = (
            parsed["street"], parsed["city"], parsed["state"], parsed["zip"])
    state = _normalize_state(state) if state and len(state) != 2 else (
        (state or "").upper() or None)
    if state and len(state) != 2:
        state = _normalize_state(state)
    street = (street or "").strip(" ,-–—") or None
    city = (city or "").strip(" ,-–—") or None
    zip_code = (zip_code or "").strip() or None

    if not street and not city:
        return ""
    if not street:
        # City-only hint — not a full title by itself.
        bits = [city]
        if state and zip_code:
            bits = [f"{city}, {state} {zip_code}"]
        elif state:
            bits = [f"{city}, {state}"]
        return bits[0]

    if city and state and zip_code:
        return f"{street}, {city}, {state} {zip_code}"
    if city and state:
        return f"{street}, {city}, {state}"
    if city and zip_code:
        return f"{street}, {city} {zip_code}"
    if city:
        return f"{street}, {city}"
    if state and zip_code:
        return f"{street}, {state} {zip_code}"
    return street


def location_complete(loc: dict) -> bool:
    """True when street + city + state + ZIP are all present."""
    return bool(loc.get("street") and loc.get("city")
                and loc.get("state") and loc.get("zip"))


def simplify_street(part: str) -> str:
    """
    Back-compat alias: normalize a location blob to the canonical left side.

    Historically this STRIPPED city/state/ZIP. Jordan 2026-08-05 reversed that
    — those pieces stay. Street suffixes (Drive, Lane) are kept when present.
    """
    return format_location(raw=part) or (part or "").strip()


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
    """PURE. Any legacy name → its meaningful parts, geography-ONLY parts and
    job-type descriptors removed, order preserved.

    City/state/ZIP glued onto the street with commas stay inside the street
    part (they are not a separate pipe segment).
    """
    body = PREFIX_RE.sub("", raw or "")
    parts = [_strip_descriptor_tail(p) for p in _SPLIT_RE.split(body)]
    return [p for p in parts
            if p and not _is_geography(p) and not _is_descriptor(p)]


def _merge_location(primary: dict, hint: dict) -> dict:
    """Fill missing primary fields from a location hint (e.g. Monday location5)."""
    out = dict(primary)
    for key in ("city", "state", "zip"):
        if not out.get(key) and hint.get(key):
            out[key] = hint[key]
    ps = (out.get("street") or "").strip()
    hs = (hint.get("street") or "").strip()
    if not ps and hs:
        out["street"] = hs
    elif ps and hs and hs.lower().startswith(ps.lower()) and len(hs) > len(ps):
        # Hint often has the fuller street ("9195 Silva Drive" vs title "9195 Silva").
        out["street"] = hs
    elif not out.get("street"):
        out["street"] = hs or None
    return out


def format_job_title(*, project_type: Optional[str] = None,
                     homeowner_last: Optional[str] = None,
                     business_name: Optional[str] = None) -> Optional[str]:
    """
    PURE. Build the required third pipe segment from recorded facts.

    Residential → "{Last} residence" (last token of a full name; never doubles
    an existing trailing "residence"). Commercial → business_name as-is.
    Unknown type prefers business_name, else residential form. Never invents
    from empty inputs — returns None so the caller asks.
    """
    pt = (project_type or "").strip().lower()
    biz = (business_name or "").strip() or None
    home = (homeowner_last or "").strip() or None

    def _residence(raw: str) -> str:
        s = raw.strip()
        if s.lower().endswith("residence"):
            return s
        last = s.split()[-1]
        return f"{last} residence"

    if "res" in pt:
        return _residence(home) if home else None
    if "comm" in pt:
        return biz
    if biz:
        return biz
    if home:
        return _residence(home)
    return None


def _resolve_job_title(*, parsed: Optional[str] = None,
                       job_title_hint: Optional[str] = None,
                       project_type: Optional[str] = None,
                       homeowner_last: Optional[str] = None,
                       business_name: Optional[str] = None) -> Optional[str]:
    """Prefer an already-parsed third part, then an explicit hint, then format."""
    if parsed and parsed.strip():
        return parsed.strip()
    hint = (job_title_hint or "").strip()
    if hint:
        return hint
    return format_job_title(project_type=project_type,
                            homeowner_last=homeowner_last,
                            business_name=business_name)


def is_standard(name: str) -> bool:
    """True when `name` is the 3-part pipe + city/state/ZIP + job-title standard."""
    if not name or "|" not in name:
        return False
    body = PREFIX_RE.sub("", name)
    parts = [p.strip() for p in body.split("|")]
    if len(parts) != 3 or any(not p for p in parts):
        return False
    if any(_is_descriptor(p) for p in parts):
        return False
    # Left side must carry street + city + state + ZIP.
    loc = parse_location(parts[0])
    return location_complete(loc)


def to_standard(raw: str, *, builder_hint: Optional[str] = None,
                customer_hint: Optional[str] = None,
                location_hint: Optional[str] = None,
                job_title_hint: Optional[str] = None,
                project_type: Optional[str] = None,
                homeowner_last: Optional[str] = None,
                business_name: Optional[str] = None) -> dict:
    """
    PURE. Messy job name → the 3-part pipe + city/state/ZIP + job-title standard.

    Returns {name, ok, street, builder, job_title, city, state, zip, note}.
    `ok` is False when a required piece is missing — per Jake/Jordan the caller
    ASKS rather than guessing, and `note` says what's missing.

    `location_hint` is typically Monday Job Location (`location5`) text — a
    recorded fact, so it's allowed to supply city/state/ZIP when the title
    doesn't. Same for `builder_hint` / `customer_hint` from the bid's Customer
    link, and `job_title_hint` / format_job_title inputs for the third part.
    """
    raw = (raw or "").strip()
    hint_loc = parse_location(location_hint or "")
    title_kwargs = dict(
        job_title_hint=job_title_hint, project_type=project_type,
        homeowner_last=homeowner_last, business_name=business_name)

    def _result(*, name: str, ok: bool, street, builder, job_title,
                city, state, zip_code, note: str) -> dict:
        return {
            "name": name, "ok": ok,
            "street": street, "builder": builder, "job_title": job_title,
            "city": city, "state": state, "zip": zip_code, "note": note,
        }

    def _missing_note(missing: list[str]) -> str:
        return ("Missing " + " and ".join(dict.fromkeys(missing))
                + " — add them, don't let it guess.")

    def _collect_missing(loc: dict, builder, job_title) -> list[str]:
        missing: list[str] = []
        if not loc.get("street"):
            missing.append("street/number")
        if any(not loc.get(k) for k in ("city", "state", "zip")):
            missing.append("city/state/ZIP")
        if not builder:
            missing.append("builder/client")
        if not job_title:
            missing.append("job title")
        return missing

    if not raw:
        # Still try to say something useful from hints alone.
        if hint_loc.get("street"):
            left = format_location(
                street=hint_loc.get("street"), city=hint_loc.get("city"),
                state=hint_loc.get("state"), zip_code=hint_loc.get("zip"))
            builder = (builder_hint or customer_hint or "").strip() or None
            job_title = _resolve_job_title(parsed=None, **title_kwargs)
            name = SEPARATOR.join(p for p in (left, builder, job_title) if p)
            missing = _collect_missing(hint_loc, builder, job_title)
            return _result(
                name=name, ok=not missing,
                street=hint_loc.get("street"), builder=builder,
                job_title=job_title,
                city=hint_loc.get("city"), state=hint_loc.get("state"),
                zip_code=hint_loc.get("zip"),
                note=_missing_note(missing) if missing else "",
            )
        job_title = _resolve_job_title(parsed=None, **title_kwargs)
        return _result(
            name="", ok=False, street=None, builder=None, job_title=job_title,
            city=None, state=None, zip_code=None,
            note="No job name to work from.")

    prefix_m = PREFIX_RE.match(raw)
    prefix = ""
    if prefix_m:
        p = prefix_m.group(1).upper()
        prefix = "CO_" if p.startswith("CO") else p

    if is_standard(raw):
        parts = [p.strip() for p in PREFIX_RE.sub("", raw).split("|")]
        loc = parse_location(parts[0])
        return _result(
            name=raw.strip(), ok=True,
            street=loc.get("street"), builder=parts[1], job_title=parts[2],
            city=loc.get("city"), state=loc.get("state"),
            zip_code=loc.get("zip"), note="")

    parts = parse_parts(raw)

    street_part = next((p for p in parts if _looks_like_street(p)), None)
    rest = [p for p in parts if p is not street_part]

    builder = rest[0] if rest else None
    parsed_title = rest[1] if len(rest) > 1 else None
    if not builder:
        builder = (builder_hint or customer_hint or "").strip() or None

    if street_part:
        loc = _merge_location(parse_location(street_part), hint_loc)
    elif parts:
        # No street number — Jake's "Lemon Tree | King" shape. First part is
        # the identifier; still merge location hint for city/state/ZIP.
        loc = _merge_location(parse_location(parts[0]), hint_loc)
        if not loc.get("street"):
            loc["street"] = simplify_street(parts[0]) or parts[0]
        rest2 = parts[1:]
        builder = (rest2[0] if rest2 else
                   (builder_hint or customer_hint or "").strip() or None)
        parsed_title = rest2[1] if len(rest2) > 1 else None
    else:
        loc = dict(hint_loc)

    # If the title had no street but the location hint did, prefer the hint.
    if not loc.get("street") and hint_loc.get("street"):
        loc = _merge_location(hint_loc, loc)

    # 2-part names only supply builder; job title must come from hints/format.
    # 3-part names use parts[2] as job title.
    job_title = _resolve_job_title(parsed=parsed_title, **title_kwargs)

    left = format_location(
        street=loc.get("street"), city=loc.get("city"),
        state=loc.get("state"), zip_code=loc.get("zip"))
    pieces = [p for p in (left, builder, job_title) if p]
    name = prefix + SEPARATOR.join(pieces)

    missing = _collect_missing(loc, builder, job_title)
    if missing:
        return _result(
            name=name, ok=False,
            street=loc.get("street"), builder=builder, job_title=job_title,
            city=loc.get("city"), state=loc.get("state"),
            zip_code=loc.get("zip"), note=_missing_note(missing))
    return _result(
        name=name, ok=True,
        street=loc.get("street"), builder=builder, job_title=job_title,
        city=loc.get("city"), state=loc.get("state"),
        zip_code=loc.get("zip"), note="")


def compose_job_name(location: str, builder: str, *,
                     raw_name: Optional[str] = None,
                     job_title: Optional[str] = None,
                     project_type: Optional[str] = None,
                     homeowner_last: Optional[str] = None,
                     business_name: Optional[str] = None) -> str:
    """
    PURE. Build a standard job title for estimate / CO / Drive write paths.

    Prefer `raw_name` when present (may already carry builder / job title);
    otherwise compose from location + builder + job_title. Always routes
    through `to_standard` so every write path shares one formatter.
    """
    builder = (builder or "").strip()
    location = (location or "").strip()
    raw = (raw_name or "").strip()
    title_kw = dict(
        job_title_hint=job_title, project_type=project_type,
        homeowner_last=homeowner_last, business_name=business_name)
    if raw:
        std = to_standard(raw, builder_hint=builder, location_hint=location,
                          **title_kw)
    elif location and builder:
        std = to_standard(f"{location}{SEPARATOR}{builder}",
                          builder_hint=builder, location_hint=location,
                          **title_kw)
    elif location:
        std = to_standard(location, builder_hint=builder, location_hint=location,
                          **title_kw)
    else:
        return builder
    if std["name"]:
        return std["name"]
    bits = [p for p in (location, builder, (job_title or "").strip() or None) if p]
    return SEPARATOR.join(bits)


# ---------------------------------------------------------------------------
# Matching across the convention change — the duplicate safeguard
# ---------------------------------------------------------------------------

def tokens(name: str) -> set:
    """
    PURE. Name → distinctive lowercase tokens. Street numbers and builder names
    survive; separators, geography, street suffixes and boilerplate do not — so
    a short legacy pipe name and its new city-bearing form yield the SAME token
    set (ZIP / city / state intentionally dropped).
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


# ---------------------------------------------------------------------------
# Jake's job-folder names are a DIFFERENT shape from Monday item names, verified
# against the real "01 - Completed Plans" tree (folder 1X1vuutn…) on 2026-07-29:
#
#     349 - HDG - AutoZone #11053 - Highland Heights, KY
#     331 - Jent - Bryant Res - Sent
#     345 - Ludlow High School Fieldhouse - Build Estimate
#     347 - CMS - Axis Communications Cincinnati - Pricing 7/27
#
# i.e. `{job sequence #} - {GC} - {project} - {status}`. Two consequences:
#   1. The LEADING NUMBER is a job counter (312–351), not a street number, so it
#      must never be treated as the distinctive identifier — and must never earn
#      the shared-number boost.
#   2. Folder names are SHORTER than Monday names (no street, no city, no job
#      type), so Jaccard punishes them for what they legitimately omit. Bid
#      "9761 Gertrude Lane, Cincinnati OH 45231 - Bryant - Jent Construction -
#      New House" vs folder "331 - Jent - Bryant Res - Sent" scores 0.33 on
#      Jaccard and would have been MISSED. Containment is the right measure.
# ---------------------------------------------------------------------------

# Trailing workflow status on a job folder, not part of the job's identity.
_FOLDER_STATUS = {
    "sent", "gt", "build", "estimate", "pricing", "draft", "wip", "pending",
    "lost", "won", "hold", "onhold", "review", "revised", "final", "takeoff",
    "res",          # "Bryant Res" — abbreviation, identifies nothing on its own
}

_LEADING_SEQ_RE = re.compile(r"^\s*\d{1,4}\s*[-–—_.]\s*")


def strip_folder_decoration(name: str) -> str:
    """
    PURE. Job-folder name → just the identifying part. Drops the leading job
    sequence number and any trailing status segment.
    "331 - Jent - Bryant Res - Sent" → "Jent - Bryant Res"
    """
    s = _LEADING_SEQ_RE.sub("", (name or "").strip())
    for _ in range(2):
        parts = re.split(r"\s*[-–—]\s*", s)
        if len(parts) > 1:
            tail = re.sub(r"[^a-z0-9 ]", " ", parts[-1].lower()).split()
            if tail and all(w in _FOLDER_STATUS or w.isdigit() for w in tail):
                s = " - ".join(parts[:-1]).strip()
                continue
        break
    return s.strip(" -–—_")


def folder_tokens(name: str) -> set:
    """
    PURE. Job-folder name → distinctive tokens, decoration removed.

    Also drops the workflow-status vocabulary, which `tokens()` doesn't know
    about: a status word left in ("res", "sent") inflates the denominator and
    weakens an otherwise clean match.
    """
    return {t for t in tokens(strip_folder_decoration(name))
            if t not in _FOLDER_STATUS}


def folder_match_score(job_name: str, folder_name: str) -> float:
    """
    PURE. 0.0–1.0 that this job folder belongs to this job.

    CONTAINMENT (overlap coefficient), not Jaccard: the folder legitimately omits
    the street address and city that the Monday name carries, so it must not be
    penalised for being shorter. Guarded so one weak token can't carry a match:
    a single shared token only counts when it's a distinctive brand/number-like
    token AND it's most of the folder's identity.
    """
    tj, tf = tokens(job_name), folder_tokens(folder_name)
    if not tj or not tf:
        return 0.0
    inter = tj & tf
    if not inter:
        return 0.0
    if len(inter) == 1:
        only = next(iter(inter))
        # A lone generic word is not evidence. A lone distinctive token is, but
        # only if the folder is essentially about that one thing.
        if len(only) < 4 or len(tf) > 2:
            return 0.0
    return len(inter) / min(len(tj), len(tf))


# Folders match on containment, which runs higher than Jaccard, so it needs its
# own (stricter) bar.
FOLDER_MATCH_THRESHOLD = 0.6


def best_folder(job_name: str, folders: list) -> Optional[dict]:
    """
    PURE. Pick the job folder for `job_name`, or None.

    `folders` are dicts with at least {id, name}. Same refusal rule as
    best_match: an ambiguous top-2 returns None, because reading the wrong job's
    scope review would prefill a handoff with another job's scope — worse than
    prefilling nothing.
    """
    if not job_name or not folders:
        return None
    scored = sorted(
        ({**f, "score": folder_match_score(job_name, f.get("name") or "")}
         for f in folders),
        key=lambda f: f["score"], reverse=True)
    if not scored or scored[0]["score"] < FOLDER_MATCH_THRESHOLD:
        return None
    if len(scored) > 1 and scored[1]["score"] >= scored[0]["score"] - 0.05:
        return None
    return scored[0]


def best_match(name: str, candidates: list, *,
               threshold: float = MATCH_THRESHOLD) -> Optional[dict]:
    """
    PURE. Pick the candidate that is the same job as `name`, or None.

    `candidates` are dicts with at least {id, name}. An exact name match always
    wins; otherwise the highest scorer above `threshold` (default
    MATCH_THRESHOLD), and only when it beats the runner-up — an ambiguous
    match is treated as no match, because adopting the wrong item is worse
    than creating a new one. Callers that need a stricter bar (e.g. Ops↔
    Projects link backfill at 0.85) pass threshold explicitly.
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
    if not scored or scored[0]["score"] < threshold:
        return None
    if len(scored) > 1 and scored[1]["score"] >= scored[0]["score"] - 0.05:
        return None                      # ambiguous ⇒ don't adopt
    return {**scored[0], "how": "tokens"}
