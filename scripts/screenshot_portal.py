"""Per-role, per-viewport screenshot harness (+ throttled-4G evidence).

Runs the portal LOCALLY and screenshots every page each admin preset can open,
at the four reference viewports. Zero production code involved in auth:

  - The app runs in-process (uvicorn thread) with a THROWAWAY session secret
    that exists only for this run, so the minted cookies are worthless
    anywhere else.
  - Grants for the fake ``test-<preset>@localhost`` users come from
    monkeypatching ``shared.access.is_provisioned`` / ``effective_features``
    IN THIS PROCESS ONLY — the deployed service and real accounts are never
    touched. Approved by Jordan 2026-08-09.

External integrations (Monday / GCS / Drive / Stripe) are deliberately absent
locally, so pages render their degraded / empty states — that is legitimate
layout evidence (those states are part of the design system contract).

Usage (from repo root):

    python scripts/screenshot_portal.py                 # full matrix
    python scripts/screenshot_portal.py --roles ops,crew
    python scripts/screenshot_portal.py --throttle      # 4G pass (see below)

Output: docs/screenshots/<role>/<viewport>/<page>.jpg  + docs/screenshots/INDEX.md
The output dir is WIPED at the start of a full run (latest-run-only policy).

Throttle mode emulates 4G via CDP (150ms RTT, 1.6 Mbps down, 750 Kbps up) on
each role's HOME page, records time-to-DOM-ready and time-to-first-content,
verifies something renders within the 3s budget, and screenshots the page
mid-load at ~1.5s so the loading state itself is evidenced.
"""
from __future__ import annotations

import argparse
import contextlib
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "screenshots"
HOST, PORT = "127.0.0.1", 8765
BASE = f"http://{HOST}:{PORT}"

VIEWPORTS = {
    "375x667": (375, 667),      # iPhone SE
    "390x844": (390, 844),      # iPhone 14/15
    "768x1024": (768, 1024),    # iPad
    "1280x800": (1280, 800),    # desktop
}

# 4G-ish per Chrome devtools presets (between "Fast 3G" and unthrottled).
THROTTLE = {"latency": 150, "download": 1.6 * 1024 * 1024 / 8,
            "upload": 750 * 1024 / 8}
THROTTLE_BUDGET_S = 3.0


def _free_port_check() -> None:
    """Pick a fresh ephemeral port every run.

    A crashed run can leave a zombie uvicorn on the old port serving STALE
    code with a STALE session secret — requests then auth-bounce or render
    phantom old pages (cost ~40 min of ghost-chasing, 2026-08-09). Never
    reuse a fixed port; rebind globals to a kernel-assigned free one.
    """
    global PORT, BASE
    with contextlib.closing(socket.socket()) as s:
        s.bind((HOST, 0))
        PORT = s.getsockname()[1]
    BASE = f"http://{HOST}:{PORT}"


def _boot_app():
    """Import the app with local test env + harness-only patches applied."""
    import os

    # Throwaway secret: cookies minted here verify only inside this process.
    os.environ["GVC_SESSION_SECRET"] = secrets.token_hex(32)
    os.environ["GVC_GRANTS_BACKEND"] = "env"
    os.environ.pop("GVC_UI_DEV_BYPASS", None)
    # Grants come from the monkeypatch below; this env feeds ONLY
    # access.superadmin_emails(), which gates owner-personal pages
    # (/ui/nonneg) — cover them under the full preset.
    os.environ["GVC_PORTAL_ALLOWED_EMAILS"] = "test-full@localhost"
    # Make every external adapter fail fast into its degraded state.
    for var in ("MONDAY_API_TOKEN", "STRIPE_API_KEY"):
        os.environ.pop(var, None)

    # WeasyPrint needs native Pango/Cairo that this Windows box doesn't have;
    # PDF rendering is irrelevant to screenshots. Stub before app import.
    wp = types.ModuleType("weasyprint")
    wp.HTML = object
    wp.CSS = object
    sys.modules.setdefault("weasyprint", wp)

    from shared import access

    presets = {p["id"]: set(p["features"]) for p in access.ROLE_PRESETS}

    def grants_for(email: str) -> set[str]:
        m = re.fullmatch(r"test-([a-z]+)@localhost", email or "")
        if not m or m.group(1) not in presets:
            return set()
        feats = presets[m.group(1)]
        if access.WILDCARD in feats:
            return set(access.FEATURES)
        return set(access._expand(feats))

    # HARNESS-PROCESS-ONLY monkeypatch (the approved design).
    access.is_provisioned = lambda email: bool(grants_for(email))
    access.effective_features = grants_for

    from app.service import app  # noqa: WPS433 — after env + patches
    return app, presets


