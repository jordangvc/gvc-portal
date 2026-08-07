"""CO Find-the-Project: Project # short-circuit + cache contract."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _page_js(name: str) -> str:
    html = (ROOT / "web" / name).read_text(encoding="utf-8")
    blocks = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.S | re.I)
    return "\n".join(blocks)


def test_co_lookup_from_project_number_short_circuits_search():
    js = _page_js("change-order.html")
    assert "if (looksLikeProjectNumber(q)) return loadProjectByNumber(q)" in js
    assert "loadProjectByNumber" in js
    assert 'fetch("/ui/api/change-order/lookup?project_number="' in js
    assert "applyProjectLookup" in js


def test_co_lookup_route_accepts_project_number():
    src = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    # Narrow to the CO lookup handler.
    chunk = src.split("def ui_change_order_lookup")[1].split("def ui_change_order_search")[0]
    assert "project_number: str = \"\"" in chunk or "project_number: str =" in chunk
    assert "find_project_by_number" in chunk


def _run_all() -> bool:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
