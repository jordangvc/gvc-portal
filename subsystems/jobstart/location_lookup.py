"""
Look up city / state / ZIP for job-title enrichment.
=========================================================================
Jordan 2026-08-05: do NOT skip incomplete rename rows — look them up.

Sources (recorded facts first):
  1. Monday location column text
  2. Monday location column raw JSON (`address`, sometimes city/street)
  3. Typed LocationValue fields when the GraphQL fragment returns them
  4. Extra free-text hints (linked Bid location, Drive folder name, …)

Geocoding (Nominatim) lives in adapters/geocode.py and is called by the
backfill scripts when these recorded sources still leave gaps.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from subsystems.jobstart import naming


def parse_monday_location_json(raw_value: Optional[str]) -> dict:
    """
    PURE. Monday location5 `value` JSON → {street, city, state, zip, address, lat, lng}.

    Typical shape:
      {"lat":"39.1","lng":"-84.5","address":"9195 Silva Dr, Cincinnati, OH 45241"}
    """
    out = {
        "street": None, "city": None, "state": None, "zip": None,
        "address": None, "lat": None, "lng": None,
    }
    if not raw_value:
        return out
    try:
        parsed = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        return out
    if not isinstance(parsed, dict):
        return out

    address = (parsed.get("address") or parsed.get("formatted") or "").strip()
    out["address"] = address or None
    for key in ("lat", "lng"):
        if parsed.get(key) not in (None, ""):
            try:
                out[key] = float(parsed[key])
            except (TypeError, ValueError):
                out[key] = str(parsed[key])

    # Some Monday payloads nest a richer address object.
    nested = parsed.get("address") if isinstance(parsed.get("address"), dict) else None
    blob = address
    if nested:
        blob = ", ".join(
            str(nested.get(k) or "").strip()
            for k in ("street", "city", "state", "zip", "postal_code", "country")
            if nested.get(k)
        ) or blob

    if blob and not isinstance(blob, dict):
        loc = naming.parse_location(str(blob))
        for key in ("street", "city", "state", "zip"):
            out[key] = loc.get(key)
        out["address"] = str(blob).strip() or out["address"]
    return out


def location_from_column_value(cv: Optional[dict]) -> dict:
    """
    PURE. One Monday column_values entry → merged location pieces + best address text.

    Accepts plain {id,text,value} and optional LocationValue fields
    (city, street, street_number, address) when the query asked for them.
    """
    if not cv:
        return {"street": None, "city": None, "state": None, "zip": None,
                "address": None, "lat": None, "lng": None, "hint": None}

    text = (cv.get("text") or cv.get("display_value") or "").strip() or None
    from_json = parse_monday_location_json(cv.get("value"))

    # Typed LocationValue fields (may be null even when text/value are rich).
    street_number = (cv.get("street_number") or "").strip()
    street_name = (cv.get("street") or "").strip()
    typed_city = (cv.get("city") or "").strip() or None
    typed_street = None
    if street_number or street_name:
        typed_street = " ".join(p for p in (street_number, street_name) if p)
    typed_address = (cv.get("address") or "").strip() or None
    if isinstance(cv.get("address"), dict):
        typed_address = None

    pieces = {
        "street": typed_street or from_json.get("street"),
        "city": typed_city or from_json.get("city"),
        "state": from_json.get("state"),
        "zip": from_json.get("zip"),
        "address": typed_address or from_json.get("address") or text,
        "lat": from_json.get("lat") or cv.get("lat"),
        "lng": from_json.get("lng") or cv.get("lng"),
    }

    # Prefer a complete parse of the best address blob.
    for blob in (pieces["address"], text):
        if not blob:
            continue
        loc = naming.parse_location(blob)
        for key in ("street", "city", "state", "zip"):
            if not pieces.get(key) and loc.get(key):
                pieces[key] = loc[key]

    hint = naming.format_location(
        street=pieces.get("street"), city=pieces.get("city"),
        state=pieces.get("state"), zip_code=pieces.get("zip"),
    )
    if not hint and pieces.get("address"):
        hint = str(pieces["address"]).strip()
    if not hint and text:
        hint = text
    pieces["hint"] = hint or None
    return pieces


def merge_location_hints(*hints: Optional[str]) -> Optional[str]:
    """
    PURE. Combine multiple free-text address blobs into one canonical hint.

    Fills missing city/state/ZIP from later hints; prefers the fullest street.
    """
    merged = {"street": None, "city": None, "state": None, "zip": None}
    for hint in hints:
        if not (hint or "").strip():
            continue
        part = naming.parse_location(hint)
        merged = naming._merge_location(merged, part)
    if not any(merged.values()):
        return None
    return naming.format_location(
        street=merged.get("street"), city=merged.get("city"),
        state=merged.get("state"), zip_code=merged.get("zip"),
    ) or None


def street_from_job_name(name: str) -> Optional[str]:
    """PURE. Left side of a pipe/dash title → street-ish string for geocoding."""
    body = naming.PREFIX_RE.sub("", name or "").strip()
    if not body:
        return None
    left = re.split(r"\s*[|—–]\s*|\s+-\s+", body, maxsplit=1)[0].strip()
    loc = naming.parse_location(left)
    return loc.get("street") or (left if naming._looks_like_street(left) else None)


def enrich_location(
    *,
    name: str,
    location_text: Optional[str] = None,
    location_value_json: Optional[str] = None,
    location_column: Optional[dict] = None,
    extra_hints: Optional[list[Optional[str]]] = None,
) -> dict:
    """
    PURE. Gather every recorded location clue for one item.

    Returns {hint, street, city, state, zip, lat, lng, sources: [str]}.
    Does not call the network — geocode is a separate step.
    """
    sources: list[str] = []
    col = location_from_column_value(location_column) if location_column else None
    if not col and (location_text or location_value_json):
        col = location_from_column_value({
            "text": location_text or "",
            "value": location_value_json or "",
        })
    col = col or {}

    hints: list[Optional[str]] = []
    if col.get("hint"):
        hints.append(col["hint"])
        sources.append("monday_location")
    if location_text and location_text not in hints:
        hints.append(location_text)
        if "monday_location" not in sources:
            sources.append("monday_location_text")
    for h in extra_hints or []:
        if h and h not in hints:
            hints.append(h)
            sources.append("linked_or_drive")

    # Title itself may already carry city/ZIP on the left of the pipe.
    title_left = street_from_job_name(name)
    if name and "|" in name:
        hints.insert(0, name.split("|", 1)[0].strip())

    merged_hint = merge_location_hints(*hints)
    loc = naming.parse_location(merged_hint or "")
    if not loc.get("street") and title_left:
        loc["street"] = title_left

    hint = naming.format_location(
        street=loc.get("street"), city=loc.get("city"),
        state=loc.get("state"), zip_code=loc.get("zip"),
    ) or merged_hint

    return {
        "hint": hint,
        "street": loc.get("street") or col.get("street") or title_left,
        "city": loc.get("city") or col.get("city"),
        "state": loc.get("state") or col.get("state"),
        "zip": loc.get("zip") or col.get("zip"),
        "lat": col.get("lat"),
        "lng": col.get("lng"),
        "sources": sources,
        "complete": naming.location_complete({
            "street": loc.get("street") or col.get("street") or title_left,
            "city": loc.get("city") or col.get("city"),
            "state": loc.get("state") or col.get("state"),
            "zip": loc.get("zip") or col.get("zip"),
        }),
    }


def co_parent_from_name(name: str) -> Optional[str]:
    """PURE. `CO.12 - Parent Title` → `Parent Title`."""
    m = re.match(r"^\s*CO\.\d+\s*[-–—]\s*(.+)$", name or "", re.I)
    return m.group(1).strip() if m else None


def co_number_from_name(name: str) -> Optional[str]:
    """PURE. `CO.12 - …` → `CO.12` (for rebuild)."""
    m = re.match(r"^\s*(CO\.\d+)\b", name or "", re.I)
    if not m:
        return None
    # Normalize to CO.<digits>
    digits = re.search(r"\d+", m.group(1))
    return f"CO.{digits.group(0)}" if digits else m.group(1)


def build_co_title(parent_name: str, co_name: str) -> str:
    """PURE. Rebuild `CO.n - {parent}` from an existing CO title + new parent."""
    prefix = co_number_from_name(co_name) or "CO"
    parent_name = (parent_name or "").strip()
    return f"{prefix} - {parent_name}" if parent_name else co_name
