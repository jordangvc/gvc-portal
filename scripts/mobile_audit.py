"""One-shot mobile audit: every page, phone viewport, mechanical checks + shots.

For each page the full preset can open, at 390x844 and 375x667:
  - horizontal overflow (document.scrollWidth vs viewport) + the offending
    elements' selectors/widths,
  - type census: computed font-size/family of h1, kickers, body text, buttons —
    so cross-page inconsistency ("fonts don't line up") is measurable,
  - elements with font-size < 11px (unreadable in the field),
  - screenshot to _mobile-audit/ for eyeball review.

Local tool — output dir is git-ignored scratch, not evidence.
"""
from __future__ import annotations

import contextlib
import json
import secrets
import socket
import sys
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OUT = ROOT / "_mobile-audit"
HOST, PORT = "127.0.0.1", 8770
BASE = f"http://{HOST}:{PORT}"

CHECK_JS = """
() => {
  const vw = window.innerWidth;
  const out = { vw, scrollW: document.documentElement.scrollWidth, wide: [], tiny: [], type: {} };
  const sel = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += "#" + el.id;
    else if (el.className && typeof el.className === "string")
      s += "." + el.className.trim().split(/\\s+/).slice(0,2).join(".");
    return s;
  };
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width > vw + 1 && el.children.length < 30)
      out.wide.push({ sel: sel(el), w: Math.round(r.width) });
    const st = getComputedStyle(el);
    const fs = parseFloat(st.fontSize);
    if (fs && fs < 11 && el.textContent.trim().length > 2 && r.height > 0)
      out.tiny.push({ sel: sel(el), fs: Math.round(fs*10)/10, txt: el.textContent.trim().slice(0,30) });
  }
  const probe = (name, q) => {
    const el = document.querySelector(q);
    if (!el) return;
    const st = getComputedStyle(el);
    out.type[name] = { fs: st.fontSize, fam: st.fontFamily.split(",")[0].replace(/['"]/g,""), w: st.fontWeight };
  };
  probe("h1", "h1"); probe("h2", "h2"); probe("kicker", ".kicker");
  probe("body-p", ".page p, .gvc-shell p, main p"); probe("btn", ".btn, .gvc-btn");
  probe("input", "input[type=text], .input, .gvc-input");
  out.wide = out.wide.slice(0, 8); out.tiny = out.tiny.slice(0, 8);
  return out;
}
"""


def boot():
    import os
    os.environ["GVC_SESSION_SECRET"] = secrets.token_hex(32)
    os.environ["GVC_GRANTS_BACKEND"] = "env"
    os.environ["GVC_PORTAL_ALLOWED_EMAILS"] = "test-full@localhost"
    for v in ("MONDAY_API_TOKEN", "STRIPE_API_KEY"):
        os.environ.pop(v, None)
    wp = types.ModuleType("weasyprint"); wp.HTML = object; wp.CSS = object
    sys.modules.setdefault("weasyprint", wp)
    from shared import access
    feats = set(access.FEATURES)
    access.is_provisioned = lambda e: e == "test-full@localhost"
    access.effective_features = lambda e: feats if e == "test-full@localhost" else set()
    from app.service import app
    import uvicorn
    srv = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        with contextlib.closing(socket.socket()) as s:
            if s.connect_ex((HOST, PORT)) == 0:
                return
        time.sleep(0.1)
    raise SystemExit("no server")


def main():
    boot()
    from shared import auth as pa
    from tests.test_role_home_reachable import PAGE_FEATURE
    cookie = pa.make_session_cookie("test-full@localhost")
    pages = sorted(PAGE_FEATURE) + ["/ui/nonneg"]
    OUT.mkdir(exist_ok=True)
    report = {}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        for w, h in ((390, 844), (375, 667)):
            ctx = b.new_context(viewport={"width": w, "height": h},
                                is_mobile=True, has_touch=True, device_scale_factor=2)
            ctx.add_cookies([{"name": "gvc_session", "value": cookie,
                              "domain": HOST, "path": "/"}])
            pg = ctx.new_page()
            for path in pages:
                name = "hub" if path == "/" else path.rsplit("/", 1)[-1]
                try:
                    pg.goto(BASE + path, wait_until="domcontentloaded", timeout=20000)
                    pg.wait_for_timeout(1400)
                    data = pg.evaluate(CHECK_JS)
                    report[f"{name}@{w}"] = data
                    if w == 390:
                        pg.screenshot(path=str(OUT / f"{name}.jpg"),
                                      type="jpeg", quality=75, full_page=True)
                except Exception as exc:  # noqa: BLE001
                    report[f"{name}@{w}"] = {"error": str(exc)[:120]}
            ctx.close()
        b.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    # summary
    print(f"{'page@vw':22} {'overflow':9} wide-elements / tiny-text")
    for key, d in sorted(report.items()):
        if "error" in d:
            print(f"{key:22} ERROR {d['error']}"); continue
        over = d["scrollW"] - d["vw"]
        flag = f"+{over}px" if over > 1 else "ok"
        wide = "; ".join(f"{x['sel']}({x['w']})" for x in d["wide"][:3])
        tiny = "; ".join(f"{x['sel']}={x['fs']}" for x in d["tiny"][:3])
        print(f"{key:22} {flag:9} {wide or '-'} | {tiny or '-'}")
    print("\ntype census (390):")
    print(f"{'page':16} {'h1':16} {'kicker':14} {'body':14} {'btn':14} {'input':10}")
    for key, d in sorted(report.items()):
        if not key.endswith("@390") or "error" in d:
            continue
        t = d.get("type", {})
        f = lambda n: (t.get(n, {}).get("fs", "-") + "/" + t.get(n, {}).get("fam", "")[:8]) if n in t else "-"
        print(f"{key[:-4]:16} {f('h1'):16} {f('kicker'):14} {f('body-p'):14} {f('btn'):14} {f('input'):10}")


if __name__ == "__main__":
    main()
