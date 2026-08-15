"""Coach — prompt grounding, response hardening, notes, and route contracts.

The property that matters: a malformed or malicious model response can never
corrupt the stored doc (parse_tips shapes or raises), and the coach never
runs without something to ground on being stated honestly in the prompt.

Runs under pytest OR directly: ``python tests/test_nonneg_coach.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("GVC_SESSION_SECRET", "nonneg-test-secret-0123456789abcdef")

try:  # same conditional stub as test_nonneg.py — CI keeps real WeasyPrint
    import weasyprint  # noqa: F401
except ImportError:
    import types
    _wp = types.ModuleType("weasyprint")
    _wp.HTML = object
    _wp.CSS = object
    sys.modules["weasyprint"] = _wp

from subsystems.nonneg import coach, tracker  # noqa: E402

D = date.fromisoformat
TODAY = D("2026-08-21")


def _doc_with_notes() -> dict:
    doc = tracker.blank_doc()
    doc["goals"][0] = "Grow GVC to $6M by Aug 2027."
    doc["goal_notes"][0] = "Danis bid pending."
    doc["days"] = {
        "2026-08-20": {"note": "Walked the KPMG job with Matt Salee, "
                               "ceiling grid issue made a great story."},
        "2026-08-19": {"note": "Gym at 5am. Read Buy Back Your Time ch 3."},
        "2026-08-01": {"note": "too old to include"},
    }
    return doc


# ---- prompt build ---------------------------------------------------------

def test_prompt_carries_notes_goals_and_stats():
    doc = _doc_with_notes()
    stats = tracker.compute_stats(doc, today=TODAY)
    p = coach.build_prompt(doc, stats, today=TODAY)
    assert "Matt Salee" in p and "Buy Back Your Time" in p
    assert "Grow GVC to $6M" in p and "Danis bid pending" in p
    assert "too old to include" not in p          # outside the 14-day window
    assert "NEVER invent a name" in p
    assert '"post_ideas"' in p and '"outreach"' in p


def test_prompt_carries_gvc_media_rules_for_public_posts():
    """Post ideas are PUBLIC — the 2026-07-28 admin-meeting media rules ride
    in the prompt: no street addresses, no customer/GC names in posts, no
    crew faces without consent, nothing unsafe. Outreach stays exempt
    (private messages may use names)."""
    doc = _doc_with_notes()
    p = coach.build_prompt(doc, tracker.compute_stats(doc, today=TODAY),
                           today=TODAY)
    assert "GVC MEDIA RULES" in p
    assert "city and state only, never a street address" in p
    assert "NEVER name a customer, builder, GC" in p
    assert "No crew members' faces" in p
    assert "unsafe work" in p
    assert "outreach is private, names" in p
    # The rules sit AFTER the notes, adjacent to the output schema, with a
    # mandatory final self-check — mid-prompt placement was blown past by the
    # live model (verified 2026-08-09).
    assert p.index("GVC MEDIA RULES") > p.index("DAILY NOTES")
    assert "FINAL CHECK" in p


def test_prompt_honest_when_empty():
    doc = tracker.blank_doc()
    stats = tracker.compute_stats(doc, today=TODAY)
    p = coach.build_prompt(doc, stats, today=TODAY)
    assert "not written yet" in p
    assert "no notes yet" in p


def test_prompt_lists_prior_tips_to_avoid_repeats():
    doc = _doc_with_notes()
    doc["coach"] = {"post_ideas": [{"idea": "grid story", "why": ""}],
                    "outreach": [{"who": "Matt", "why": "", "opener": ""}]}
    p = coach.build_prompt(doc, tracker.compute_stats(doc, today=TODAY),
                           today=TODAY)
    assert "grid story" in p and "Do not repeat" in p


def test_recent_notes_newest_first_within_window():
    notes = coach.recent_notes(_doc_with_notes(), today=TODAY)
    assert [d for d, _ in notes] == ["2026-08-20", "2026-08-19"]


# ---- response hardening ---------------------------------------------------

def test_parse_tips_shapes_valid_response():
    tips = coach.parse_tips({
        "focus": "Post the grid story.",
        "post_ideas": [{"idea": "Grid fix walkthrough", "why": "8/20 note"}],
        "outreach": [{"who": "Matt Salee", "why": "walked the job",
                      "opener": "Good walking KPMG with you."}],
        "_model": "claude-sonnet-4-6", "_source": "proxy",
    }, generated_at="2026-08-21T04:30:00", through="2026-08-20")
    assert tips["focus"].startswith("Post")
    assert tips["post_ideas"][0]["idea"] == "Grid fix walkthrough"
    assert tips["outreach"][0]["opener"].startswith("Good walking")
    assert tips["model"] == "claude-sonnet-4-6" and tips["source"] == "proxy"
    assert tips["through"] == "2026-08-20"


def test_parse_tips_rejects_garbage_and_caps():
    for bad in ({}, {"focus": ""}, {"post_ideas": ["not-a-dict"]}):
        try:
            coach.parse_tips(bad, generated_at="x", through="y")
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    many = {"focus": "f",
            "post_ideas": [{"idea": f"i{n}", "why": "w" * 900}
                           for n in range(20)]}
    tips = coach.parse_tips(many, generated_at="x", through="y")
    assert len(tips["post_ideas"]) == coach.MAX_IDEAS
    assert len(tips["post_ideas"][0]["why"]) == 500
    # Unknown keys from the model never pass through to the stored doc.
    evil = {"focus": "f", "days": {"2026-08-10": {}}, "goals": ["hax"]}
    tips = coach.parse_tips(evil, generated_at="x", through="y")
    assert set(tips) == {"generated_at", "through", "focus", "post_ideas",
                         "outreach", "model", "source"}


# ---- notes ----------------------------------------------------------------

def test_day_note_set_and_windowed():
    doc = coach.set_day_note(tracker.blank_doc(), "2026-08-10", "won a bid",
                             today=D("2026-08-10"))
    assert doc["days"]["2026-08-10"]["note"] == "won a bid"
    # Day-before warm-up note allowed; earlier and future are not.
    coach.set_day_note(tracker.blank_doc(), "2026-08-09", "prep",
                       today=D("2026-08-10"))
    for bad_day, today in (("2026-08-01", D("2026-08-10")),
                           ("2026-08-11", D("2026-08-10"))):
        try:
            coach.set_day_note(tracker.blank_doc(), bad_day, "x", today=today)
            raise AssertionError(f"accepted {bad_day}")
        except ValueError:
            pass


def test_day_note_preserves_ticks_and_caps_length():
    doc = tracker.toggle(tracker.blank_doc(), "2026-08-10", "sweat", True,
                         today=D("2026-08-10"))
    doc = coach.set_day_note(doc, "2026-08-10", "x" * 9000,
                             today=D("2026-08-10"))
    assert doc["days"]["2026-08-10"]["sweat"] is True
    assert len(doc["days"]["2026-08-10"]["note"]) == coach.NOTE_MAX


def test_refresh_interval_guard():
    import time
    now = time.time()
    from datetime import datetime
    recent = datetime.fromtimestamp(now - 10).isoformat()
    old = datetime.fromtimestamp(now - 4000).isoformat()
    assert coach.seconds_since(recent, now_ts=now) < coach.MIN_REFRESH_INTERVAL_S
    assert coach.seconds_since(old, now_ts=now) > coach.MIN_REFRESH_INTERVAL_S
    assert coach.seconds_since("garbage", now_ts=now) > 1e8


# ---- routes ---------------------------------------------------------------

def _client():
    from fastapi.testclient import TestClient
    from app import service
    return TestClient(service.app), service


def _cookie(email: str) -> dict:
    from shared import auth as portal_auth
    return {portal_auth.SESSION_COOKIE: portal_auth.make_session_cookie(email)}


def test_new_routes_register_and_are_owner_gated(monkeypatch):
    client, service = _client()
    paths = {getattr(r, "path", None) for r in service.app.routes}
    for p in ("/ui/api/nonneg/note", "/ui/api/nonneg/coach",
              "/v1/tasks/nonneg-coach"):
        assert p in paths, p
    from shared import access
    monkeypatch.setenv("GVC_PORTAL_ALLOWED_EMAILS", "owner-test@localhost")
    monkeypatch.setattr(access, "is_provisioned", lambda e: True)
    r = client.put("/ui/api/nonneg/note",
                   cookies=_cookie("andrea-test@localhost"),
                   json={"date": "2026-08-10", "note": "x"},
                   follow_redirects=False)
    assert r.status_code == 404
    r = client.post("/ui/api/nonneg/coach",
                    cookies=_cookie("andrea-test@localhost"),
                    follow_redirects=False)
    assert r.status_code == 404


def test_coach_route_unconfigured_is_honest_503(monkeypatch):
    client, _ = _client()
    from shared import access
    from adapters import llm
    from subsystems.nonneg import store
    monkeypatch.setenv("GVC_PORTAL_ALLOWED_EMAILS", "owner-test@localhost")
    monkeypatch.setattr(access, "is_provisioned", lambda e: True)
    monkeypatch.setattr(store, "read_doc", lambda obj: ({}, 0))
    monkeypatch.setenv("GVC_CLAUDE_PROXY_URL", "")   # proxy off, no key
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.transport() == ""
    r = client.post("/ui/api/nonneg/coach",
                    cookies=_cookie("owner-test@localhost"))
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "COACH_NOT_CONFIGURED"


def test_coach_route_stores_tips_from_fake_llm(monkeypatch):
    client, _ = _client()
    from shared import access
    from adapters import llm
    from subsystems.nonneg import store
    monkeypatch.setenv("GVC_PORTAL_ALLOWED_EMAILS", "owner-test@localhost")
    monkeypatch.setattr(access, "is_provisioned", lambda e: True)

    state = {"doc": {}}
    monkeypatch.setattr(store, "read_doc", lambda obj: (dict(state["doc"]), 1))

    def fake_mutate(obj, fn):
        new, result = fn(dict(state["doc"]))
        state["doc"] = new
        return result

    monkeypatch.setattr(store, "mutate", fake_mutate)
    monkeypatch.setattr(llm, "complete_json", lambda task, prompt, **kw: {
        "focus": "Post the grid story.",
        "post_ideas": [{"idea": "Grid fix", "why": "note"}],
        "outreach": [{"who": "the KPMG super", "why": "walked the job",
                      "opener": "Good seeing the job today."}],
        "_model": "fake", "_source": "test",
    })
    r = client.post("/ui/api/nonneg/coach",
                    cookies=_cookie("owner-test@localhost"))
    assert r.status_code == 200
    body = r.json()
    assert body["coach"]["focus"] == "Post the grid story."
    assert body["coach"]["outreach"][0]["who"] == "the KPMG super"
    assert state["doc"]["coach"]["post_ideas"][0]["idea"] == "Grid fix"


def test_scheduler_task_requires_api_key():
    client, _ = _client()
    r = client.post("/v1/tasks/nonneg-coach", json={"dry_run": True})
    assert r.status_code in (401, 403, 503)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
