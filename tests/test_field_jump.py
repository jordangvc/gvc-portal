"""Clickable validation errors jump to the missing field."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUMP = ROOT / "web" / "gvc-field-jump.js"


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_field_jump_helpers() -> None:
    check("file exists", JUMP.is_file())
    script = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync('web/gvc-field-jump.js', 'utf8');
const sandbox = { console, globalThis: {}, document: {
  readyState: 'complete',
  addEventListener() {},
  body: { contains() { return true; } },
  getElementById() { return null; },
  querySelector() { return null; },
}};
sandbox.window = sandbox;
sandbox.global = sandbox.globalThis;
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const J = sandbox.GvcFieldJump || sandbox.globalThis.GvcFieldJump;
const link = J.link('Finish a client', 'client_name');
const list = J.list([
  { label: 'Address is required', focus: 'holder_address' },
  { label: 'Builder not set', focus: 'builder' },
]);
process.stdout.write(JSON.stringify({
  hasLink: link.includes('data-jump="client_name"') && link.includes('Finish a client'),
  hasList: list.includes('data-jump="holder_address"') && list.includes('Builder not set'),
  banner: J.banner('Email missing', 'client_emails').includes('data-jump="client_emails"'),
}));
"""
    r = subprocess.run(
        ["node", "-e", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "node failed")
    data = json.loads(r.stdout)
    check("link emits data-jump", data["hasLink"] is True)
    check("list emits jumps", data["hasList"] is True)
    check("banner emits jump", data["banner"] is True)


def test_pages_wire_field_jump() -> None:
    for name in ("estimate.html", "invoice.html", "change-order.html",
                 "jobstart.html", "coi.html", "jobcheck.html"):
        body = (ROOT / "web" / name).read_text(encoding="utf-8")
        check(f"{name} loads jump js", "/ui/gvc-field-jump.js" in body)
    stages = (ROOT / "web" / "gvc-form-stages.js").read_text(encoding="utf-8")
    check("stages paint gap banner", "Needs a fix — tap to go there" in stages)
    check("stages keep focus on gap", "data-gap-focus" in stages)
    est = (ROOT / "web" / "estimate.html").read_text(encoding="utf-8")
    check("estimate validateStep has focus", 'focus: "client_name"' in est)
    js = (ROOT / "web" / "jobstart.html").read_text(encoding="utf-8")
    check("jobstart missing list jumps", "jumpToMissingField" in js)
    check("jobstart sectioned bid list", "bid-section__list" in js)


def test_route_registers() -> None:
    import os
    import sys

    os.environ.setdefault("GVC_UI_DEV_BYPASS", "1")
    os.environ.setdefault("GVC_PORTAL_ALLOWED_EMAILS", "dev-bypass@localhost")
    sys.path.insert(0, str(ROOT))
    from app.service import app

    paths = {getattr(r, "path", None) for r in app.routes}
    check("field-jump route", "/ui/gvc-field-jump.js" in paths)


def test_initials_dot_email() -> None:
    from shared import hub_nav

    check("jordan.faulkner → JF", hub_nav.initials_for("", "jordan.faulkner@x.com") == "JF")
    check("Jordan Faulkner → JF", hub_nav.initials_for("Jordan Faulkner", "j@x.com") == "JF")


def test_people_and_results_css() -> None:
    css = (ROOT / "web" / "gvc-forms.css").read_text(encoding="utf-8")
    check("person name ellipsis", "text-overflow: ellipsis" in css and ".gvc-person-name" in css)
    check("results scroll cap", ".gvc-results" in css and "max-height: 17.5rem" in css)
    ui = (ROOT / "web" / "gvc-ui.css").read_text(encoding="utf-8")
    check("rail title nowrap", ".rail__foot .row__title" in ui)
    check("sp chip wrap", ".sp .chip" in ui and "overflow-wrap: anywhere" in ui)


if __name__ == "__main__":
    test_field_jump_helpers()
    test_pages_wire_field_jump()
    test_route_registers()
    test_initials_dot_email()
    test_people_and_results_css()
    print("ALL PASSED")
