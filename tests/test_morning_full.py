"""
Morning Brief full-surface tests.
Self-running:  python tests/test_morning_full.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import access, boards  # noqa: E402
from adapters.monday import morning as mm  # noqa: E402
from subsystems.morning import (  # noqa: E402
    prep, route, action_requests as ar, media, owner_pulse, weather,
)
from orchestrators import morning_flow as flow  # noqa: E402


def test_roles_in_features():
    for f in ("morning", "morning_ops", "morning_gm", "morning_owner"):
        assert f in access.FEATURES
    assert "morning" in access.BASELINE
    assert "morning_gm" not in access.BASELINE


def test_prep_six_criteria_and_streak():
    assert len(prep.CRITERIA) == 6
    assert prep.is_scheduled_workday(date(2026, 8, 3))
    assert not prep.is_scheduled_workday(date(2026, 8, 2))
    # Streak counts consecutive scheduled days starting AT as_of going back.
    missed = ["2026-08-05", "2026-08-04", "2026-08-03"]
    streak = prep.consecutive_miss_streak(missed, as_of=date(2026, 8, 5))
    assert streak == 3
    assert prep.streak_alert_level(3) != "none"
    assert prep.streak_alert_level(5) != "none"


def test_maps_and_optimize():
    url = route.maps_url(
        [{"name": "A", "location": "100 Main St"},
         {"name": "B", "location": "200 Oak Ave"}],
        origin={"kind": "office", "address": "Cincinnati, OH"},
    )
    assert url and "google.com/maps" in url
    assert "origin=" in url
    assert "destination=" in url
    assert "waypoints=" in url
    assert "100%20Main" in url or "100 Main" in url or "Main" in url
    assert "travelmode=driving" in url
    # Multi-stop: origin → A → B (B is destination, A is waypoint).
    assert "waypoints=" in url
    ordered = flow._optimize_order([
        {"name": "B", "hard_time": None},
        {"name": "A", "hard_time": "09:00"},
    ])
    assert ordered[0]["name"] == "A"


def test_maps_skips_completed_and_blank_stops():
    url = route.maps_url(
        [
            {"name": "Done", "location": "1 First St", "completed": True},
            {"name": "Live", "location": "2 Second Ave", "completed": False},
            {"name": "No addr", "location": "", "completed": False},
        ],
        origin={"address": "Office HQ"},
    )
    assert "Second" in url
    assert "First" not in url  # completed skipped when actives remain
    assert "destination=" in url


def test_weather_condition_and_drying_note():
    assert weather.condition_from_weathercode(0) == "clear"
    assert weather.condition_from_weathercode(61) == "rain"
    assert weather.condition_from_weathercode(999) is None
    rain_tip = weather.drying_note(
        condition="rain", weathercode=61, humidity_pct=80)
    assert rain_tip and "protect" in rain_tip.lower()
    humid = weather.drying_note(
        condition="cloudy", weathercode=3, humidity_pct=85)
    assert humid and "humidity" in humid.lower()
    good = weather.drying_note(
        condition="clear", weathercode=0, humidity_pct=40)
    assert good and "good drying" in good.lower()


def test_weather_payload_shape():
    payload = weather.build_weather_payload(
        {"label": "Office"},
        api_payload={
            "current": {
                "temperature_2m": 72.0,
                "relative_humidity_2m": 45,
                "precipitation": 0.0,
                "weather_code": 0,
                "precipitation_probability": 10,
            },
        },
    )
    assert payload["temp_f"] == 72.0
    assert payload["condition"] == "clear"
    assert "72" in (payload["summary"] or "")
    assert "clear" in (payload["summary"] or "")
    assert payload["humidity_pct"] == 45
    assert payload.get("drying_note")
    # Soft fail without coords.
    empty = weather.weather_for_origin({"label": "X"})
    assert empty["label"] == "X"
    assert empty.get("summary") is None


def test_nfj_migrate_plan_and_idempotency():
    rows = [
        {"item_id": 1, "name": "Hang A", "project_name": "100 Main | Acme",
         "needs_from_jordan": "Decision"},
        {"item_id": 2, "name": "Frame B", "needs_from_jordan": "Clear"},
        {"item_id": 3, "name": "Tape C", "needs_from_jordan": "Materials"},
    ]
    plans = ar.plan_nfj_migrations(rows, existing_requests={})
    assert {p["project_item_id"] for p in plans} == {1, 3}
    need1 = plans[0]["need"] if plans[0]["project_item_id"] == 1 else plans[1]["need"]
    assert "Needs from Jordan" in need1

    existing = {
        "abc": {
            "id": "abc",
            "status": ar.STATUS_NEEDS_TRIAGE,
            "project_item_id": 1,
            "source": ar.SOURCE_NEEDS_FROM_JORDAN,
            "need": need1,
        }
    }
    plans2 = ar.plan_nfj_migrations(rows, existing_requests=existing)
    assert {p["project_item_id"] for p in plans2} == {3}
    assert ar.existing_open_nfj(existing, project_item_id=1) is not None
    assert ar.active_nfj_label({"needs_from_jordan": "Clear"}) is None
    assert ar.active_nfj_label({"needs_from_jordan": "Help"}) == "Help"


def test_nfj_apply_create_triage_record():
    doc, rec = ar.apply_create(
        {},
        requester_email="gm@greenvalleycontractors.com",
        needed_from_email="jordan@greenvalleycontractors.com",
        category="decision_approval",
        need="100 Main — Needs from Jordan: Decision",
        trade_subtype=None,
        project_item_id=42,
        project_name="100 Main | Acme",
        due_at=None,
        status=ar.STATUS_NEEDS_TRIAGE,
        source=ar.SOURCE_NEEDS_FROM_JORDAN,
        allow_empty_needed_from=True,
    )
    assert rec["status"] == ar.STATUS_NEEDS_TRIAGE
    assert rec["source"] == ar.SOURCE_NEEDS_FROM_JORDAN
    assert rec["escalation"] == ar.ESCALATION_NEEDS_TRIAGE
    assert rec["project_item_id"] == 42
    # Second create for same item is detected as dup by existing_open_nfj.
    assert ar.existing_open_nfj(
        doc["requests"], project_item_id=42,
        need=rec["need"]) is not None


def test_action_request_categories():
    assert "materials" in ar.CATEGORIES
    assert "framing" in ar.TRADE_SUBTYPES


def test_pictures_folder_pick():
    folders = [
        {"id": "1", "name": "Other", "modifiedTime": "2026-01-01T00:00:00Z"},
        {"id": "2", "name": "Pictures", "modifiedTime": "2026-01-01T00:00:00Z"},
        {"id": "3", "name": "pictures", "modifiedTime": "2026-06-01T00:00:00Z"},
    ]
    picked = media.pick_pictures_folder(folders)
    assert picked["id"] == "3"


def test_owner_pulse_filters():
    pulse = owner_pulse.build_owner_pulse({
        "prep_pct": 80,
        "prep_alerts_3_5": [{"level": 3, "message": "visible"}],
        "planning_signals": [{"alert": True, "count": 3}],
        "safety_stops": [],
        "owner_decisions": [],
        "huddle_outcome": {"projects_covered": 2, "actions_assigned": 1},
    })
    assert pulse["team_prep_pct"] == 80
    assert pulse["has_exceptions"] is True
    assert len(pulse["preparation_alerts"]) == 1


def test_shape_owner_decisions_filters_and_links():
    owner = "jordan@greenvalleycontractors.com"
    requests = {
        "a": {
            "id": "a",
            "status": ar.STATUS_NEEDS_TRIAGE,
            "escalation": ar.ESCALATION_NEEDS_TRIAGE,
            "needed_from_email": owner,
            "requester_email": "gm@greenvalleycontractors.com",
            "category": "decision_approval",
            "need": "100 Main — Needs from Jordan: Decision",
            "project_item_id": 42,
            "project_name": "100 Main | Acme",
            "source": ar.SOURCE_NEEDS_FROM_JORDAN,
            "created_at": "2026-08-01T12:00:00+00:00",
        },
        "b": {
            "id": "b",
            "status": ar.STATUS_OPEN,
            "escalation": ar.ESCALATION_OVERDUE,
            "needed_from_email": owner,
            "requester_email": "ops@greenvalleycontractors.com",
            "category": "materials",
            "need": "Approve ladder rental",
            "project_item_id": 7,
            "project_name": "7 Oak",
            "created_at": "2026-08-02T12:00:00+00:00",
        },
        "c": {
            "id": "c",
            "status": ar.STATUS_COMPLETED,
            "escalation": ar.ESCALATION_NONE,
            "needed_from_email": owner,
            "need": "Done already",
            "project_item_id": 9,
        },
        "d": {
            "id": "d",
            "status": ar.STATUS_OPEN,
            "escalation": ar.ESCALATION_NONE,
            "needed_from_email": "someoneelse@greenvalleycontractors.com",
            "need": "Not for owner",
            "project_item_id": 11,
        },
        "e": {
            "id": "e",
            "status": ar.STATUS_OPEN,
            "escalation": ar.ESCALATION_NONE,
            "needed_from_email": "",
            "source": ar.SOURCE_NEEDS_FROM_JORDAN,
            "need": "NFJ with blank needed_from",
            "project_item_id": 99,
            "project_name": "Blank needed",
        },
    }
    out = ar.shape_owner_decisions(requests, owner_email=owner)
    ids = [r["id"] for r in out]
    assert ids[0] == "b"  # overdue first
    assert "a" in ids and "e" in ids
    assert "c" not in ids and "d" not in ids
    assert out[0]["href"] == "/ui/jobcheck?item=7"
    pulse = owner_pulse.build_owner_pulse({
        "prep_pct": 50,
        "owner_decisions": out,
        "safety_stops": [{"item_id": 1, "name": "Hazard job",
                          "blocked": "Safety stop",
                          "href": "/ui/jobcheck?item=1"}],
    })
    assert pulse["has_exceptions"] is True
    assert len(pulse["owner_decisions"]) == 3


def test_financial_still_excluded():
    mm.assert_no_financial_keys({"ok": True, "rows": [{"name": "x"}]})
    for cid in ("board_counts", "numeric_mm3fcjmn"):
        assert cid in boards.MORNING_HARD_EXCLUDED_IDS


def test_long_term_hold():
    from datetime import timezone
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    row = {
        "blocked": "Waiting on GC",
        "overdue": "",
        "updated_at": (now - timedelta(days=10)).isoformat(),
    }
    assert mm.is_attention(row)
    assert mm.is_long_term_hold(row, now=now)




def test_link_column_url_ignores_gfolder_label():
    from adapters.monday import jobcheck as mj
    from adapters.drive import folder_id_from_url
    assert mj._link_column_url({"text": "GFolder"}) is None
    assert folder_id_from_url("GFolder") is None
    url = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert mj._link_column_url({"url": url, "text": "GFolder"}) == url
    assert mj._link_column_url({
        "text": "GFolder",
        "value": '{"url":"%s","text":"GFolder"}' % url,
    }) == url


def test_card_includes_gfolder_url():
    row = {
        "item_id": 7,
        "name": "Hang",
        "url": "https://example.test/7",
        "blocked": "Clear",
        "overdue": "",
        "gfolder_url": "https://drive.google.com/drive/folders/abc",
    }
    card = flow._card(row, reason="Ops. Owner")
    assert card["gfolder_url"] == "https://drive.google.com/drive/folders/abc"
    # Missing GFolder soft-fails to None (UI shows disabled Open Drive).
    card2 = flow._card({**row, "gfolder_url": None}, reason="Ops. Owner")
    assert card2["gfolder_url"] is None


def test_attach_gfolder_urls_soft_fails():
    calls = []

    class _MC:
        pass

    def fake_get(mc, item_id):
        calls.append(item_id)
        if item_id == 2:
            raise RuntimeError("monday down")
        return f"https://drive.example/{item_id}"

    cards = [{"item_id": 1}, {"item_id": 2}, {"item_id": 1}]
    orig = mm.get_gfolder_url_for_ops_item
    mm.get_gfolder_url_for_ops_item = fake_get
    try:
        flow._attach_gfolder_urls(_MC(), cards)
    finally:
        mm.get_gfolder_url_for_ops_item = orig
    assert cards[0]["gfolder_url"] == "https://drive.example/1"
    assert cards[1]["gfolder_url"] is None
    assert cards[2]["gfolder_url"] == "https://drive.example/1"
    assert calls == [1, 2]


def test_hub_morning_route_aliases_registered():
    """Hub links /ui/morning-gm and /ui/morning-owner; canonical is /ui/morning/gm."""
    src = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    for path in ("/ui/morning/gm", "/ui/morning-gm",
                 "/ui/morning/owner", "/ui/morning-owner"):
        assert f'@app.get("{path}"' in src, path


def test_normalize_updated_at_feeds_hold_in_card():
    from datetime import datetime, timezone
    item = {
        "id": "88",
        "name": "Stale blocked",
        "updated_at": "2026-07-01T00:00:00Z",
        "group": {"id": "topics", "title": "Active"},
        "column_values": [
            {"id": boards.MORNING_COL_BLOCKED, "text": "Materials", "type": "status"},
        ],
    }
    row = mm._normalize(item)
    assert mm.is_long_term_hold(row, now=datetime(2026, 8, 3, tzinfo=timezone.utc))
    card = flow._card(row, reason="Long-term hold")
    assert card["updated_at"] == "2026-07-01T00:00:00Z"


def test_ar_escalation_sweep_dms_ack_and_overdue():
    """Daytime sweep DMs recipient; overdue also DMs owner — never a channel."""
    from adapters import slack_notify
    from zoneinfo import ZoneInfo

    now = datetime(2026, 8, 5, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    ack_rec = {
        "id": "ar-ack",
        "needed_from_email": "mark@greenvalleycontractors.com",
        "need": "Need more screws at site",
        "escalation": ar.ESCALATION_ACK_REMINDER,
    }
    overdue_rec = {
        "id": "ar-ovd",
        "needed_from_email": "robert@greenvalleycontractors.com",
        "need": "GC waiting on schedule",
        "escalation": ar.ESCALATION_OVERDUE,
    }
    calls = {"ack": [], "esc": [], "dm": []}
    orig_run = ar.run_escalations
    orig_ack = slack_notify.notify_action_request_ack_due
    orig_esc = slack_notify.notify_action_request_escalation
    orig_dm = slack_notify.post_dm
    ar.run_escalations = lambda _now: [ack_rec, overdue_rec]
    slack_notify.notify_action_request_ack_due = (
        lambda req: calls["ack"].append(req) or {"ok": True}
    )
    slack_notify.notify_action_request_escalation = (
        lambda req: calls["esc"].append(req) or {"ok": True}
    )
    slack_notify.post_dm = (
        lambda email, text: calls["dm"].append((email, text)) or {"ok": True}
    )
    try:
        out = flow.run_ar_escalation_sweep(now=now)
    finally:
        ar.run_escalations = orig_run
        slack_notify.notify_action_request_ack_due = orig_ack
        slack_notify.notify_action_request_escalation = orig_esc
        slack_notify.post_dm = orig_dm
    assert out["ok"] is True
    assert out["ar_escalated"] == 2
    assert [r["id"] for r in calls["ack"]] == ["ar-ack"]
    assert [r["id"] for r in calls["esc"]] == ["ar-ovd"]
    assert len(calls["dm"]) == 1
    assert "jordan@" in calls["dm"][0][0]
    assert "overdue" in calls["dm"][0][1].lower()


def test_ar_escalation_task_route_registered():
    src = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    assert '@app.post("/v1/tasks/morning-ar-escalations")' in src
    assert "run_ar_escalation_sweep" in src


if __name__ == "__main__":
    tests = [
        test_roles_in_features,
        test_prep_six_criteria_and_streak,
        test_maps_and_optimize,
        test_maps_skips_completed_and_blank_stops,
        test_weather_condition_and_drying_note,
        test_weather_payload_shape,
        test_nfj_migrate_plan_and_idempotency,
        test_nfj_apply_create_triage_record,
        test_action_request_categories,
        test_pictures_folder_pick,
        test_owner_pulse_filters,
        test_shape_owner_decisions_filters_and_links,
        test_financial_still_excluded,
        test_long_term_hold,
        test_link_column_url_ignores_gfolder_label,
        test_card_includes_gfolder_url,
        test_attach_gfolder_urls_soft_fails,
        test_hub_morning_route_aliases_registered,
        test_normalize_updated_at_feeds_hold_in_card,
        test_ar_escalation_sweep_dms_ack_and_overdue,
        test_ar_escalation_task_route_registered,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
