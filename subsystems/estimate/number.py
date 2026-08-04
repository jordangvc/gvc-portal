"""
Estimate numbering — service-assigned `EST-YYYY-MMDD-NNN`.
=========================================================================
Core scheme (decided 2026-06-10, prefix locked 2026-08-04): generation date
+ daily incremental counter, zero-padded to 3. The outbound document id is
`EST-{core}`; the same core becomes `PRO-{core}` on the project and
`INV-{core}` on the invoice (see shared/doc_number.py).

Sources scanned for today's max NNN (the number is max+1 across BOTH):
  1. The Monday Bid Board, `Estimate #` column (stores bare core) —
     skipped gracefully when Monday isn't configured.
  2. The local/container output dir (catches estimates finalized while a
     Monday write-back failed; filenames may be EST-… or bare).

Concurrency note: at GVC's volume (a handful of estimates/day, 3-7 portal
users) a read-then-assign race is theoretical; if two finalizes ever do
collide, the Monday `Estimate #` audit makes it visible and the loser is
re-issued. Revisit with a real counter store only if volume changes.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from shared.doc_number import (
    CORE_RE,
    PREFIX_EST,
    core_number,
    for_estimate,
    monday_estimate_cell,
)

# Accept bare core OR EST-/PRO-/INV- prefixed (normalize to EST- at assign).
ESTIMATE_NUMBER_RE = re.compile(
    r"^(?:EST-|PRO-|INV-)?(\d{4})-(\d{4})-(\d{3})$",
    re.IGNORECASE,
)

# Bid Board + its `Estimate #` column (see
# GVC_Estimate_System_Confirmed_Design.md for the full column map).
from shared.boards import BID_BOARD_ID
COL_ESTIMATE_NUMBER = "numbers18"


def day_prefix(d: Optional[date] = None) -> str:
    d = d or date.today()
    return f"{d.year}-{d.month:02d}{d.day:02d}"


def format_number(d: date, nnn: int) -> str:
    """Outbound estimate id: EST-YYYY-MMDD-NNN."""
    return for_estimate(f"{day_prefix(d)}-{nnn:03d}")


def parse_counter(value: str, *, prefix: str) -> Optional[int]:
    """Return NNN if `value` is a well-formed spine number for the day prefix."""
    core = core_number(value)
    if not core:
        return None
    if not core.startswith(prefix + "-"):
        return None
    m = CORE_RE.match(core)
    if not m:
        return None
    return int(m.group(3))


def _counters_from_output_dir(output_dir: Path, *, prefix: str) -> Iterable[int]:
    if not output_dir or not Path(output_dir).exists():
        return []
    found = []
    for p in Path(output_dir).iterdir():
        # Filenames are `<identifier>.pdf`, possibly with ` Rev N` suffixes.
        stem = p.stem.split(" ")[0]
        n = parse_counter(stem, prefix=prefix)
        if n is not None:
            found.append(n)
    return found


def _counters_from_monday(prefix: str) -> Iterable[int]:
    """
    Query the Bid Board for today's numbers. Returns [] (with a
    stderr note) on ANY failure — numbering must never block an estimate.
    """
    try:
        from adapters.monday.client import MondayClient, MondayNotConfigured
        try:
            mc = MondayClient()
        except MondayNotConfigured:
            return []
        query = """
        query ($boardId: [ID!], $columnId: String!, $value: CompareValue!) {
          boards(ids: $boardId) {
            items_page(
              limit: 100,
              query_params: {rules: [{column_id: $columnId,
                                      compare_value: $value,
                                      operator: contains_text}]}
            ) {
              items { column_values(ids: [$columnId]) { id text } }
            }
          }
        }
        """
        data = mc._query(query, {
            "boardId": [str(BID_BOARD_ID)],
            "columnId": COL_ESTIMATE_NUMBER,
            "value": prefix,
        })
        counters = []
        for board in data.get("boards") or []:
            for item in (board.get("items_page") or {}).get("items") or []:
                for cv in item.get("column_values") or []:
                    n = parse_counter(cv.get("text") or "", prefix=prefix)
                    if n is not None:
                        counters.append(n)
        return counters
    except Exception as e:  # noqa: BLE001
        print(f"[estimate-number] Monday scan skipped: {type(e).__name__}: {e}",
              file=sys.stderr)
        return []


def next_estimate_number(*, output_dir: Path, today: Optional[date] = None) -> str:
    """Return the next `EST-YYYY-MMDD-NNN` for today (EST-…-001 if none yet)."""
    d = today or date.today()
    prefix = day_prefix(d)
    counters = list(_counters_from_monday(prefix))
    counters += list(_counters_from_output_dir(Path(output_dir), prefix=prefix))
    nnn = (max(counters) + 1) if counters else 1
    return format_number(d, nnn)


def normalize_estimate_identifier(value: str) -> str:
    """Canonical outbound form EST-{core}; empty string stays empty."""
    v = (value or "").strip()
    if not v:
        return ""
    return for_estimate(v) if core_number(v) else v


# Re-export for callers that write Monday Estimate #.
__all__ = [
    "ESTIMATE_NUMBER_RE",
    "COL_ESTIMATE_NUMBER",
    "day_prefix",
    "format_number",
    "parse_counter",
    "next_estimate_number",
    "normalize_estimate_identifier",
    "monday_estimate_cell",
    "PREFIX_EST",
]
