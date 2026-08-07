"""Invoice lookup bundles billable COs + UI skips the second fetch."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _invoice_page_js() -> str:
    html = (ROOT / "web" / "invoice.html").read_text(encoding="utf-8")
    blocks = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.S | re.I)
    return "\n".join(blocks)


def test_apply_lookup_prefers_bundled_change_orders():
    js = _invoice_page_js()
    assert "Array.isArray(body.change_orders)" in js
    assert "renderBillableCos(body.change_orders)" in js
    # Fallback to the dedicated endpoint when the bundle is absent.
    assert "loadBillableCos(basePn)" in js
    assert 'fetch("/ui/api/invoice/billable-cos?project_number="' in js


def test_placeholders_teach_spine_not_legacy_series():
    html = (ROOT / "web" / "invoice.html").read_text(encoding="utf-8")
    assert "PRO-2026" in html
    assert 'placeholder="C-005 or MV-001"' not in html


def test_lookup_route_returns_change_orders_key():
    """Source contract: ui_invoice_lookup returns change_orders alongside prefill."""
    src = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    assert 'return {"ok": True, "prefill": prefill, "change_orders": change_orders}' in src
    assert "list_unbilled_co_items" in src
    assert "ThreadPoolExecutor(max_workers=2)" in src
    assert "C-005" not in src.split("def ui_invoice_lookup")[1].split("def ui_invoice_search")[0]


def _run_all() -> bool:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
