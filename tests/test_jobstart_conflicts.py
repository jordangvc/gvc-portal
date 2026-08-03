"""
Job Start — no silent Monday overwrite; already-processed safeguard.
=========================================================================
Self-running:  python tests/test_jobstart_conflicts.py

Holds the rules that make accept() safe to re-run tomorrow morning:
  1. On adopt, a filled Monday cell that disagrees with the packet is KEPT
     (flagged as a conflict) — never silently overwritten.
  2. Empty Monday cells still get filled from the packet.
  3. Matching values are a no-op.
  4. Relation/link columns always write (connecting boards IS the handoff).
  5. Number / date compare tolerates "340" vs "340.0" and ISO date prefixes.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "weasyprint" not in sys.modules:
    _stub = types.ModuleType("weasyprint")
    _stub.HTML = object
    sys.modules["weasyprint"] = _stub

from adapters.monday import jobstart as mj  # noqa: E402
from shared import boards  # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} {extra}".strip())


# ---------------------------------------------------------------------------
# 1. Empty Monday → write; match → skip; differ → conflict
# ---------------------------------------------------------------------------

proposed = {
    "text_builder": {"text": "Jent Construction"},
    "status_type": {"label": "Commercial"},
    "date_start": {"date": "2026-08-15"},
    "num_boards": {"number": "340"},
    boards.JOBSTART_OPS_COL_LINK_PROJECTS: {"item_ids": [111]},
}

existing_empty = {}
safe, conflicts = mj.filter_conflicting_writes(proposed, existing_empty)
check("empty Monday takes every packet write",
      set(safe) == set(proposed) and conflicts == [])

existing_match = {
    "text_builder": "Jent Construction",
    "status_type": "Commercial",
    "date_start": "2026-08-15",
    "num_boards": "340",
}
safe, conflicts = mj.filter_conflicting_writes(proposed, existing_match)
check("matching values are skipped (no-op)",
      "text_builder" not in safe and "status_type" not in safe
      and "date_start" not in safe and "num_boards" not in safe)
check("matching produces no conflicts", conflicts == [])
check("link columns still write on a match pass",
      boards.JOBSTART_OPS_COL_LINK_PROJECTS in safe)

existing_conflict = {
    "text_builder": "Danis",
    "status_type": "Residential",
    "date_start": "2026-09-01",
    "num_boards": "200",
}
safe, conflicts = mj.filter_conflicting_writes(proposed, existing_conflict)
check("disagreeing cells are NOT written",
      "text_builder" not in safe and "status_type" not in safe
      and "date_start" not in safe and "num_boards" not in safe)
check("each disagreeing cell is reported",
      len(conflicts) == 4, f"got {conflicts!r}")
check("conflict carries existing + proposed",
      any(c["column_id"] == "text_builder"
          and c["existing"] == "Danis"
          and c["proposed"] == "Jent Construction" for c in conflicts))
check("link columns always write even when other cells conflict",
      boards.JOBSTART_OPS_COL_LINK_PROJECTS in safe)


# ---------------------------------------------------------------------------
# 2. Tolerant compare
# ---------------------------------------------------------------------------

safe, conflicts = mj.filter_conflicting_writes(
    {"n": {"number": "340"}}, {"n": "340.0"})
check("340 vs 340.0 is a match, not a conflict",
      "n" not in safe and conflicts == [])

safe, conflicts = mj.filter_conflicting_writes(
    {"d": {"date": "2026-08-15"}}, {"d": "2026-08-15T00:00:00Z"})
check("ISO date prefix matches YYYY-MM-DD",
      "d" not in safe and conflicts == [])

safe, conflicts = mj.filter_conflicting_writes(
    {"s": {"label": "Commercial"}}, {"s": "commercial"})
check("status compare is case-insensitive",
      "s" not in safe and conflicts == [])


# ---------------------------------------------------------------------------
# 3. Mixed: fill blanks, keep conflicts, write links
# ---------------------------------------------------------------------------

safe, conflicts = mj.filter_conflicting_writes(
    {
        "lock": {"text": "4417"},
        "builder": {"text": "Jent"},
        boards.JOBSTART_P_COL_OPPORTUNITY: {"item_ids": [9]},
    },
    {"builder": "Danis"},  # lock empty on Monday
)
check("blank Monday cell is filled", "lock" in safe)
check("filled disagreeing cell is conflicted out", "builder" not in safe)
check("one conflict reported", len(conflicts) == 1)
check("opportunity link always writes",
      boards.JOBSTART_P_COL_OPPORTUNITY in safe)


# ---------------------------------------------------------------------------
# 4. Display helpers
# ---------------------------------------------------------------------------

check("label payload displays",
      mj._proposed_display({"label": "Upcoming"}) == "Upcoming")
check("date payload displays",
      mj._proposed_display({"date": "2026-08-15"}) == "2026-08-15")
check("relation payload has no text compare",
      mj._proposed_display({"item_ids": [1, 2]}) == "")
check("norm equal on empty", mj._norm_compare("", ""))
check("norm unequal", not mj._norm_compare("a", "b"))


print(f"\n{PASS} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL: {f}")
sys.exit(1 if FAIL else 0)
