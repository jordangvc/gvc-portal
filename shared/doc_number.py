"""
Unified document spine — one core number, typed prefixes.
=========================================================================
Jordan 2026-08-04: the estimate number carries through project + invoice.
Only the letters in front change:

  EST-YYYY-MMDD-NNN   estimate (PDF / portal / Gmail)
  PRO-YYYY-MMDD-NNN   project  (Projects board Project #)
  INV-YYYY-MMDD-NNN   invoice  (Document # / Stripe metadata)

The core `YYYY-MMDD-NNN` is assigned once at estimate finalize (same daily
counter as before). Monday's Bid Board `Estimate #` column is a numbers
column — it stores the bare core so the API write stays valid. Text
columns (Project #, Document #) and every customer-facing doc use the
prefixed form.

Legacy values without a prefix (bare core, older `C-005` / `GVC-…` ids)
pass through unchanged when they aren't a portal core number.
"""
from __future__ import annotations

import re
from typing import Optional

PREFIX_EST = "EST"
PREFIX_PRO = "PRO"
PREFIX_INV = "INV"

CORE_RE = re.compile(r"^(\d{4})-(\d{4})-(\d{3})$")
PREFIXED_RE = re.compile(
    r"^(EST|PRO|INV)-(\d{4}-\d{4}-\d{3})$",
    re.IGNORECASE,
)


_REV_SUFFIX_RE = re.compile(r"^(.*?)(\s+Rev\s+\d+)\s*$", re.IGNORECASE)


def split_revision_suffix(value: Optional[str]) -> tuple[str, str]:
    """Split `INV-… Rev 2` → (`INV-…`, ` Rev 2`)."""
    raw = (value or "").strip()
    m = _REV_SUFFIX_RE.match(raw)
    if m:
        return m.group(1).strip(), m.group(2)
    return raw, ""


def core_number(value: Optional[str]) -> Optional[str]:
    """Bare YYYY-MMDD-NNN, or None if `value` isn't a portal spine number."""
    v, _rev = split_revision_suffix(value)
    if not v:
        return None
    m = PREFIXED_RE.match(v)
    if m:
        return m.group(2)
    if CORE_RE.match(v):
        return v
    return None


def is_spine_number(value: Optional[str]) -> bool:
    """True for bare core or EST-/PRO-/INV- prefixed forms (Rev suffix ok)."""
    return core_number(value) is not None


def with_prefix(kind: str, value: Optional[str]) -> str:
    """
    Return `{KIND}-{core}` (+ preserved ` Rev N` when present). Legacy /
    unrecognized values are returned stripped (no invented prefix).
    """
    kind = (kind or "").strip().upper()
    base, rev = split_revision_suffix(value)
    core = core_number(base)
    if not core or kind not in {PREFIX_EST, PREFIX_PRO, PREFIX_INV}:
        return (value or "").strip()
    return f"{kind}-{core}{rev}"


def for_estimate(value: Optional[str]) -> str:
    return with_prefix(PREFIX_EST, value)


def for_project(value: Optional[str]) -> str:
    return with_prefix(PREFIX_PRO, value)


def for_invoice(value: Optional[str]) -> str:
    return with_prefix(PREFIX_INV, value)


def monday_estimate_cell(value: Optional[str]) -> str:
    """Value to write into Bid Board Estimate # (numbers column) — bare core."""
    return core_number(value) or (value or "").strip()


def search_needles(value: Optional[str]) -> list[str]:
    """
    Query variants for Monday contains_text search on spine numbers.

    Monday stores Bid Board Estimate # as bare core and Projects Project # as
    PRO-{core}. Pasting EST-/PRO-/INV-… must still hit those cells, so we
    return: typed string first (name search / caller q), then PRO-{core}
    when spine (Projects column), then bare core. Deduped, empty dropped.
    """
    raw = (value or "").strip()
    if not raw:
        return []
    out: list[str] = []
    core = core_number(raw)
    candidates = [raw]
    if core:
        candidates.append(for_project(raw))
        candidates.append(core)
    for candidate in candidates:
        if candidate and candidate not in out:
            out.append(candidate)
    return out
