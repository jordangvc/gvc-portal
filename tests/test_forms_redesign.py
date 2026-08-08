"""Money generators use the forms redesign pack (gvc-forms.css)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

FORMS = ("estimate.html", "change-order.html", "invoice.html")


def test_forms_pack_files_and_routes() -> None:
    assert (WEB / "gvc-forms.css").is_file()
    assert (WEB / "gvc-form-chrome.js").is_file()
    assert (WEB / "gvc-form-stages.js").is_file()
    from app.service import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/ui/gvc-forms.css" in paths
    assert "/ui/gvc-form-chrome.js" in paths
    assert "/ui/gvc-form-stages.js" in paths


def test_forms_pages_match_pack_contract() -> None:
    for name in FORMS:
        html = (WEB / name).read_text(encoding="utf-8")
        markup, _, _ = html.lower().partition("<script")
        assert 'data-forms="1"' in html, name
        assert 'href="/ui/gvc-ui.css"' in html, name
        assert 'href="/ui/gvc-forms.css"' in html, name
        assert 'href="/ui/gvc.css"' not in html, name
        assert "<select" not in markup, name
        assert "gvc-form-chrome.js" in html and "GvcFormChrome.mount" in html, name
        assert "gvc-form-stages.js" in html and "GvcFormStages.mount" in html, name
        assert 'id="gvc-form-chrome"' in html, name
        assert "gvc-actionbar" in html and 'id="btn-next"' in html, name
        assert 'id="stages"' in html, name
        assert "sec-help" not in html, name


def test_forms_chrome_js_shared_topbar() -> None:
    js = (WEB / "gvc-form-chrome.js").read_text(encoding="utf-8")
    assert "gvc-topbar" in js and "gvc-path" in js
    assert "gvc-appnav" in js
    assert "GvcFlow.mount" in js
