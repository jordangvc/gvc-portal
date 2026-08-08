"""Priority portal pages share hub theme preference (no light FOUC)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

PRIORITY = (
    "estimate.html",
    "invoice.html",
    "change-order.html",
    "jobcheck.html",
    "jobstart.html",
    "morning.html",
    "billing.html",
    "check.html",
)


def test_priority_pages_wire_gvc_theme():
    for name in PRIORITY:
        html = (WEB / name).read_text(encoding="utf-8")
        assert 'data-theme="light"' not in html.split("<head", 1)[0], name
        assert "FOUC-safe theme boot" in html, name
        assert "/ui/gvc-theme.js" in html, name
        # Money forms inject #theme-toggle via GvcFormChrome; others mount inline.
        forms = 'data-forms="1"' in html and "/ui/gvc-form-chrome.js" in html
        if forms:
            assert "GvcFormChrome.mount" in html, name
        else:
            assert 'id="theme-toggle"' in html, name
            assert "GvcTheme.mount" in html, name


def test_hub_and_fieldguide_still_themed():
    for name in ("hub.html", "fieldguide.html"):
        html = (WEB / name).read_text(encoding="utf-8")
        assert "/ui/gvc-theme.js" in html or "gvc-theme" in html, name
