"""Job Check Hub deep-link (?item=) — static contract checks.

Runs under pytest OR: `python tests/test_jobcheck_deeplink.py`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JOBCHECK = (ROOT / "web" / "jobcheck.html").read_text(encoding="utf-8")
SERVICE = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
HUB_FLOW = (ROOT / "orchestrators" / "hub_flow.py").read_text(encoding="utf-8")


def test_hub_emits_item_query():
    assert "/ui/jobcheck?item=" in HUB_FLOW


def test_jobcheck_boots_item_param():
    assert "bootJobFromUrl" in JOBCHECK
    assert 'params.get("item")' in JOBCHECK
    assert "parseMondayItemId" in JOBCHECK
    # Strip the query only AFTER a successful load so a failed open can retry.
    assert "history.replaceState" in JOBCHECK
    assert "loadJob(id)" in JOBCHECK or "loadJob(id)" in JOBCHECK


def test_jobcheck_deeplink_does_not_wait_on_jobs_list():
    """Hub deep-links must open /job/{id} without awaiting /jobs first."""
    scripts = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", JOBCHECK, re.S)
    main = next((s for s in scripts if "bootJobFromUrl" in s), "")
    assert main
    # boot is no longer nested inside loadJobs.
    load_jobs_fn = re.search(
        r"async function loadJobs\(\)\s*\{(.*?)\n\}", main, re.S)
    assert load_jobs_fn, "loadJobs missing"
    assert "bootJobFromUrl" not in load_jobs_fn.group(1)
    # Boot runs first; list is fire-and-forget afterward.
    assert "await bootJobFromUrl()" in main
    assert re.search(r"bootJobFromUrl\(\);\s*\n\s*loadJobs\(\)", main) or (
        "loadJobs();" in main and main.index("await bootJobFromUrl()") < main.index("loadJobs()")
    )


def test_jobcheck_saveflash_no_yank_on_ok():
    assert 'id="saveflash"' in JOBCHECK
    assert 'if (kind === "ok") return' in JOBCHECK


def test_jobcheck_warms_monday():
    assert "/ui/api/monday/warm" in JOBCHECK


def test_html_pages_use_private_cache_helper():
    assert "_cached_web_html" in SERVICE
    assert "_PRIVATE_HTML_CACHE_HEADERS" in SERVICE
    assert "private, max-age=300" in SERVICE
    for name in ("estimate.html", "invoice.html", "change-order.html", "billing.html"):
        assert f'_cached_web_html("{name}")' in SERVICE


def test_jobcheck_js_parses():
    scripts = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", JOBCHECK, re.S)
    assert scripts, "expected inline script"
    main = next((s for s in scripts if "bootJobFromUrl" in s), "")
    assert main, "expected inline script with bootJobFromUrl"
    assert "await bootJobFromUrl()" in main
    assert "return true" in main  # loadJob success signal for replaceState


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    raise SystemExit(failed)
