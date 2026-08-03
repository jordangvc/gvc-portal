"""
Job Start — open bids in the picker, and accepting one in place.
=========================================================================
Self-running:  python tests/test_jobstart_open_bids.py

Jordan, 2026-07-29: "this should have access to the open bids, not just the
accepted bids. He should be able to accept the bid in here as well."

Before this, adapters/monday/jobstart.fetch_accepted_bids() hard-filtered to
Stage = Accepted, so a bid was invisible to Job Start until somebody went to
Monday and flipped the stage by hand — backwards for the tool Sales is supposed
to work in.

The rules held in place here:
  1. Open bids are LISTED, not hidden behind a filter toggle, and they sort
     below work already in flight.
  2. Dead bids are excluded by STAGE, never by group. Stage and group have
     already drifted in the live data (the Bryant/Jent bid is stage Accepted
     while sitting in "Open Deals"; two accepted bids sit in Lost Deals), so
     filtering on group would hide won jobs.
  3. An unrecognised stage label means "open", never "hidden" — those labels are
     hand-edited in Monday.
  4. The stage write is idempotent: re-sending a packet must not rewrite a stage
     or re-move an item that is already right.
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
from orchestrators import jobstart_flow as jf  # noqa: E402
from shared import boards  # noqa: E402
from subsystems.jobstart import drafts  # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} {extra}".strip())


# ---------------------------------------------------------------------------
# 1. Stage classification
# ---------------------------------------------------------------------------

check("the accepted label is accepted",
      mj.stage_state("Accepted") == "accepted")
check("case and padding don't matter",
      mj.stage_state("  accepted ") == "accepted")
check("an open stage is open",
      mj.stage_state("Sent to Client") == "open")
check("a BLANK stage is open, not hidden",
      mj.stage_state(None) == "open" and mj.stage_state("") == "open",
      "a bid nobody has staged yet is still sellable")
check("an unrecognised label is open, not hidden",
      mj.stage_state("Waiting on GC pricing") == "open",
      "stage labels are hand-edited in Monday; hiding unknowns loses bids")
for dead in ("Project Lost/canceled", "LOST", "Cancelled", "canceled by owner"):
    check(f"{dead!r} is dead", mj.stage_state(dead) == "dead")
check("'Lost' inside a longer label still counts as dead",
      mj.stage_state("Closed - Lost to another sub") == "dead")


# ---------------------------------------------------------------------------
# 2. Normalizing a raw board row
# ---------------------------------------------------------------------------

def row(stage, group_id="topics", group_title="Open Deals", item_id=1):
    return {
        "id": str(item_id),
        "name": "9761 Gertrude | Jent Construction",
        "group": {"id": group_id, "title": group_title},
        "column_values": [
            {"id": boards.JOBSTART_BID_STAGE_COL, "text": stage, "value": None},
        ],
    }


accepted_row = mj._normalize_bid(row("Accepted"))
check("an accepted bid normalizes", accepted_row is not None)
check("the row carries its stage", accepted_row["stage"] == "Accepted")
check("the row carries its stage_state",
      accepted_row["stage_state"] == "accepted")
check("the row carries its group id", accepted_row["group_id"] == "topics")

open_row = mj._normalize_bid(row("Sent to Client"))
check("AN OPEN BID IS NOW LISTED", open_row is not None,
      "this is the whole point of the change")
check("the open bid is flagged open", open_row["stage_state"] == "open")

check("a lost bid is dropped", mj._normalize_bid(row("Project Lost/canceled")) is None)
check("a lost bid is dropped even from the Won group",
      mj._normalize_bid(row("Lost", group_id=boards.JOBSTART_BID_WON_GROUP)) is None)

# THE DRIFT CASE — the live Bryant/Jent bid: stage Accepted, group "Open Deals".
check("stage wins over group: accepted-in-open-deals is KEPT",
      mj._normalize_bid(row("Accepted", group_id="topics")) is not None)
check("the drift is surfaced", accepted_row["group_drift"] is True)
check("no drift flag when the group agrees",
      mj._normalize_bid(row("Accepted",
                            group_id=boards.JOBSTART_BID_WON_GROUP))["group_drift"] is False)
check("an OPEN bid in Open Deals is not 'drift'",
      open_row["group_drift"] is False,
      "drift only means 'won but not filed as won'")
# And the pair CLAUDE.md records as Accepted-but-parked-in-Lost-Deals:
kaiker = mj._normalize_bid(row("Accepted", group_id="closed",
                               group_title="Lost Deals"))
check("an accepted bid parked in Lost Deals is still listed", kaiker is not None,
      "filtering by group would have hidden a won job")
check("...and flagged as drift", kaiker["group_drift"] is True)


# ---------------------------------------------------------------------------
# 3. Work-list order
# ---------------------------------------------------------------------------

def rank(**kw):
    return jf.picker_rank(kw, statuses=drafts)


check("waiting on ops sorts first",
      rank(packet_status=drafts.STATUS_WITH_OPS) == 0)
check("sent back next", rank(packet_status=drafts.STATUS_SENT_BACK) == 1)
check("a started packet next", rank(packet_status=drafts.STATUS_DRAFT) == 2)
check("a part-filled packet with no status still counts as in progress",
      rank(packet_status=None, draft_filled=3) == 2)
check("won-but-not-started next", rank(stage_state="accepted") == 3)
check("open bids below that", rank(stage_state="open") == 4)
check("handed off sinks to the bottom",
      rank(packet_status=drafts.STATUS_ACCEPTED, stage_state="accepted") == 5)
check("an accepted PACKET sinks even on an open bid",
      rank(packet_status=drafts.STATUS_ACCEPTED, stage_state="open") == 5)
check("open bids never outrank blocked work",
      rank(stage_state="open") > rank(packet_status=drafts.STATUS_WITH_OPS))
check("a missing stage_state degrades to 'open', not to the top",
      rank(packet_status=None) == 4)


# ---------------------------------------------------------------------------
# 4. The stage write — idempotent, independent, never raises
# ---------------------------------------------------------------------------

class FakeClient:
    """Records the mutations it's asked to run; can be told to fail."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or ()

    def _query(self, query, variables=None):
        kind = ("move" if "move_item_to_group" in query
                else "update" if "change_multiple_column_values" in query
                else "other")
        self.calls.append((kind, variables))
        if kind in self.fail_on:
            raise RuntimeError(f"monday said no ({kind})")
        return {"change_multiple_column_values": {"id": "1"},
                "move_item_to_group": {"id": "1"}}


