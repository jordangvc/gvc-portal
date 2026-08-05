"""
Forward-geocode a street into city/state/ZIP for the tri-state area.
=========================================================================
Used by the job-rename backfill when Monday location fields are empty.
Nominatim / OpenStreetMap — no API key. Be polite: 1 request/sec and a
real User-Agent. Prefer recorded Monday facts over this.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from subsystems.jobstart import naming

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "GVC-Portal-JobRename/1.0 (hello@greenvalleycontractors.com)"
TRI_STATES = (
    ("OH", "Ohio"),
    ("IN", "Indiana"),
    ("KY", "Kentucky"),
)

_last_call_monotonic = 0.0
MIN_INTERVAL_SEC = 1.05

_SUFFIX_RE = re.compile(
    r"\b(st|street|rd|road|ave|avenue|dr|drive|ln|lane|ct|court|way|blvd|"
    r"cir|circle|pl|place|trl|trail|pike|hwy|highway)\b",
    re.I,
)


def _throttle() -> None:
    global _last_call_monotonic
    now = time.monotonic()
    wait = MIN_INTERVAL_SEC - (now - _last_call_monotonic)
    if wait > 0:
        time.sleep(wait)
    _last_call_monotonic = time.monotonic()


def _nominatim_search(params: dict) -> list[dict]:
    _throttle()
    query = urlencode({**params, "format": "json", "addressdetails": 1, "limit": 3})
    req = Request(
        f"{NOMINATIM_URL}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(req, timeout=20) as resp:  # noqa: S310 — fixed HTTPS endpoint
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def _extract_zip(raw: Optional[str]) -> Optional[str]:
    m = re.search(r"\b(\d{5})\b", raw or "")
    return m.group(1) if m else None


def _has_suffix(street: str) -> bool:
    return bool(_SUFFIX_RE.search(street or ""))


def _hit_to_location(hit: dict, *, street_fallback: str) -> Optional[dict]:
    addr = hit.get("address") or {}
    if not isinstance(addr, dict):
        return None
    city = (
        addr.get("city") or addr.get("town") or addr.get("village")
        or addr.get("hamlet") or addr.get("municipality")
    )
    state_abbr = naming._normalize_state(addr.get("state")) if addr.get("state") else None
    zip_code = _extract_zip(str(addr.get("postcode") or ""))
    if not (city and state_abbr and zip_code):
        return None
    road = " ".join(
        p for p in (addr.get("house_number"), addr.get("road")) if p
    ).strip()
    street = road or street_fallback
    if not street:
        return None
    return {
        "street": street,
        "city": city,
        "state": state_abbr,
        "zip": zip_code,
        "hint": naming.format_location(
            street=street, city=city, state=state_abbr, zip_code=zip_code),
        "source": "nominatim",
        "display_name": hit.get("display_name"),
    }


def lookup_tri_state_street(
    street: str,
    *,
    search_fn: Callable[[dict], list[dict]] = _nominatim_search,
) -> Optional[dict]:
    """
    Look up `street` in OH, IN, and KY. Return a location dict only when
    exactly one state yields a usable city/state/ZIP hit.

    Ambiguous (hits in multiple states) → None (still don't guess which state).
    """
    street = (street or "").strip()
    if not street or not naming._looks_like_street(street):
        return None

    candidates = [street]
    if not _has_suffix(street):
        for suf in ("Drive", "Lane", "Road", "Court", "Way", "Street"):
            candidates.append(f"{street} {suf}")

    found: list[dict] = []
    seen_states: set[str] = set()
    for state_abbr, state_name in TRI_STATES:
        state_hit = None
        for cand in candidates:
            hits = search_fn({
                "street": cand,
                "state": state_name,
                "country": "USA",
            })
            for hit in hits:
                loc = _hit_to_location(hit, street_fallback=cand)
                if not loc or loc["state"] != state_abbr:
                    continue
                state_hit = loc
                break
            if state_hit:
                break
        if state_hit and state_abbr not in seen_states:
            found.append(state_hit)
            seen_states.add(state_abbr)

    if len(found) == 1:
        return found[0]
    return None


def reverse_lat_lng(lat, lng) -> Optional[dict]:
    """Reverse-geocode a Monday location pin when address text is empty."""
    if lat in (None, "") or lng in (None, ""):
        return None
    _throttle()
    query = urlencode({
        "lat": lat, "lon": lng, "format": "json", "addressdetails": 1,
    })
    req = Request(
        f"{NOMINATIM_REVERSE_URL}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(req, timeout=20) as resp:  # noqa: S310
        hit = json.loads(resp.read().decode("utf-8"))
    if not isinstance(hit, dict):
        return None
    return _hit_to_location(hit, street_fallback="")
