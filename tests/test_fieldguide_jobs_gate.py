"""Pure tests for Field Manual jobs gate + theme asset presence.

Run: python tests/test_fieldguide_jobs_gate.py
     or pytest tests/test_fieldguide_jobs_gate.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _service_source() -> str:
    return (ROOT / "app" / "service.py").read_text(encoding="utf-8")


def test_fieldguide_jobs_route_requires_jobcheck():
    """Baseline fieldguide must not enumerate Monday jobs; jobcheck does."""
    src = _service_source()
    # Find the jobs handler body between its def and the next top-level @app
    start = src.find("def ui_fieldguide_jobs")
    assert start > 0, "ui_fieldguide_jobs missing"
    chunk = src[start : start + 800]
    assert 'require_feature(request, "jobcheck")' in chunk
    assert 'require_feature(request, "fieldguide")' not in chunk


def test_theme_js_route_and_file_exist():
    assert (ROOT / "web" / "gvc-theme.js").is_file()
    src = _service_source()
    assert '@app.get("/ui/gvc-theme.js")' in src
    assert "gvc-theme.js" in src


def test_gvc_theme_js_parses():
    text = (ROOT / "web" / "gvc-theme.js").read_text(encoding="utf-8")
    assert "gvc-theme" in text
    assert "prefers-color-scheme" in text
    assert "GvcTheme" in text


def test_hub_and_fieldguide_boot_theme():
    hub = (ROOT / "web" / "hub.html").read_text(encoding="utf-8")
    fg = (ROOT / "web" / "fieldguide.html").read_text(encoding="utf-8")
    assert "gvc-theme" in hub and "/ui/gvc-theme.js" in hub
    assert "gvc-theme" in fg and "/ui/gvc-theme.js" in fg
    assert 'data-theme="light"' not in hub.split("<head>", 1)[0]  # not hard-locked on <html>
    assert "job-manual" in fg
    assert "Job Check" in fg  # 403 copy


def main() -> int:
    tests = [
        test_fieldguide_jobs_route_requires_jobcheck,
        test_theme_js_route_and_file_exist,
        test_gvc_theme_js_parses,
        test_hub_and_fieldguide_boot_theme,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f" FAIL {fn.__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
