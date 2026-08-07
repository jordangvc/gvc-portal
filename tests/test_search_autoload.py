"""Static checks: Find-the-X auto-loads a single clear hit (flow)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _page_js(name: str) -> str:
    """Return concatenated inline <script> bodies (theme + page app)."""
    html = (ROOT / "web" / name).read_text(encoding="utf-8")
    chunks: list[str] = []
    pos = 0
    while True:
        start = html.find("<script>", pos)
        if start < 0:
            break
        end = html.find("</script>", start)
        assert end > start, name
        chunks.append(html[start:end])
        pos = end + len("</script>")
    assert chunks, name
    return "\n".join(chunks)


def test_invoice_autoload_single_parent():
    js = _page_js("invoice.html")
    assert "Matched — loading" in js
    assert "parents.length === 1" in js
    assert r"/^CO\.\d+-/i" in js


def test_estimate_autoload_single_bid():
    js = _page_js("estimate.html")
    assert "Matched — loading" in js
    assert "rows.length === 1" in js
    assert "return loadBid(String(rows[0].item_id))" in js


def test_change_order_autoload_single_parent():
    js = _page_js("change-order.html")
    assert "Matched — loading" in js
    assert "parents.length === 1" in js
    assert "looksLikeProjectNumber" in js
    assert "loadForRevision(cosOnly[0].project_number)" in js
