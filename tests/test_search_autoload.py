"""Static checks: Find-the-X auto-loads a single clear hit (flow)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> str:
    html = (ROOT / "web" / name).read_text(encoding="utf-8")
    start = html.find("<script>")
    end = html.find("</script>", start)
    assert start >= 0 and end > start, name
    return html[start:end]


def test_invoice_autoload_single_parent():
    js = _script("invoice.html")
    assert "Matched — loading" in js
    assert "parents.length === 1" in js
    assert "/^CO\\.\\d+-/i" in js or r"/^CO\.\d+-/i" in js


def test_estimate_autoload_single_bid():
    js = _script("estimate.html")
    assert "Matched — loading" in js
    assert "rows.length === 1" in js
    assert "return loadBid(String(rows[0].item_id))" in js


def test_change_order_autoload_single_parent():
    js = _script("change-order.html")
    assert "Matched — loading" in js
    assert "parents.length === 1" in js
    assert "looksLikeProjectNumber" in js
    assert "loadForRevision(cosOnly[0].project_number)" in js
