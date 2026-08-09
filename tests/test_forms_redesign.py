"""Money generators use the forms redesign pack (gvc-forms.css)."""
from __future__ import annotations

import re
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


def test_forms_template_tokens_are_substituted_by_their_route() -> None:
    """A {{TOKEN}} left unrendered inside the inline script kills the whole page.

    The money forms pass the signed-in email into GvcFormChrome.mount via
    {{EMAIL_JSON}}, so each page's route must replace it. These pages have no
    server-side render test otherwise.
    """
    service_src = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    for name in FORMS:
        html = (WEB / name).read_text(encoding="utf-8")
        for token in sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", html))):
            render = service_src.find(f'_cached_web_html("{name}")')
            assert render != -1, f"{name} is not served via _cached_web_html"
            block = service_src[render:render + 400]
            assert f'.replace("{token}"' in block, (
                f"{name} carries {token} but its route never substitutes it — "
                "the browser would receive the literal token"
            )


def test_forms_health_widget_refs_are_null_guarded() -> None:
    """The r103 chrome conversion removed the #health strip from money forms.

    checkHealth() survived it; an unguarded `$("#health").textContent` threw an
    uncaught TypeError on every /ui/invoice load (caught by the screenshot
    harness, 2026-08-09). If a page references #health without the element,
    every touch must be null-guarded like estimate/change-order already are.
    """
    for name in FORMS:
        html = (WEB / name).read_text(encoding="utf-8")
        if '"#health"' not in html:
            continue
        if 'id="health"' in html:
            continue  # element exists; direct writes are fine
        assert 'if (!healthEl) return' in html and "if (healthEl)" in html, (
            f"{name} references #health but the element is gone — guard every "
            "access (see estimate.html checkHealth for the pattern)"
        )
        assert '$("#health").innerHTML' not in html, name
        assert '$("#health").textContent' not in html, name


def test_forms_pack_owns_legacy_dialect_classes() -> None:
    """Classes the money forms still emit must be styled by the forms pack.

    The r103 conversion dropped legacy gvc.css (and its .gvc-moneyform scope)
    but the pages kept emitting `.draft-row` rows and an empty `#preview-frame`
    iframe — so draft/search lists rendered as bare unstyled text (nothing
    separating one job from the next) and the Live Document panel showed a
    default empty iframe box. Caught by Jordan on the live estimate page,
    2026-08-09.
    """
    css = (WEB / "gvc-forms.css").read_text(encoding="utf-8")
    assert ".gvc-results .draft-row" in css, "draft rows lost their styling"
    assert "border-bottom" in css.split(".gvc-results .draft-row", 1)[1][:400], (
        "draft rows need a separator between jobs"
    )
    assert ".gvc-doc iframe" in css and ".gvc-doc.show iframe" in css, (
        "preview iframe must be hidden until a preview exists"
    )
    for name in FORMS:
        html = (WEB / name).read_text(encoding="utf-8")
        assert "gvc-doc-placeholder" in html, (
            f"{name}: Live Document panel needs its empty-state placeholder"
        )
        assert 'classList.add("show")' in html, (
            f"{name}: preview JS no longer reveals the document panel"
        )


def test_forms_chrome_js_shared_topbar() -> None:
    js = (WEB / "gvc-form-chrome.js").read_text(encoding="utf-8")
    assert "gvc-topbar" in js and "gvc-path" in js
    assert "gvc-appnav" in js
    assert "GvcFlow.mount" in js
