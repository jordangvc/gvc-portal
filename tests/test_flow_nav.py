"""Money-spine Path nav + Takeoff launcher wiring.

Run: .venv/bin/pytest tests/test_flow_nav.py -q
  or: python tests/test_flow_nav.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_flow_nav_spine() -> None:
    from shared import flow_nav, hub_nav

    ids = flow_nav.spine_ids()
    check("spine starts hub", ids[0] == "hub")
    check("spine has takeoff", "takeoff" in ids)
    check("spine ends invoice", ids[-1] == "invoice")
    check("seven steps", len(ids) == 7)
    check("billing before invoice", ids.index("billing") < ids.index("invoice"))

    steps = flow_nav.spine_steps()
    takeoff = next(s for s in steps if s["id"] == "takeoff")
    check("takeoff href portal", takeoff["href"] == "/ui/takeoff")

    bare = flow_nav.takeoff_app_url()
    check("bare netlify", bare.startswith("https://gvctakeoff.netlify.app/"))
    with_ret = flow_nav.takeoff_app_url(portal_origin="https://portal.example")
    check("return param", "return=" in with_ret and "portal.example" in with_ret)
    check("from=portal", "from=portal" in with_ret)

    tools = hub_nav.tools_for_client({"takeoff", "estimate", "jobstart", "jobcheck", "invoice"})
    tk = next(t for t in tools if t["feature"] == "takeoff")
    check("hub takeoff internal", tk["href"] == "/ui/takeoff")
    check("hub takeoff not external", tk["external"] is False)
    check("home href takeoff", hub_nav.home_tool_href("takeoff") == "/ui/takeoff")


def test_takeoff_page_and_flow_js_on_disk() -> None:
    web = ROOT / "web"
    check("takeoff.html exists", (web / "takeoff.html").is_file())
    check("gvc-flow.js exists", (web / "gvc-flow.js").is_file())
    html = (web / "takeoff.html").read_text(encoding="utf-8")
    check("brand back to hub", 'href="/"' in html and "gvc-brand" in html)
    check("path mount", 'GvcFlow.mount' in html and '"takeoff"' in html)
    check("import estimate link", "/ui/estimate?takeoff=1" in html)
    check("open takeoff cta", "open-takeoff" in html)

    flow_js = (web / "gvc-flow.js").read_text(encoding="utf-8")
    check("flow exports mount", "mount:" in flow_js or "function mount" in flow_js)
    for page, step in (
        ("estimate.html", "estimate"),
        ("jobstart.html", "jobstart"),
        ("jobcheck.html", "jobcheck"),
        ("billing.html", "billing"),
        ("invoice.html", "invoice"),
    ):
        body = (web / page).read_text(encoding="utf-8")
        check(f"{page} has path host", 'id="gvc-flow"' in body)
        check(f"{page} mounts {step}", f'"{step}"' in body and "GvcFlow.mount" in body)


def test_takeoff_route_registers() -> None:
    import os

    os.environ.setdefault("GVC_UI_DEV_BYPASS", "1")
    os.environ.setdefault("GVC_PORTAL_ALLOWED_EMAILS", "dev-bypass@localhost")
    from app.service import app

    paths = {getattr(r, "path", None) for r in app.routes}
    check("/ui/takeoff route", "/ui/takeoff" in paths)
    check("/ui/gvc-flow.js route", "/ui/gvc-flow.js" in paths)


def test_jobstart_boot_warms_and_parallels_list() -> None:
    """Deep-link ?bid= must not block Change-bid list warm (Job Check parity)."""
    body = (ROOT / "web" / "jobstart.html").read_text(encoding="utf-8")
    check("jobstart warms monday", '/ui/api/monday/warm' in body)
    check("jobstart boot loads bids always", "loadBids()" in body)
    check("deep bid keepStatus", "keepStatus" in body and "bootJobStartFromUrl" in body)
    check("lite first paint", "?lite=1" in body and "hydrateDriveSources" in body)
    check("drive_pending banner", "drive_pending" in body)
    for page in ("billing.html", "estimate.html"):
        html = (ROOT / "web" / page).read_text(encoding="utf-8")
        check(f"{page} warms monday", '/ui/api/monday/warm' in html)


def test_estimate_finalize_deeplinks_jobstart_bid() -> None:
    body = (ROOT / "web" / "estimate.html").read_text(encoding="utf-8")
    check(
        "finalize next uses jobstart?bid=",
        "/ui/jobstart?bid=" in body and "monday_item_id" in body,
    )


if __name__ == "__main__":
    test_flow_nav_spine()
    test_takeoff_page_and_flow_js_on_disk()
    test_takeoff_route_registers()
    test_jobstart_boot_warms_and_parallels_list()
    test_estimate_finalize_deeplinks_jobstart_bid()
    print("all ok")
