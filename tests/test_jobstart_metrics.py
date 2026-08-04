"""
Job Start activity metrics — first-pass acceptance from synthetic events.
Runs under pytest OR directly: `python tests/test_jobstart_metrics.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.jobstart_metrics import first_pass_stats  # noqa: E402


def _ev(action: str, target: str, ts: str, **extra):
    return {
        "ts": ts,
        "action": action,
        "actor": extra.pop("actor", "user@gvc.test"),
        "target": target,
        "result": "ok",
        "extra": extra,
    }


def test_empty_events():
    out = first_pass_stats([])
    assert out["sent_count"] == 0
    assert out["accepted_count"] == 0
    assert out["first_pass_rate"] is None
    assert out["recent_send_back_notes"] == []


def test_first_pass_acceptance_no_send_back():
    events = [
        _ev("jobstart.sent_to_ops", "1001", "2026-07-01T10:00:00+00:00", job="Bid A"),
        _ev("jobstart.accepted", "1001", "2026-07-02T14:00:00+00:00", job="Bid A"),
    ]
    out = first_pass_stats(events)
    assert out["sent_count"] == 1
    assert out["accepted_count"] == 1
    assert out["sent_back_count"] == 0
    assert out["first_pass_accepted"] == 1
    assert out["first_pass_rate"] == 1.0
    assert out["avg_send_to_accept_hours"] == 28.0


def test_send_back_then_accept_not_first_pass():
    events = [
        _ev("jobstart.sent_to_ops", "2002", "2026-07-01T09:00:00+00:00"),
        _ev("jobstart.sent_back", "2002", "2026-07-01T15:00:00+00:00",
            actor="ops@gvc.test", note="Missing lock box"),
        _ev("jobstart.sent_to_ops", "2002", "2026-07-02T09:00:00+00:00"),
        _ev("jobstart.accepted", "2002", "2026-07-03T09:00:00+00:00"),
    ]
    out = first_pass_stats(events)
    assert out["sent_count"] == 2
    assert out["sent_back_count"] == 1
    assert out["accepted_count"] == 1
    assert out["first_pass_accepted"] == 0
    assert out["first_pass_rate"] == 0.0
    assert len(out["recent_send_back_notes"]) == 1
    assert out["recent_send_back_notes"][0]["note"] == "Missing lock box"
    assert out["recent_send_back_notes"][0]["bid"] == "2002"


def test_mixed_bids_first_pass_rate():
    events = [
        _ev("jobstart.sent_to_ops", "3001", "2026-07-01T08:00:00+00:00"),
        _ev("jobstart.accepted", "3001", "2026-07-01T18:00:00+00:00"),
        _ev("jobstart.sent_to_ops", "3002", "2026-07-01T08:00:00+00:00"),
        _ev("jobstart.sent_back", "3002", "2026-07-01T12:00:00+00:00", note="Scope gap"),
        _ev("jobstart.accepted", "3002", "2026-07-02T08:00:00+00:00"),
    ]
    out = first_pass_stats(events)
    assert out["accepted_count"] == 2
    assert out["first_pass_accepted"] == 1
    assert out["first_pass_rate"] == 0.5
    assert out["sent_back_count"] == 1


def test_ignores_non_jobstart_events():
    events = [
        {"ts": "2026-07-01T08:00:00+00:00", "action": "estimate.run", "target": "EST-1"},
        _ev("jobstart.sent_to_ops", "4001", "2026-07-01T09:00:00+00:00"),
    ]
    out = first_pass_stats(events)
    assert out["sent_count"] == 1
    assert out["accepted_count"] == 0


def test_recent_send_back_notes_sorted_newest_first():
    events = [
        _ev("jobstart.sent_back", "5001", "2026-07-01T10:00:00+00:00", note="older"),
        _ev("jobstart.sent_back", "5002", "2026-07-03T10:00:00+00:00", note="newer"),
    ]
    notes = first_pass_stats(events)["recent_send_back_notes"]
    assert [n["note"] for n in notes] == ["newer", "older"]


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
