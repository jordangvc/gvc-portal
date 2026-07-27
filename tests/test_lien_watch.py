"""
Lien Watch pure-logic tests — deadline math, state parsing, severity, the
dark-alert gate. First file of the REBUILT tests/ tree (the pre-recovery
441-test suite didn't survive the Cloud Run bundle; see CLAUDE.md 2026-07-26).
Everything here is pure or env-gated — no Monday, no Slack, no network.
Runs under pytest OR directly: `python tests/test_lien_watch.py`.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.lien_watch import deadlines as lw  # noqa: E402
from orchestrators import lien_flow  # noqa: E402


TODAY = date(2026, 7, 26)


def _kinds(result):
    return {d["kind"] for d in result["deadlines"]}


def _row(result, kind):
    return next(d for d in result["deadlines"] if d["kind"] == kind)


# ---------------------------------------------------------------- rules pack

def test_rules_pack_loads_and_is_unblessed():
    rules = lw.load_rules()
    assert rules["version"] == "2026-07-26"
    assert rules["entries"], "rules pack has no entries"
    for entry in rules["entries"]:
        assert entry["attorney_reviewed"] is False, entry["id"]
        assert entry.get("sources"), f"{entry['id']} lost its source citation"
        assert entry["state"] in ("OH", "IN", "KY")
        assert entry["category"] in ("lien", "retainage")


# ------------------------------------------------------------- state parsing

def test_parse_state_from_location_and_name():
    assert lw.parse_state("5777 Kellogg Ave, Cincinnati, OH 45230, USA") == "OH"
    assert lw.parse_state("1579 N Dearborn Rd, West Harrison, IN, USA") == "IN"
    assert lw.parse_state("Covington, Kentucky") == "KY"
    assert lw.parse_state("5845 Howe Rd, Trenton, Ohio 45067") == "OH"
    assert lw.parse_state(None, "910 SE Mohawk Trail, Greensburg IN 47240 - Jeff") == "IN"
    # The English word "in" must never classify a job as Indiana.
    assert lw.parse_state("Basement in the back") is None
    assert lw.parse_state("Yelton Basement - Residential") is None
    assert lw.parse_state(None, None) is None


def test_normalize_project_type_board_labels():
    assert lw.normalize_project_type("Residential") == "residential"
    assert lw.normalize_project_type("Commercial") == "commercial"
    assert lw.normalize_project_type("Repair") == "unknown"
    assert lw.normalize_project_type("Standard Project") == "unknown"
    assert lw.normalize_project_type(None) == "unknown"


# ----------------------------------------------------------------- severity

def test_severity_bands():
    assert lw.severity_for(None) == "unknown"
    assert lw.severity_for(-1) == "missed"
    assert lw.severity_for(0) == "critical"
    assert lw.severity_for(3) == "critical"
    assert lw.severity_for(4) == "warn"
    assert lw.severity_for(14) == "warn"
    assert lw.severity_for(15) == "ok"


def test_worst_severity():
    assert lw.worst_severity(["ok", "warn", "missed"]) == "missed"
    assert lw.worst_severity(["ok", "unknown"]) == "ok"
    assert lw.worst_severity([]) == "unknown"


# ------------------------------------------------------------ deadline math

def test_oh_commercial_nof_21_days():
    r = lw.compute_deadlines("OH", "commercial", date(2026, 7, 20), today=TODAY)
    nof = _row(r, "notice_of_furnishing")
    assert nof["due_date"] == "2026-08-10"          # +21 days
    assert nof["days_remaining"] == 15
    assert nof["severity"] == "ok"
    assert nof["attorney_reviewed"] is False
    # Last-furnishing anchors surface with NO fabricated date.
    lien = _row(r, "lien_filing")
    assert lien["due_date"] is None and lien["severity"] == "unknown"
    assert "75 days" in lien["window"]


def test_oh_residential_has_no_nof_but_advisory():
    r = lw.compute_deadlines("OH", "residential", date(2026, 7, 1), today=TODAY)
    assert "notice_of_furnishing" not in _kinds(r)
    assert "60 days" in _row(r, "lien_filing")["window"]
    assert any("1311.011" in a for a in r["advisories"])


def test_in_residential_pre_lien_30_and_60():
    r = lw.compute_deadlines("IN", "residential", date(2026, 7, 24), today=TODAY)
    repair = _row(r, "pre_lien_notice_repair")
    new = _row(r, "pre_lien_notice_new_construction")
    assert repair["due_date"] == "2026-08-23"       # +30
    assert new["due_date"] == "2026-09-22"          # +60
    assert repair["days_remaining"] == 28 and new["days_remaining"] == 58


def test_in_commercial_has_no_pre_lien_notice():
    r = lw.compute_deadlines("IN", "commercial", date(2026, 7, 1), today=TODAY)
    assert not any(k.startswith("pre_lien") for k in _kinds(r))
    assert "90 days" in _row(r, "lien_filing")["window"]


def test_ky_six_month_filing_window_and_month_math():
    r = lw.compute_deadlines("KY", "commercial", date(2026, 7, 1), today=TODAY)
    lien = _row(r, "lien_filing")
    assert lien["window"] == "6 months"
    assert lien["due_date"] is None                  # last-furnishing anchor
    notice = _row(r, "notice_of_intent")
    assert "120 days" in notice["window"]
    # calendar-month addition clamps to month end
    assert lw._add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert lw._add_months(date(2026, 1, 15), 6) == date(2026, 7, 15)


def test_public_rules_only_apply_when_explicitly_public():
    pub = lw.compute_deadlines("OH", "public", date(2026, 7, 1), today=TODAY)
    assert "public_fund_affidavit" in _kinds(pub)
    unk = lw.compute_deadlines("OH", "unknown", date(2026, 7, 1), today=TODAY)
    assert "public_fund_affidavit" not in _kinds(unk)


def test_unknown_type_computes_both_variants_marked_ambiguous():
    r = lw.compute_deadlines("OH", "unknown", date(2026, 7, 20), today=TODAY)
    liens = [d for d in r["deadlines"] if d["kind"] == "lien_filing"]
    assert {d["window"] for d in liens} == {"60 days", "75 days"}
    assert all(d["ambiguous"] for d in r["deadlines"])
    # NOF only exists on the commercial variant but still shows up.
    assert "notice_of_furnishing" in _kinds(r)


def test_unknown_type_identical_variants_collapse_to_either():
    r = lw.compute_deadlines("KY", "unknown", date(2026, 7, 1), today=TODAY)
    lien = _row(r, "lien_filing")                    # 6 months both ways
    assert lien["project_type_variant"] == "either"


def test_missing_inputs_degrade_to_unknown():
    assert lw.compute_deadlines(None, "commercial", date(2026, 7, 1),
                                today=TODAY)["state_known"] is False
    assert lw.compute_deadlines("TX", "commercial", date(2026, 7, 1),
                                today=TODAY)["deadlines"] == []
    r = lw.compute_deadlines("OH", "commercial", None, today=TODAY)
    assert all(d["severity"] == "unknown" for d in r["deadlines"])
    assert r["max_severity"] == "unknown"


def test_missed_and_critical_bands_end_to_end():
    # first furnishing 30 days ago ⇒ OH NOF (+21) is 9 days overdue
    r = lw.compute_deadlines("OH", "commercial", TODAY.replace(month=6, day=26),
                             today=TODAY)
    nof = _row(r, "notice_of_furnishing")
    assert nof["days_remaining"] == -9 and nof["severity"] == "missed"
    assert r["max_severity"] == "missed"
    assert r["deadlines"][0]["kind"] == "notice_of_furnishing"  # most urgent first


# ----------------------------------------------------- first-furnishing basis

def test_first_furnishing_prefers_start_date_then_created():
    job = {"start_date": "2026-07-20", "created_at": "2026-05-01T10:00:00Z",
           "group_id": "topics"}
    assert lien_flow._first_furnishing(job) == (date(2026, 7, 20), "start_date")
    job = {"start_date": None, "created_at": "2026-05-01T10:00:00Z",
           "group_id": "topics"}
    assert lien_flow._first_furnishing(job) == (date(2026, 5, 1), "assumed")


def test_not_started_jobs_get_no_assumed_clock():
    from adapters.monday.lien import NOT_STARTED_GROUP_ID
    job = {"start_date": None, "created_at": "2025-05-08T16:57:28Z",
           "group_id": NOT_STARTED_GROUP_ID}
    assert lien_flow._first_furnishing(job) == (None, None)
    # …but an explicit Start Date wins even in the Not Started group.
    job["start_date"] = "2026-08-01"
    assert lien_flow._first_furnishing(job) == (date(2026, 8, 1), "start_date")


# ------------------------------------------------- Monday row normalization

def _raw_item(name="123 Main St, Cincinnati, OH - Builder", group="topics",
              title="Work-In-Progress Projects", **cols):
    defaults = {"date": None, "location5": None, "status": None,
                "deal_stage": None, "text_mm4fvj91": None}
    defaults.update(cols)
    return {"id": "101", "name": name, "created_at": "2026-05-01T10:00:00Z",
            "group": {"id": group, "title": title},
            "column_values": [{"id": k, "text": v} for k, v in defaults.items()]}


def test_normalize_keeps_active_jobs_and_maps_columns():
    from adapters.monday import lien as ml
    row = ml._normalize(_raw_item(date="2026-07-20",
                                  location5="Cincinnati, OH 45230",
                                  status="Commercial"))
    assert row["item_id"] == 101
    assert row["start_date"] == "2026-07-20"
    assert row["location"] == "Cincinnati, OH 45230"
    assert row["project_type_label"] == "Commercial"
    assert row["created_at"] == "2026-05-01T10:00:00Z"
    assert str(ml.PROJECTS_BOARD_ID) in row["url"]


def test_normalize_skips_closed_co_and_lost_rows():
    from adapters.monday import lien as ml
    assert ml._normalize(_raw_item(group="closed",
                                   title="Completed and Paid Projects")) is None
    assert ml._normalize(_raw_item(name="CO.1 - 123 Main St - Builder")) is None
    assert ml._normalize(_raw_item(deal_stage="Project Lost/canceled")) is None
    # completed-but-UNPAID groups stay in — that's where lien rights matter
    assert ml._normalize(_raw_item(group="duplicate_of_for_internal_qual__1",
                                   title="Work Completed 100% (Unpaid)")) is not None


# ------------------------------------------------------------- dark alerts

def test_alert_gate_is_dark_by_default():
    saved = os.environ.pop("GVC_LIEN_ALERTS_ENABLED", None)
    try:
        out = lien_flow.send_lien_alerts({"jobs": [{"item_id": 1, "deadlines": [
            {"kind": "notice_of_furnishing", "days_remaining": 7}]}]})
        assert out == {"ok": True, "enabled": False, "sent": [], "errors": []}
        # Near-true values must NOT open the gate.
        for value in ("1", "yes", "True", "TRUE", " true "):
            os.environ["GVC_LIEN_ALERTS_ENABLED"] = value
            if value.strip() == "true":
                continue
            out = lien_flow.send_lien_alerts({"jobs": []})
            assert out["enabled"] is False, f"gate opened on {value!r}"
    finally:
        os.environ.pop("GVC_LIEN_ALERTS_ENABLED", None)
        if saved is not None:
            os.environ["GVC_LIEN_ALERTS_ENABLED"] = saved


def test_alert_marks_and_message_when_enabled_dry_run():
    saved_flag = os.environ.get("GVC_LIEN_ALERTS_ENABLED")
    saved_chan = os.environ.get("GVC_LIEN_SLACK_CHANNEL")
    os.environ["GVC_LIEN_ALERTS_ENABLED"] = "true"
    os.environ["GVC_LIEN_SLACK_CHANNEL"] = "C000TEST"
    try:
        tracker = {"jobs": [{
            "item_id": 42, "name": "Job A", "url": "https://x", "state": "OH",
            "first_furnishing": "2026-07-20", "first_furnishing_basis": "assumed",
            "deadlines": [
                {"kind": "notice_of_furnishing", "label": "Notice of Furnishing",
                 "days_remaining": 7, "due_date": "2026-08-02",
                 "statute": "ORC 1311.05", "anchor_text": "first furnishing"},
                {"kind": "lien_filing", "label": "Lien affidavit",
                 "days_remaining": 10, "due_date": "2026-08-05",
                 "statute": "ORC 1311.06", "anchor_text": "last furnishing"},
                {"kind": "x", "label": "x", "days_remaining": None,
                 "due_date": None, "statute": "", "anchor_text": ""},
            ]}]}
        out = lien_flow.send_lien_alerts(tracker, dry_run=True)
        assert out["enabled"] is True and out["ok"] is True
        # only the T-14/7/3/1 marks ping — 10 days out and undated rows don't
        assert [e["kind"] for e in out["sent"]] == ["notice_of_furnishing"]
        msg = lien_flow._alert_message(tracker["jobs"][0],
                                       tracker["jobs"][0]["deadlines"][0])
        assert "due in 7 days" in msg and "ORC 1311.05" in msg
        assert "assumed from item creation" in msg
        assert "unverified by counsel" in msg
    finally:
        for key, val in (("GVC_LIEN_ALERTS_ENABLED", saved_flag),
                         ("GVC_LIEN_SLACK_CHANNEL", saved_chan)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def test_alerts_enabled_but_no_channel_is_a_loud_noop():
    saved_flag = os.environ.get("GVC_LIEN_ALERTS_ENABLED")
    saved_chan = os.environ.pop("GVC_LIEN_SLACK_CHANNEL", None)
    os.environ["GVC_LIEN_ALERTS_ENABLED"] = "true"
    try:
        out = lien_flow.send_lien_alerts({"jobs": []})
        assert out["ok"] is False and out["sent"] == []
        assert any("GVC_LIEN_SLACK_CHANNEL" in e for e in out["errors"])
    finally:
        if saved_flag is None:
            os.environ.pop("GVC_LIEN_ALERTS_ENABLED", None)
        else:
            os.environ["GVC_LIEN_ALERTS_ENABLED"] = saved_flag
        if saved_chan is not None:
            os.environ["GVC_LIEN_SLACK_CHANNEL"] = saved_chan


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{failed} failed" if failed else "\nall tests passed")
    sys.exit(1 if failed else 0)
