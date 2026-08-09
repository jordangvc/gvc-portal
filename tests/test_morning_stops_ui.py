"""Morning Brief stops: route selection is explicit, completing is deliberate.

The old stop row had a BARE unlabeled checkbox that marked the stop COMPLETE
and posted an update to Monday — it read as "select this stop for Optimize",
which is exactly how Jordan read it (2026-08-09). These tests pin the fixed
contract:

  - a labelled "Route" checkbox controls inclusion in Optimize / Maps,
  - completing a stop is an explicit button, never a bare checkbox,
  - Optimize sends ONLY included stops and reattaches excluded ones so they
    stay on the day's list,
  - reorder buttons stay present.

Also pins the asset-cache rule this incident surfaced: portal CSS/JS must not
outlive the HTML that references them (a shipped CSS fix looked broken for up
to an hour while browsers held the old stylesheet).

Runs under pytest OR directly: ``python tests/test_morning_stops_ui.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MORNING = (ROOT / "web" / "morning.html").read_text(encoding="utf-8")


def test_route_inclusion_is_a_labelled_checkbox() -> None:
    assert 'data-act="include"' in MORNING
    assert 'aria-label="Include in route"' in MORNING
    assert "stop-inc" in MORNING, "the Route checkbox must carry its label"
    assert "function stopIncluded" in MORNING
    assert "s.completed) return false" in MORNING, (
        "completed stops must never be routed"
    )


def test_completing_is_an_explicit_button_not_a_bare_checkbox() -> None:
    assert 'data-act="done"' in MORNING
    assert re.search(r'<button[^>]*data-act="done"', MORNING), (
        "Done must be a button"
    )
    assert not re.search(r'<input[^>]*data-act="done"', MORNING), (
        "a checkbox that completes a stop reads as selection — never again"
    )


def test_optimize_sends_only_included_and_keeps_excluded() -> None:
    handler = MORNING.split('$("#btnOptimize")', 1)[1][:2500]
    assert "filter(stopIncluded)" in handler
    assert "JSON.stringify({ stops: included })" in handler
    assert ".concat(excluded)" in handler, (
        "excluded stops must stay on the day's list after Optimize"
    )
    assert "No stops are checked" in handler, "empty selection needs a message"


def test_reorder_buttons_present() -> None:
    assert 'data-act="up"' in MORNING and 'data-act="down"' in MORNING


def test_no_portal_asset_cached_longer_than_html() -> None:
    service = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    assert "max-age=3600" not in service, (
        "portal CSS/JS must cache <= the HTML (300s) — an hour-old stylesheet "
        "made a shipped fix look broken (2026-08-09)"
    )


if __name__ == "__main__":
    test_route_inclusion_is_a_labelled_checkbox()
    test_completing_is_an_explicit_button_not_a_bare_checkbox()
    test_optimize_sends_only_included_and_keeps_excluded()
    test_reorder_buttons_present()
    test_no_portal_asset_cached_longer_than_html()
    print("ALL PASSED")
