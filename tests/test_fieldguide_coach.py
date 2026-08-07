"""
Field Manual checklist coach — pure logic + orchestration smoke.
Runs under pytest OR directly: `python tests/test_fieldguide_coach.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrators import fieldguide_coach_flow  # noqa: E402
from subsystems.fieldguide import coach as fg_coach  # noqa: E402


def test_normalize_procedure_id():
    assert fg_coach.normalize_procedure_id("#hang") == "hang"
    assert fg_coach.normalize_procedure_id("Finish") == "finish"
    assert fg_coach.normalize_procedure_id("#finish/fin-levels") == "finish"
    assert fg_coach.normalize_procedure_id("") is None


def test_curated_procedures_present():
    required = {
        "framing", "hang", "scrape", "finish", "level5-skim", "cleanout",
        "act", "firestop", "preboard-walk", "ratedwalls", "changeorder",
        "stock-drywall", "qc-walk", "jobstart-firstday",
        "closeout-rhythm", "scaffold-lifts", "job-conditions", "window-returns",
    }
    assert required <= set(fg_coach.PROCEDURE_COACH.keys())
    for proc_id, entry in fg_coach.PROCEDURE_COACH.items():
        assert entry["title"] and entry["summary"]
        assert len(entry["next_steps"]) >= 3
        assert len(entry["related"]) >= 2
        for step in entry["next_steps"]:
            assert step["text"] and step["anchor"].startswith("#")
        for rel in entry["related"]:
            assert rel["id"] and rel["title"] and rel["why"]


def test_unknown_procedure_falls_back_to_home_and_ai_rules():
    out = fg_coach.build_coach_response(procedure="not-a-real-proc")
    assert out["ok"] is True
    assert out["known"] is False
    assert out["procedure"] == "not-a-real-proc"
    anchors = {s["anchor"] for s in out["next_steps"]}
    assert "#home" in anchors
    assert "#ai-field-rules" in anchors
    assert out["bans"] == list(fg_coach.COACH_BANS)


def test_stage_alias_resolves_finish():
    out = fg_coach.build_coach_response(stage="taped")
    assert out["known"] is True
    assert out["procedure"] == "finish"
    assert "Finishing" in out["title"]


def test_column_id_resolves_hang_on_projects():
    out = fieldguide_coach_flow.get_coach(
        column_id="status_19",
        board="projects",
    )
    assert out["known"] is True
    assert out["procedure"] == "hang"
    assert out["jobcheck_hint"]["column_id"] == "status_19"
    assert out["jobcheck_hint"]["label"] == "Hanging Status"
    assert out["jobcheck_hint"]["anchor"] == "#hang"


def test_ops_status_19_does_not_resolve_to_hang():
    out = fieldguide_coach_flow.get_coach(
        column_id="status_19",
        board="ops",
    )
    assert out["known"] is False
    assert out["jobcheck_hint"] is None


def test_orchestrator_get_coach_hang():
    out = fieldguide_coach_flow.get_coach(procedure="hang")
    assert out["ok"] is True
    assert out["procedure"] == "hang"
    assert "Hang" in out["title"]
    assert "preboard-walk" in {r["id"] for r in out["related"]}


def test_bans_never_empty():
    out = fieldguide_coach_flow.get_coach(procedure="firestop")
    assert len(out["bans"]) == 3
    assert "Job Check" in out["bans"][0]


if __name__ == "__main__":
    tests = [
        test_normalize_procedure_id,
        test_curated_procedures_present,
        test_unknown_procedure_falls_back_to_home_and_ai_rules,
        test_stage_alias_resolves_finish,
        test_column_id_resolves_hang_on_projects,
        test_ops_status_19_does_not_resolve_to_hang,
        test_orchestrator_get_coach_hang,
        test_bans_never_empty,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failed:
        sys.exit(1)
    print(f"All {len(tests)} tests passed.")
