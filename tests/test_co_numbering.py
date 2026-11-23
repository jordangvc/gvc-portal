"""CO numbering: core-normalized base matching + Drive filename extraction.

Regression cover for two numbering bugs:
  - next_co_number compared bases verbatim, while the rest of the portal
    core-normalizes spine numbers — a CO stored under the bare core was
    invisible when the caller's base arrived EST-/PRO-/INV-prefixed (and
    vice versa), splitting one job's COs into separate sequences.
  - _gather_existing_identifiers appended Drive file stems verbatim, but
    real Drive names are `CO.1-{base} - {job name}.pdf`, whose stem never
    parses as a CO id — Drive-only COs were never counted, so the next
    finalize reused their number (and silently updated the wrong item).

Run: python tests/test_co_numbering.py
  or: .venv/bin/pytest tests/test_co_numbering.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subsystems.change_order.number import (  # noqa: E402
    extract_co_identifier,
    next_co_number,
)


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_core_normalized_base_matching() -> None:
    # Existing COs stored under prefixed bases still count for the bare core.
    existing = ["CO.1-EST-2026-0611-001", "CO.2-PRO-2026-0611-001"]
    check("prefixed existing COs count toward the bare-core base",
          next_co_number("2026-0611-001", existing) == "CO.3-2026-0611-001")
    # ...and the reverse: a bare-core CO counts for a prefixed caller base.
    check("bare-core existing CO counts toward a prefixed base",
          next_co_number("EST-2026-0611-001", ["CO.1-2026-0611-001"])
          == "CO.2-EST-2026-0611-001")
    # Other jobs' COs (different core) never count.
    check("another job's COs are ignored",
          next_co_number("2026-0611-001", ["CO.7-2026-0611-002"])
          == "CO.1-2026-0611-001")
    # Legacy non-spine bases keep verbatim matching.
    check("legacy base still matches verbatim",
          next_co_number("GVC-2026-MV-005", ["CO.1-GVC-2026-MV-005"])
          == "CO.2-GVC-2026-MV-005")
    check("legacy base does not absorb another job's COs",
          next_co_number("GVC-2026-MV-005", ["CO.3-GVC-2026-MV-006"])
          == "CO.1-GVC-2026-MV-005")


def test_extract_co_identifier_from_drive_names() -> None:
    check("standard Drive name",
          extract_co_identifier("CO.1-2026-0611-001 - Smith Kitchen.pdf")
          == "CO.1-2026-0611-001")
    check("Drive name without a job suffix",
          extract_co_identifier("CO.3-2026-0611-001.pdf") == "CO.3-2026-0611-001")
    check("Rev suffix on the identifier is stripped",
          extract_co_identifier("CO.2-2026-0611-001 Rev 2 - Smith Kitchen.pdf")
          == "CO.2-2026-0611-001")
    check("bare identifier passes through",
          extract_co_identifier("CO.4-2026-0611-001") == "CO.4-2026-0611-001")
    check("non-CO file yields None",
          extract_co_identifier("Smith Kitchen estimate.pdf") is None)
    check("empty string yields None", extract_co_identifier("") is None)


def test_drive_only_co_holds_the_max() -> None:
    # The Geiger failure shape: every CO for the job exists ONLY as a Drive
    # PDF. The next number must continue from the Drive max, not restart.
    drive_names = [
        "CO.1-2026-0611-001 - Geiger Residence.pdf",
        "CO.2-2026-0611-001 - Geiger Residence.pdf",
        "CO.3-2026-0611-001 - Geiger Residence.pdf",
        "site photos.pdf",
    ]
    identifiers = [x for x in (extract_co_identifier(n) for n in drive_names) if x]
    check("all Drive COs extracted", len(identifiers) == 3)
    check("next number continues from the Drive-only max",
          next_co_number("2026-0611-001", identifiers) == "CO.4-2026-0611-001")


if __name__ == "__main__":
    test_core_normalized_base_matching()
    test_extract_co_identifier_from_drive_names()
    test_drive_only_co_holds_the_max()
    print("test_co_numbering: all checks passed")
