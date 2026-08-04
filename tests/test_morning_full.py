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
from subsystems.morning import prep, route, action_requests as ar, media, owner_pulse  # noqa: E402
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
    ordered = flow._optimize_order([
        {"name": "B", "hard_time": None},
        {"name": "A", "hard_time": "09:00"},
    ])
    assert ordered[0]["name"] == "A"


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


if __name__ == "__main__":
    tests = [
        test_roles_in_features,
        test_prep_six_criteria_and_streak,
        test_maps_and_optimize,
        test_action_request_categories,
        test_pictures_folder_pick,
        test_owner_pulse_filters,
        test_financial_still_excluded,
        test_long_term_hold,
        test_link_column_url_ignores_gfolder_label,
        test_card_includes_gfolder_url,
        test_attach_gfolder_urls_soft_fails,
        test_hub_morning_route_aliases_registered,
        test_normalize_updated_at_feeds_hold_in_card,
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
