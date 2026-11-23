"""
Change Order numbering — `CO.{n}-{base_number}`.
=========================================================================
Confirmed scheme (decided 2026-06-15): a CO always follows a job already
underway, so it REUSES that job's number (the `base_number`, taken from the
linked estimate/project) and prefixes `CO.{n}-`, where `n` increments per
job (CO.1-, CO.2-, …). Revisions keep the number + " Rev N" (same rule as
estimates).

This module is intentionally **pure and format-agnostic about the base** —
the base may be an estimate identifier (`2026-0611-001`), an invoice-style
project number (`GVC-2026-MV-005`), or whatever the linked job carries. The
caller fetches the existing COs for the job (the parent $Project item's
subitem names on the Projects board) and passes their identifiers in; this
module just computes the next number. No Monday/Drive I/O here.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from shared.doc_number import core_number

# CO.{n}-{base}. Base captured greedily; a trailing " Rev N" is stripped first.
_CO_RE = re.compile(r"^CO\.(\d+)-(.+)$")
_REV_SUFFIX_RE = re.compile(r"\s+Rev\s+\d+$", re.I)


def format_co_number(n: int, base_number: str) -> str:
    base_number = (base_number or "").strip()
    if not base_number:
        raise ValueError("base_number is required to format a CO number.")
    if n < 1:
        raise ValueError("CO counter n must be >= 1.")
    return f"CO.{n}-{base_number}"


def parse_co_number(value: str) -> Optional[tuple[int, str]]:
    """Return (n, base_number) for a well-formed CO id, else None.
    Tolerates a trailing ' Rev N' revision suffix."""
    value = _REV_SUFFIX_RE.sub("", (value or "").strip())
    m = _CO_RE.match(value)
    if not m:
        return None
    return int(m.group(1)), m.group(2).strip()


def _base_key(base: str) -> str:
    """Comparison key for a CO base: the spine core when the base is a
    portal spine number — so the bare / EST- / PRO- / INV- forms of the
    same job compare equal — else the stripped base verbatim (legacy
    bases like `GVC-2026-MV-005` keep exact-match semantics)."""
    base = (base or "").strip()
    return core_number(base) or base


def extract_co_identifier(text: str) -> Optional[str]:
    """
    Pull a `CO.{n}-{base}` identifier out of a free-form string — e.g. a
    Drive file name like `CO.1-2026-0611-001 - Smith Kitchen.pdf`, where
    everything after " - " is the job name, not part of the identifier.
    A trailing " Rev N" is stripped (parse_co_number tolerates it).
    Returns the normalized identifier, or None when the string doesn't
    start with a CO identifier.
    """
    stem = (text or "").strip()
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    candidate = stem.split(" - ")[0].strip()
    parsed = parse_co_number(candidate)
    if not parsed:
        return None
    return format_co_number(parsed[0], parsed[1])


def next_co_number(base_number: str, existing_identifiers: Iterable[str]) -> str:
    """
    Next `CO.{n}-{base_number}` for a job, given the identifiers of COs that
    already exist for it. `n` = max existing n for THIS base + 1 (1 if none).
    Identifiers for other jobs and malformed values are ignored. Bases are
    compared core-normalized (see _base_key): an existing CO stored under
    the bare core still counts when the caller's base arrives EST-/PRO-/
    INV-prefixed, and vice versa — the old verbatim comparison split one
    job's COs into separate sequences.
    """
    base_number = (base_number or "").strip()
    if not base_number:
        raise ValueError("base_number is required (the linked job's number).")
    base_key = _base_key(base_number)
    counters = []
    for ident in existing_identifiers or []:
        parsed = parse_co_number(ident)
        if parsed and _base_key(parsed[1]) == base_key:
            counters.append(parsed[0])
    n = (max(counters) + 1) if counters else 1
    return format_co_number(n, base_number)
