"""
Estimate numbering — service-assigned `YYYY-MMDD-NNN`.
=========================================================================
Confirmed scheme (decided 2026-06-10): date of generation + a daily
incremental counter, zero-padded to 3. No prefixes. The date component is
the *generation* date and never changes on revision.

Sources scanned for today's max NNN (the number is max+1 across BOTH):
  1. The Monday Bid Board, `Estimate #` column (the audit trail) —
     skipped gracefully when Monday isn't configured.
  2. The local/container output dir (catches estimates finalized while a
     Monday write-back failed).

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

ESTIMATE_NUMBER_RE = re.compile(r"^(\d{4})-(\d{4})-(\d{3})$")

# Bid Board + its `Estimate #` text column (see
# GVC_Estimate_System_Confirmed_Design.md for the full column map).
from shared.boards import BID_BOARD_ID
COL_ESTIMATE_NUMBER = "numbers18"


def day_prefix(d: Optional[date] = None) -> str:
    d = d or date.today()
    return f"{d.year}-{d.month:02d}{d.day:02d}"


def format_number(d: date, nnn: int) -> str:
    return f"{day_prefix(d)}-{nnn:03d}"


def parse_counter(value: str, *, prefix: str) -> Optional[int]:
    """Return NNN if `value` is a well-formed number for the given day prefix."""
    value = (value or "").strip()
    m = ESTIMATE_NUMBER_RE.match(value)
    if not m:
        return None
    if not value.startswith(prefix + "-"):
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
    """Return the next `YYYY-MMDD-NNN` for today (001 if none exist yet)."""
    d = today or date.today()
    prefix = day_prefix(d)
    counters = list(_counters_from_monday(prefix))
    counters += list(_counters_from_output_dir(Path(output_dir), prefix=prefix))
    nnn = (max(counters) + 1) if counters else 1
    return format_number(d, nnn)
