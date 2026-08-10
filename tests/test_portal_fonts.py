"""Portal font assets + /ui/fonts/{name} allowlist route.

Run: python tests/test_portal_fonts.py
  or: .venv/bin/pytest tests/test_portal_fonts.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FONTS = (
    "montserrat-600.woff2",
    "montserrat-700.woff2",
    "lato-400.woff2",
    "lato-700.woff2",
)


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_font_files_exist() -> None:
    fonts_dir = ROOT / "web" / "fonts"
    for name in FONTS:
        path = fonts_dir / name
        check(f"{name} exists", path.is_file())
        check(f"{name} non-empty", path.stat().st_size > 1000)


def test_gvc_css_references_font_urls() -> None:
    css = (ROOT / "web" / "gvc.css").read_text(encoding="utf-8")
    check("no base64 fonts left", "data:font/woff2;base64," not in css)
    for name in FONTS:
        check(f"css links /ui/fonts/{name}", f"/ui/fonts/{name}" in css)


def test_live_stylesheet_embeds_fonts_and_no_cdn() -> None:
    """Fonts live in gvc-ui.css — the stylesheet every page actually links.

    They were stranded in legacy gvc.css when r104 unlinked it: every page
    fell back to system fonts while the money forms pulled different faces
    from a fontshare CDN — the portal rendered different typefaces page to
    page, and CDN fonts block text on weak job-site signal (2026-08-09).
    """
    ui = (ROOT / "web" / "gvc-ui.css").read_text(encoding="utf-8")
    for name in FONTS:
        check(f"gvc-ui.css embeds {name}", f"/ui/fonts/{name}" in ui)
    check("display stack has embedded fallback", "'Montserrat'" in ui)
    check("body stack has embedded fallback", "'Lato'" in ui)
    for sheet in ("gvc-ui.css", "gvc-forms.css"):
        css = (ROOT / "web" / sheet).read_text(encoding="utf-8")
        check(f"{sheet} has no remote @import",
              "@import url('http" not in css and '@import url("http' not in css)


def test_font_route_allowlist() -> None:
    src = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    check("font route registered", '@app.get("/ui/fonts/{name}")' in src)
    check("allowlist frozenset", "_UI_FONT_ALLOWLIST" in src)
    for name in FONTS:
        check(f"allowlist includes {name}", f'"{name}"' in src)
    check("path traversal guard", "if name not in _UI_FONT_ALLOWLIST" in src)
    check("immutable cache header", "max-age=31536000, immutable" in src)


def main() -> int:
    tests = [
        test_font_files_exist,
        test_gvc_css_references_font_urls,
        test_font_route_allowlist,
    ]
    print("test_portal_fonts")
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"\n{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
