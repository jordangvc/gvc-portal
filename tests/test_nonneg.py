"""5 Daily Non-Negotiables — streak math, toggle rules, and the owner gate.

The gate case that matters: a provisioned ``*`` admin (Andrea) must get 404,
not the page — the tool is personal to the superadmin allowlist, not a grant.

Runs under pytest OR directly: ``python tests/test_nonneg.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("GVC_SESSION_SECRET", "nonneg-test-secret-0123456789abcdef")

# app.service imports WeasyPrint at module load; CI has the native libs, the
# Windows dev box doesn't. Stub ONLY when genuinely absent (CI keeps the real
# one) — same pattern as scripts/screenshot_portal.py.
try:  # noqa: SIM105
    import weasyprint  # noqa: F401
except ImportError:
    import types
    _wp = types.ModuleType("weasyprint")
    _wp.HTML = object
    _wp.CSS = object
    sys.modules["weasyprint"] = _wp

from subsystems.nonneg import tracker  # noqa: E402

D = date.fromisoformat
START = D(tracker.START_DATE)  # 2026-08-10, a Monday


def _perfect() -> dict:
    return {k: True for k in tracker.HABIT_KEYS}


def _doc(days: dict) -> dict:
    doc = tracker.blank_doc()
    doc["days"] = days
    return doc


# ---- streak math ----------------------------------------------------------

def test_before_start_nothing_counts():
    st = tracker.compute_stats(tracker.blank_doc(), today=D("2026-08-09"))
    assert st["started"] is False
    assert st["starts_in_days"] == 1
    assert st["current_streak"] == 0 and st["day_number"] == 0


def test_streak_counts_consecutive_perfect_days():
    days = {"2026-08-10": _perfect(), "2026-08-11": _perfect(),
            "2026-08-12": _perfect()}
    st = tracker.compute_stats(_doc(days), today=D("2026-08-12"))
    assert st["current_streak"] == 3
    assert st["longest_streak"] == 3
    assert st["perfect_days"] == 3


def test_today_in_progress_does_not_break_streak():
    days = {"2026-08-10": _perfect(), "2026-08-11": _perfect()}
    st = tracker.compute_stats(_doc(days), today=D("2026-08-12"))
    # 8/12 is still open — streak holds at 2, and 8/12's blanks aren't misses.
    assert st["current_streak"] == 2
    assert all(v == 0 for v in st["misses"].values())


def test_missed_day_resets_to_zero():
    days = {"2026-08-10": _perfect(),
            "2026-08-11": {"goals": True, "sweat": True},  # 2/5 — broken
            "2026-08-12": _perfect()}
    st = tracker.compute_stats(_doc(days), today=D("2026-08-12"))
    assert st["current_streak"] == 1          # today only
    assert st["longest_streak"] == 1
    assert st["misses"]["read"] == 1 and st["misses"]["goals"] == 0
    assert st["most_missed"] in ("read", "post", "send")


def test_absent_day_is_a_missed_day():
    days = {"2026-08-10": _perfect()}  # 8/11 never touched
    st = tracker.compute_stats(_doc(days), today=D("2026-08-12"))
    assert st["current_streak"] == 0
    assert st["misses"]["goals"] == 1  # 8/11 missed all five


def test_week_grid_is_monday_first_and_marks_today():
    st = tracker.compute_stats(_doc({}), today=D("2026-08-12"))
    week = st["week"]
    assert [w["dow"] for w in week] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert week[0]["date"] == "2026-08-10"
    assert [w["is_today"] for w in week] == [False, False, True, False, False, False, False]
    assert week[6]["in_challenge"] is False  # future Sunday


# ---- toggle rules ---------------------------------------------------------

def test_toggle_sets_and_clears():
    doc = tracker.blank_doc()
    doc = tracker.toggle(doc, "2026-08-10", "sweat", True, today=START)
    assert doc["days"]["2026-08-10"]["sweat"] is True
    doc = tracker.toggle(doc, "2026-08-10", "sweat", False, today=START)
    assert doc["days"]["2026-08-10"]["sweat"] is False


def test_toggle_rejects_future_prestart_and_garbage():
    doc = tracker.blank_doc()
    for bad in (("2026-08-11", "sweat"),):     # tomorrow
        try:
            tracker.toggle(doc, *bad, True, today=START)
            raise AssertionError("future day accepted")
        except ValueError:
            pass
    for bad_args in (("2026-08-09", "sweat"), ("nope", "sweat"),
                     ("2026-08-10", "situps")):
        try:
            tracker.toggle(doc, bad_args[0], bad_args[1], True, today=START)
            raise AssertionError(f"accepted {bad_args}")
        except ValueError:
            pass


def test_goals_tick_stamps_review_date():
    doc = tracker.toggle(tracker.blank_doc(), "2026-08-10", "goals", True,
                         today=START)
    assert doc["goals_last_review"] == "2026-08-10"


def test_set_goals_pads_trims_and_caps():
    doc = tracker.set_goals(tracker.blank_doc(), ["  win  ", "x" * 900])
    assert doc["goals"][0] == "win"
    assert len(doc["goals"][1]) == 500
    assert len(doc["goals"]) == 5


# ---- owner gate -----------------------------------------------------------

def _client():
    from fastapi.testclient import TestClient
    from app import service
    return TestClient(service.app), service


def _cookie(email: str) -> dict:
    from shared import auth as portal_auth
    return {portal_auth.SESSION_COOKIE: portal_auth.make_session_cookie(email)}


def test_routes_register():
    _, service = _client()
    paths = {getattr(r, "path", None) for r in service.app.routes}
    for p in ("/ui/nonneg", "/ui/api/nonneg", "/ui/api/nonneg/day",
              "/ui/api/nonneg/goals"):
        assert p in paths, p


def test_owner_gets_page_admin_gets_404(monkeypatch):
    client, _ = _client()
    from shared import access
    monkeypatch.setenv("GVC_PORTAL_ALLOWED_EMAILS", "owner-test@localhost")
    # Andrea's shape: provisioned with full grants, NOT in the superadmin env.
    monkeypatch.setattr(access, "is_provisioned", lambda e: True)
    monkeypatch.setattr(access, "effective_features",
                        lambda e: set(access.ALL_FEATURES))

    r = client.get("/ui/nonneg", cookies=_cookie("owner-test@localhost"),
                   follow_redirects=False)
    assert r.status_code == 200 and "5 Daily" in r.text

    r = client.get("/ui/nonneg", cookies=_cookie("andrea-test@localhost"),
                   follow_redirects=False)
    assert r.status_code == 404

    r = client.get("/ui/api/nonneg", cookies=_cookie("andrea-test@localhost"),
                   follow_redirects=False)
    assert r.status_code == 404


def test_personal_nav_group_is_owner_only(monkeypatch):
    from shared import hub_nav
    monkeypatch.setenv("GVC_PORTAL_ALLOWED_EMAILS", "owner-test@localhost")
    owner_groups = hub_nav.groups_for_client(set(), email="owner-test@localhost")
    other_groups = hub_nav.groups_for_client(set(), email="andrea-test@localhost")
    assert any(g["name"] == "Personal" for g in owner_groups)
    # Invisible — not merely dimmed — for everyone else.
    assert not any(g["name"] == "Personal" for g in other_groups)


def test_day_put_round_trip_with_fake_store(monkeypatch):
    client, _ = _client()
    from shared import access
    from subsystems.nonneg import store
    monkeypatch.setenv("GVC_PORTAL_ALLOWED_EMAILS", "owner-test@localhost")
    monkeypatch.setattr(access, "is_provisioned", lambda e: True)

    state: dict = {}

    def fake_read(obj):
        return dict(state), 1

    def fake_mutate(obj, fn):
        new, result = fn(dict(state))
        state.clear()
        state.update(new)
        return result

    monkeypatch.setattr(store, "read_doc", fake_read)
    monkeypatch.setattr(store, "mutate", fake_mutate)

    r = client.put("/ui/api/nonneg/day",
                   cookies=_cookie("owner-test@localhost"),
                   json={"date": tracker.START_DATE, "key": "sweat",
                         "done": True})
    # Valid only once the wall clock reaches the start date; before that the
    # route must 422 with a clean INVALID_INPUT envelope, never 500.
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["days"][tracker.START_DATE]["sweat"] is True
    else:
        assert r.json()["detail"]["code"] == "INVALID_INPUT"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