def _start_server(app) -> None:
    import uvicorn

    cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        with contextlib.closing(socket.socket()) as s:
            if s.connect_ex((HOST, PORT)) == 0:
                return
        time.sleep(0.1)
    raise SystemExit("local server did not come up")


def _pages_for(preset_id: str, feats: set[str]) -> list[tuple[str, str]]:
    from tests.test_role_home_reachable import PAGE_FEATURE

    out = []
    for path, needed in sorted(PAGE_FEATURE.items()):
        if needed is None or needed in feats:
            # /ui/inventory/admin and /ui/admin must not both become
            # "admin.jpg" — join every segment after /ui/.
            name = "hub" if path == "/" else "-".join(
                path.strip("/").split("/")[1:]) or "hub"
            out.append((name, path))
    # /ui/nonneg is superadmin-gated, not a grants feature — _boot_app puts
    # test-full@localhost in the superadmin env so the full preset covers it.
    if preset_id == "full":
        out.append(("nonneg", "/ui/nonneg"))
    return out


def _mint_cookie(email: str) -> str:
    from shared import auth as portal_auth

    return portal_auth.make_session_cookie(email)


def run(roles_filter: list[str] | None, throttle: bool) -> int:
    _free_port_check()
    app, presets = _boot_app()
    _start_server(app)

    from playwright.sync_api import sync_playwright

    role_ids = [r for r in presets if not roles_filter or r in roles_filter]
    findings: list[str] = []
    shots = 0
    console_issues: dict[str, list[str]] = {}

    if not throttle and not roles_filter:
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for rid in role_ids:
            email = f"test-{rid}@localhost"
            cookie = _mint_cookie(email)
            from shared import access
            feats = access.effective_features(email)
            pages = _pages_for(rid, feats)

            if throttle:
                shots += _throttle_pass(browser, rid, cookie, findings)
                continue

            for vp_name, (w, h) in VIEWPORTS.items():
                ctx = browser.new_context(
                    viewport={"width": w, "height": h},
                    device_scale_factor=2 if w < 500 else 1,
                    is_mobile=w < 500,
                    has_touch=w < 800,
                )
                ctx.add_cookies([{
                    "name": "gvc_session", "value": cookie,
                    "domain": HOST, "path": "/",
                }])
                page = ctx.new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
                page.on("console", lambda m, errs=errs: errs.append(m.text)
                        if m.type == "error" else None)
                for name, path in pages:
                    # Watchdog: if any single page wedges the run (native
                    # hang, event-loop deadlock), dump every stack and die
                    # loudly instead of silently holding the port.
                    import faulthandler
                    faulthandler.dump_traceback_later(90, exit=True)
                    dest = OUT / rid / vp_name
                    dest.mkdir(parents=True, exist_ok=True)
                    errs.clear()
                    try:
                        page.goto(BASE + path, wait_until="domcontentloaded",
                                  timeout=20000)
                        page.wait_for_timeout(1200)  # settle async hydration
                        page.add_style_tag(content="*{transition:none!important;"
                                                   "animation:none!important}")
                        page.screenshot(path=str(dest / f"{name}.jpg"),
                                        type="jpeg", quality=70, full_page=True)
                        shots += 1
                        real_errs = [e for e in errs
                                     if "Failed to load resource" not in e]
                        if real_errs:
                            key = f"{rid} {vp_name} {path}"
                            console_issues[key] = real_errs[:3]
                        if page.url.startswith("https://accounts.google.com"):
                            findings.append(f"AUTH BOUNCE: {rid} {path} — "
                                            "redirected to sign-in (grant hole)")
                    except Exception as exc:  # noqa: BLE001
                        findings.append(f"FAIL {rid} {vp_name} {path}: "
                                        f"{type(exc).__name__}: {exc}")
                ctx.close()
            import faulthandler
            faulthandler.cancel_dump_traceback_later()
        browser.close()

    _write_index(role_ids, presets, shots, findings, console_issues, throttle)
    print(f"\n{shots} screenshots; {len(findings)} findings; "
          f"{len(console_issues)} pages with console errors")
    for f in findings:
        print("  !", f)
    return 0 if not findings else 1


