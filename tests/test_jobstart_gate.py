"""
Job Start — the two gates, and what happens when a value can't be written.
=========================================================================
Self-running (there is no pytest on this machine — same pattern as
tests/test_jobcheck.py and tests/test_lien_watch.py):

    python tests/test_jobstart_gate.py

WHY THIS FILE EXISTS. The first live handoff attempt (Bryant/Jent, 2026-07-30)
died on `PACKET_INVALID — Some packet fields couldn't be saved`, thrown from
accept(). That was a dead end by construction: accept() is reached by
OPERATIONS, and an in-review packet is read-only, so the advice "fix the
highlighted fields" named fields the reader could not edit. Nothing had been
written to Monday — the whole handoff was refused on account of one value.

The rules these tests hold in place:
  1. Completeness and WRITABILITY are two different gates. A field can be
     filled in and still be unwritable (a status label the target board doesn't
     carry, a date in the wrong shape).
  2. Writability is checked at SEND, while the packet is still editable and the
     person who typed the value is the one being told.
  3. At ACCEPT it never blocks. The bad value is dropped, the job is still
     created, and the field is named — the same contract as `manual_columns`.
  4. A date a human typed into a Monday update ("8/15/2026") is a value we can
     plainly read, so it is normalized rather than rejected.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# WeasyPrint isn't installed on this box and this module doesn't need it —
# jobstart_flow imports the packet renderer lazily, but stub it so an import
# order change can't turn this suite red for an unrelated reason.
if "weasyprint" not in sys.modules:
    _stub = types.ModuleType("weasyprint")
    _stub.HTML = object
    sys.modules["weasyprint"] = _stub

from orchestrators import jobstart_flow as jf  # noqa: E402
from shared import boards  # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} {extra}".strip())



def main() -> int:
    global PASS, FAIL
    PASS = 0
    FAIL = []
    # A packet that satisfies every required field, all values writable.
    GOOD = {
        "project_type": "Commercial",
        "builder": "Jent Construction",
        "supervisor": "Robert R.",
        "scope": "Hang and finish level 4 throughout.",
        "exclusions": "Painting NIC. FRP by others.",
        "start_date": "2026-08-15",
        "board_count": "340",
        "lock_box": "4417 front door",
    }
    LIVE_LABELS = {
        "project_type": ["Residential", "Commercial"],
        "ceiling_finish": ["Knockdown", "Smooth"],
        "window_type": ["Vinyl", "Aluminum"],
    }


    # ---------------------------------------------------------------------------
    # 1. The two gates are genuinely different
    # ---------------------------------------------------------------------------

    check("clean packet is complete", jf.missing_required(GOOD) == [])
    check("clean packet is writable", jf.writability_errors(GOOD) == {})

    filled_but_unwritable = dict(GOOD, start_date="whenever they call")
    check("unwritable value still counts as PRESENT",
          jf.missing_required(filled_but_unwritable) == [],
          "completeness must not silently absorb a writability fault")
    check("unwritable value IS caught by the writability gate",
          "start_date" in jf.writability_errors(filled_but_unwritable))


    # ---------------------------------------------------------------------------
    # 2. Status labels: only checkable against the live board
    # ---------------------------------------------------------------------------

    wrong_label = dict(GOOD, project_type="Multi-Family")
    check("bad status label passes when labels are unknown",
          jf.writability_errors(wrong_label) == {},
          "no Monday round-trip on autosave, so this can't be caught there")
    errs = jf.writability_errors(wrong_label, status_labels=LIVE_LABELS)
    check("bad status label is caught against live labels",
          "project_type" in errs)
    check("the reason names the offending label",
          "Multi-Family" in errs.get("project_type", ""), errs)
    check("good status label passes against live labels",
          jf.writability_errors(GOOD, status_labels=LIVE_LABELS) == {})

    # THE REGRESSION THIS FILE WAS WRITTEN FOR: project_type is prefilled from the
    # BID board's own Project Type column and written to the PROJECTS board's. The
    # two label sets are maintained by hand, in two places, and need not agree.
    check("cross-board label drift is a writability fault, not a crash",
          list(jf.writability_errors(
              dict(GOOD, project_type="Commercial - New Construction"),
              status_labels=LIVE_LABELS)) == ["project_type"])


    # ---------------------------------------------------------------------------
    # 3. Dates a human typed into a Monday update are normalized, not rejected
    # ---------------------------------------------------------------------------

    for raw, want in (("2026-08-15", "2026-08-15"),
                      ("8/15/2026", "2026-08-15"),
                      ("08/15/2026", "2026-08-15"),
                      ("8/15/26", "2026-08-15"),
                      ("8-15-2026", "2026-08-15"),
                      ("8.15.2026", "2026-08-15")):
        got = jf.shape_value("date", raw)
        check(f"date {raw!r} normalizes", got == {"date": want}, f"got {got!r}")

    check("month-first is assumed (OH/IN/KY), so 3/4 is March 4th",
          jf.shape_value("date", "3/4/2026") == {"date": "2026-03-04"})
    for junk in ("whenever", "2026-13-45", "next Tuesday", "15/8/2026"):
        try:
            jf.shape_value("date", junk)
            check(f"junk date {junk!r} rejected", False, "no ValueError raised")
        except ValueError:
            check(f"junk date {junk!r} rejected", True)


    # ---------------------------------------------------------------------------
    # 4. accept() degrades: the bad field is dropped, the rest is still written
    # ---------------------------------------------------------------------------

    mixed = dict(GOOD, project_type="Multi-Family", lot="12")
    writes, unwritable, accepted = jf.build_writes(mixed, status_labels=LIVE_LABELS)
    check("the unwritable field is reported", list(unwritable) == ["project_type"])
    check("project_type's own column is absent from the writes",
          "status" not in writes["projects"], writes["projects"])
    check("every OTHER field still writes", "supervisor" in accepted
          and "lot" in accepted and "lock_box" in accepted)
    check("both boards still receive their columns",
          bool(writes["projects"]) and bool(writes["operations"]))
    check("a required-but-unwritable field does not empty the packet",
          len(accepted) >= 8, f"{len(accepted)} fields accepted")


    # ---------------------------------------------------------------------------
    # 5. Unknown keys are reported, never silently dropped
    # ---------------------------------------------------------------------------

    _w, bad_key, _a = jf.build_writes(dict(GOOD, favourite_colour="green"))
    check("an unknown packet key is reported", "favourite_colour" in bad_key)
    check("an unknown key doesn't take the real fields with it",
          "builder" in jf.build_writes(dict(GOOD, favourite_colour="green"))[2])


    # ---------------------------------------------------------------------------
    # 6. Empty means "leave the column alone" — a handoff never clears a column
    # ---------------------------------------------------------------------------

    check("empty string writes nothing", jf.shape_value("text", "   ") is None)
    check("None writes nothing", jf.shape_value("date", None) is None)
    _w2, e2, a2 = jf.build_writes(dict(GOOD, lot="", ceiling_finish="   "))
    check("blank optional fields are not errors", e2 == {})
    check("blank optional fields are not writes",
          "lot" not in a2 and "ceiling_finish" not in a2)


    # ---------------------------------------------------------------------------
    # 7. What the human is told
    # ---------------------------------------------------------------------------

    rows = jf.field_error_list({"project_type": "'X' is not a label on this board column."})
    check("field_error_list carries the form label",
          rows == [{"key": "project_type", "label": "Project type",
                    "reason": "'X' is not a label on this board column."}], rows)
    check("label_for falls back to the raw key",
          jf.label_for("not_a_field") == "not_a_field")

    detail = jf._unwritable_detail({"start_date": "Not a valid date (use YYYY-MM-DD): 'soon'"})
    check("the detail names the field by its LABEL, not its key",
          "Start date" in detail or "start" in detail.lower(), detail)
    check("the detail is singular for one field", "1 field can't" in detail, detail)
    two = jf._unwritable_detail({"start_date": "a", "project_type": "b"})
    check("the detail is plural for two fields", "2 fields can't" in two, two)


    # ---------------------------------------------------------------------------
    # 8. Hard exclusions still hold — a config edit can't open a money column
    # ---------------------------------------------------------------------------

    check("no packet field targets a hard-excluded column",
          all(col not in boards.JOBSTART_HARD_EXCLUDED_IDS
              for f in jf.packet_fields() for _b, col in f["targets"]))
    check("every packet field uses a supported render type",
          all(f["type"] in boards.JOBSTART_RENDER_TYPES for f in jf.packet_fields()))


    print(f"\n{PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAIL: {f}")
    return 1 if FAIL else 0


def test_jobstart_gate_standalone():
    """Pytest entry — keeps this file collectable and CI-gated."""
    code = main()
    assert code == 0, FAIL


if __name__ == "__main__":
    raise SystemExit(main())
