"""Mobile baseline the whole portal must hold.

Field crews and supervisors are the primary users and they are on phones, so
these are correctness rules, not polish:

  1. Every page opts into safe-area insets (`viewport-fit=cover`), or notched
     phones clip sticky bars and bottom actions.
  2. No text input renders under 16px on a phone. iOS Safari auto-zooms the
     page whenever a focused input is smaller, which yanks the layout on every
     tap into a form.

Runs under pytest OR directly: ``python tests/test_mobile_baseline.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

PAGES = sorted(WEB.glob("*.html"))


def test_every_page_has_viewport_meta_with_safe_area() -> None:
    assert PAGES, "no portal pages found"
    for page in PAGES:
        html = page.read_text(encoding="utf-8")
        m = re.search(r'<meta name="viewport" content="([^"]+)"', html)
        assert m, f"{page.name} has no viewport meta"
        content = m.group(1)
        assert "width=device-width" in content, page.name
        assert "viewport-fit=cover" in content, (
            f"{page.name} is missing viewport-fit=cover — safe-area insets do "
            "nothing on notched phones without it"
        )


def _mobile_blocks(css: str) -> str:
    """Concatenated bodies of max-width phone media queries."""
    out = []
    for m in re.finditer(r"@media[^{]*max-width:\s*(\d+)px[^{]*\{", css):
        if int(m.group(1)) < 600:
            continue  # tablet/desktop-only tweak
        start = m.end()
        depth = 1
        i = start
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        out.append(css[start:i])
    return "\n".join(out)


def test_inputs_are_16px_on_phones() -> None:
    for name in ("gvc-ui.css", "gvc-forms.css"):
        css = (WEB / name).read_text(encoding="utf-8")
        if "input" not in css:
            continue
        mobile = _mobile_blocks(css)
        assert "font-size: 16px" in mobile, (
            f"{name} has no 16px input rule inside a phone media query — "
            "iOS will auto-zoom on focus"
        )


def test_tap_target_token_exists() -> None:
    css = (WEB / "gvc-ui.css").read_text(encoding="utf-8")
    m = re.search(r"--tap:\s*(\d+)px", css)
    assert m, "gvc-ui.css must define a --tap token"
    assert int(m.group(1)) >= 44, "tap targets must be at least 44px"


if __name__ == "__main__":
    test_every_page_has_viewport_meta_with_safe_area()
    test_inputs_are_16px_on_phones()
    test_tap_target_token_exists()
    print("ALL PASSED")