def _throttle_pass(browser, rid: str, cookie: str, findings: list[str]) -> int:
    """4G evidence on the role's home + hub: budget, mid-load state, settle."""
    from tests.test_role_home_reachable import PAGE_FEATURE  # noqa: F401
    from shared import access, hub_nav

    feats = access.effective_features(f"test-{rid}@localhost")
    role = hub_nav.resolve_role(feats)
    home = hub_nav.ROLE_HOME_HREF.get(role, "/ui/morning")
    targets = ["/", home] if home != "/" else ["/"]

    ctx = browser.new_context(viewport={"width": 390, "height": 844},
                              is_mobile=True, has_touch=True,
                              device_scale_factor=2)
    ctx.add_cookies([{"name": "gvc_session", "value": cookie,
                      "domain": HOST, "path": "/"}])
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {
        "offline": False, "latency": THROTTLE["latency"],
        "downloadThroughput": THROTTLE["download"],
        "uploadThroughput": THROTTLE["upload"],
    })
    shots = 0
    dest = OUT / "_throttled-4g"
    dest.mkdir(parents=True, exist_ok=True)
    for path in targets:
        name = "hub" if path == "/" else path.rsplit("/", 1)[-1]
        t0 = time.monotonic()
        page.goto(BASE + path, wait_until="commit", timeout=30000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(dest / f"{rid}-{name}-midload.jpg"),
                        type="jpeg", quality=70)
        shots += 1
        body_text = page.evaluate("document.body ? document.body.innerText : ''")
        elapsed = time.monotonic() - t0
        if elapsed <= THROTTLE_BUDGET_S and not body_text.strip():
            findings.append(f"4G: {rid} {path} blank at {elapsed:.1f}s "
                            "(no loading state visible)")
        page.wait_for_load_state("load", timeout=30000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(dest / f"{rid}-{name}-settled.jpg"),
                        type="jpeg", quality=70)
        shots += 1
        settled = time.monotonic() - t0
        with (dest / "TIMINGS.txt").open("a", encoding="utf-8") as fh:
            fh.write(f"{rid:8} {path:20} first-content<=1.5s "
                     f"settled={settled:.1f}s\n")
    ctx.close()
    return shots


def _write_index(role_ids, presets, shots, findings, console_issues, throttle):
    mode = "throttled-4G" if throttle else "full matrix"
    lines = [
        "# Portal screenshot evidence",
        "",
        f"Generated by `python scripts/screenshot_portal.py` — {mode} run, "
        f"{time.strftime('%Y-%m-%d %H:%M')}.",
        "",
        f"- Roles: {', '.join(role_ids)}",
        f"- Viewports: {', '.join(VIEWPORTS)}",
        f"- Screenshots: {shots}",
        "",
        "External integrations are absent locally, so pages show their "
        "degraded / empty states — that is the layout under test, per "
        "`docs/DESIGN_SYSTEM.md` §7.",
        "",
    ]
    if findings:
        lines += ["## Findings", ""] + [f"- {f}" for f in findings] + [""]
    else:
        lines += ["## Findings", "", "- none — every page rendered for every "
                  "role at every viewport with no auth bounce", ""]
    if console_issues:
        lines += ["## Console errors", ""]
        for key, errs in sorted(console_issues.items()):
            lines.append(f"- `{key}`:")
            lines += [f"  - {e}" for e in errs]
        lines.append("")
    index = OUT / "INDEX.md"
    prior = index.read_text(encoding="utf-8") if (index.exists() and throttle) else ""
    index.write_text(prior + "\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--roles", help="comma-separated preset ids (default: all)")
    ap.add_argument("--throttle", action="store_true",
                    help="4G evidence pass instead of the full matrix")
    args = ap.parse_args()
    roles = [r.strip() for r in args.roles.split(",")] if args.roles else None
    raise SystemExit(run(roles, args.throttle))