mc = FakeClient()
rep = mj.mark_bid_accepted(mc, 2776470967, current_stage="Sent to Client",
                           current_group="topics")
check("an open bid gets its stage written", rep["stage_written"] is True)
check("...and is moved to Won Deals", rep["group_moved"] is True)
check("...with no errors", rep["errors"] == [])
check("exactly two mutations ran", len(mc.calls) == 2, mc.calls)
kinds = [c[0] for c in mc.calls]
check("one update and one move", sorted(kinds) == ["move", "update"], kinds)

mc2 = FakeClient()
rep2 = mj.mark_bid_accepted(mc2, 1, current_stage="Accepted",
                            current_group=boards.JOBSTART_BID_WON_GROUP)
check("ALREADY CORRECT ⇒ no writes at all", mc2.calls == [], mc2.calls)
check("...and the report says nothing was done",
      rep2 == {"stage_written": False, "group_moved": False, "errors": []}, rep2)

mc3 = FakeClient()
rep3 = mj.mark_bid_accepted(mc3, 1, current_stage="Accepted",
                            current_group="topics")
check("stage already right, group wrong ⇒ only the move runs",
      [c[0] for c in mc3.calls] == ["move"], mc3.calls)
check("...reported as move-only",
      rep3["group_moved"] is True and rep3["stage_written"] is False)

mc4 = FakeClient(fail_on=("update",))
rep4 = mj.mark_bid_accepted(mc4, 1, current_stage="Open", current_group="topics")
check("a failed stage write NEVER raises", isinstance(rep4, dict))
check("...is reported", len(rep4["errors"]) == 1, rep4["errors"])
check("...and does NOT stop the group move", rep4["group_moved"] is True,
      "the two halves are independent on purpose")

mc5 = FakeClient(fail_on=("update", "move"))
rep5 = mj.mark_bid_accepted(mc5, 1, current_stage="Open", current_group="topics")
check("both halves failing still returns a report",
      len(rep5["errors"]) == 2 and rep5["stage_written"] is False, rep5)


# ---------------------------------------------------------------------------
# 5. Config sanity
# ---------------------------------------------------------------------------

check("the Won group id is set", bool(boards.JOBSTART_BID_WON_GROUP))
check("the Won group isn't the Lost group",
      boards.JOBSTART_BID_WON_GROUP != "closed")
check("dead-stage words are lowercase for substring matching",
      all(w == w.lower() for w in boards.JOBSTART_DEAD_STAGE_WORDS))
check("the accepted stage isn't itself a dead word",
      not any(w in boards.JOBSTART_ACCEPTED_STAGE.lower()
              for w in boards.JOBSTART_DEAD_STAGE_WORDS),
      "otherwise every won bid would vanish from the picker")


print(f"\n{PASS} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL: {f}")
sys.exit(1 if FAIL else 0)
