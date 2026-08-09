"""Inventory admin console — static page contract, JS parse, labels template.

Style of tests/test_forms_redesign.py (static assertions against the shipped
HTML) plus the env+stub header pattern from tests/test_inventory_api.py for
the route-registration check. Runs under pytest OR directly:
``python tests/test_inventory_admin_ui.py``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# --- environment BEFORE app import (test_inventory_api.py pattern) ----------
os.environ["GVC_SESSION_SECRET"] = "t" * 64
os.environ["GVC_GRANTS_BACKEND"] = "env"
os.environ.pop("GVC_UI_DEV_BYPASS", None)
os.environ.pop("GVC_PORTAL_ALLOWED_EMAILS", None)

# WeasyPrint stub (native libs absent on dev boxes; PDF paths untested here).
_wp = types.ModuleType("weasyprint")
_wp.HTML = object
_wp.CSS = object
sys.modules.setdefault("weasyprint", _wp)

WEB = ROOT / "web"
PAGE = WEB / "inventory-admin.html"
TEMPLATES = ROOT / "templates"

IMPORT_HEADER = "name,tracking,base_unit,category,aliases,qty,location,serial"

SECTION_ANCHORS = ("items", "assets", "locations", "counts", "attention",
                   "import", "reports", "labels")


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


# ------------------------------------------------------------------ static

def test_page_links_redesign_stylesheet_only() -> None:
    html = _html()
    assert 'href="/ui/gvc-ui.css"' in html
    assert 'href="/ui/gvc.css"' not in html, "legacy gvc.css must not be linked"


def test_page_mobile_and_theme_contract() -> None:
    html = _html()
    assert "viewport-fit=cover" in html, "safe-area CSS is inert without it"
    assert 'data-palette="emerald"' in html
    # FOUC-safe theme boot must run in <head>, before the stylesheet paints.
    head = html.partition("</head>")[0]
    assert "gvc-theme" in head and "data-theme" in head


def test_no_select_elements() -> None:
    html = _html().lower()
    markup, _, _ = html.partition("<script")
    assert "<select" not in markup, "chips/searchable lists replace <select>"
    # Stronger: the page never emits <select> anywhere, scripts included.
    assert "<select" not in html


def test_email_json_token_present() -> None:
    assert "{{EMAIL_JSON}}" in _html()


def test_sections_present_as_anchors_or_nav() -> None:
    html = _html()
    for anchor in SECTION_ANCHORS:
        assert f'href="#{anchor}"' in html, f"missing nav entry for {anchor}"
        assert f'id="sec-{anchor}"' in html, f"missing section for {anchor}"


def test_import_template_header_present() -> None:
    assert IMPORT_HEADER in _html()


def test_error_envelope_rendering_helper_present() -> None:
    html = _html()
    assert "advice" in html, "errors must render the {code, detail, advice} envelope"
    assert "errHtml" in html


def test_email_tokens_are_substituted_by_the_route() -> None:
    """An unrendered {{TOKEN}} in JS position kills the whole inline script."""
    service_src = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    html = _html()
    render = service_src.find('_cached_web_html("inventory-admin.html")')
    assert render != -1, "page is not served via _cached_web_html"
    block = service_src[render:render + 400]
    for token in sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", html))):
        assert f'.replace("{token}"' in block, (
            f"page carries {token} but its route never substitutes it"
        )


# ---------------------------------------------------------------- JS parse

def _inline_script_bodies(html: str) -> list[str]:
    return [
        m.group(1)
        for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                             html, re.S | re.I)
        if m.group(1).strip()
    ]


def test_inline_js_parses_after_token_substitution() -> None:
    node = shutil.which("node")
    assert node, "node is required to parse-check the inline JS"
    bodies = _inline_script_bodies(_html())
    assert bodies, "expected at least one inline <script>"
    for i, src in enumerate(bodies):
        substituted = re.sub(r"\{\{[A-Z0-9_]+\}\}", '"x@y"', src)
        # Windows encoding trap: write a UTF-8 temp FILE for node --check;
        # never pipe source over stdin.
        fd, path = tempfile.mkstemp(suffix=f"-inv-admin-{i}.js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(substituted)
            proc = subprocess.run(
                [node, "--check", path],
                capture_output=True, text=True, timeout=60,
            )
            assert proc.returncode == 0, (
                f"inline script #{i} failed node --check:\n{proc.stderr}"
            )
        finally:
            os.unlink(path)


# ----------------------------------------------------------- labels sheet

def test_labels_template_renders_35_labels_with_page_breaks() -> None:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    labels = [
        {"code": f"LBQ{i:03d}", "title": f"Rack {i} — mud & tape",
         "sub": f"Main Shop / Rack {i}", "kind": "LOCATION", "qr_b64": "QUJDRA=="}
        for i in range(35)
    ]
    out = env.get_template("inventory_labels.html.j2").render(
        labels=labels, company="Green Valley Contractors")
    assert out.count("data:image/png;base64,") == 35
    assert "Green Valley Contractors" in out
    # 35 labels must break onto a second sheet: page-break-capable structure.
    assert "page-break" in out or "break-after" in out
    assert out.count('class="sheet"') == 2
    # Every Jinja construct rendered — no raw template syntax left behind.
    assert "{{" not in out and "{%" not in out


def test_labels_template_escapes_content() -> None:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    out = env.get_template("inventory_labels.html.j2").render(
        labels=[{"code": "X<1>", "title": "<b>bad</b>", "sub": "",
                 "kind": "ITEM", "qr_b64": "QQ=="}],
        company="GVC & Co")
    assert "<b>bad</b>" not in out
    assert "&lt;b&gt;bad&lt;/b&gt;" in out


# ------------------------------------------------------- route registration

def test_admin_page_route_registered() -> None:
    from app.service import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/ui/inventory/admin" in paths


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failed else 0)
