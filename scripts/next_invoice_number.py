"""
Pick the next free invoice identifier in a series.

Identifiers follow `GVC-{year}-{series}-{NNN}` — series is `C` (commercial) or
`MV` (residential). This helper scans `inputs/` and `output/` for the highest
existing number in the chosen series for the given year, and prints the next
one so you don't have to eyeball the directory listing or risk a collision.

Usage (run from the project root):

    ./gvc scripts/next_invoice_number.py --series C
    ./gvc scripts/next_invoice_number.py --series MV --year 2026

The result is written to stdout as the bare identifier (e.g. `GVC-2026-C-004`)
so you can pipe it into other tools. If no prior invoices in the series exist
for the year, returns `GVC-{year}-{series}-001`.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUTS_DIR = ROOT / "inputs"
OUTPUT_DIR = ROOT / "output"


def collect_identifiers(year: int, series: str, *, inputs: Path, outputs: Path) -> set[str]:
    """
    Return every identifier in the given (year, series) found in inputs/ and
    output/. Filenames are expected to match `GVC-{year}-{series}-{NNN}` —
    extra slug suffixes after the digits are allowed.
    """
    pattern = re.compile(rf"^(GVC-{year}-{re.escape(series)}-\d{{3}})\b")
    found: set[str] = set()
    for d in (inputs, outputs):
        if not d.exists():
            continue
        for p in d.iterdir():
            m = pattern.match(p.stem)
            if m:
                found.add(m.group(1))
    return found


def next_identifier(year: int, series: str, *, inputs: Path = INPUTS_DIR,
                    outputs: Path = OUTPUT_DIR) -> str:
    """Return the next free identifier in the given (year, series)."""
    existing = collect_identifiers(year, series, inputs=inputs, outputs=outputs)
    if not existing:
        n = 1
    else:
        numbers = [int(ident.rsplit("-", 1)[-1]) for ident in existing]
        n = max(numbers) + 1
    return f"GVC-{year}-{series}-{n:03d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--series", required=True, choices=["C", "MV"],
                    help="C = commercial, MV = residential")
    ap.add_argument("--year", type=int, default=date.today().year,
                    help="Year to look up (default: current year)")
    args = ap.parse_args()
    print(next_identifier(args.year, args.series))
    return 0


if __name__ == "__main__":
    sys.exit(main())
