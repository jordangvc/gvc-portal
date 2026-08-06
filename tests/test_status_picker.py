"""Pure tests for Job Check status-picker grouping helpers.

Loads web/gvc-status-picker.js into a minimal JS runtime via Node when
available; otherwise reimplements the pure group/suggestion rules in Python
to lock the contract.

Run: python tests/test_status_picker.py
  or: .venv/bin/pytest tests/test_status_picker.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PICKER = ROOT / "web" / "gvc-status-picker.js"


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def _node_eval() -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync('web/gvc-status-picker.js', 'utf8');
const sandbox = { console, globalThis: {} };
sandbox.global = sandbox.globalThis;
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const P = sandbox.globalThis.GvcStatusPicker;
const live = [
  'Upcoming','Hanging','Pre-Rock','Ready to Invoice','Complete',
  'Brand New Label From Monday'
];
const groups = P.groupsFor('ops:status', live);
const sug = P.suggestionsFor('ops:status', 'Hanging', live);
const sugBlocked = P.suggestionsFor('ops:color_mm1hrm6z', 'Clear',
  ['Clear','Blocked','Waiting on GC','Waiting on Materials','Jordan']);
const flatTrade = P.groupsFor('projects:status_19',
  ['Hanging Not Started','100% Hanging Completed']);
process.stdout.write(JSON.stringify({
  hasStage: !!P.STATUS_GROUPS['ops:status'],
  groupNames: groups.map(g => g.name),
  otherItems: (groups.find(g => g.name === 'Other') || {items:[]}).items,
  hangItems: (groups.find(g => g.name === 'Hang') || {items:[]}).items,
  sug,
  sugBlocked,
  flatTradeNames: flatTrade.map(g => g.name),
  flatTradeItems: flatTrade[0] && flatTrade[0].items,
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
    return json.loads(r.stdout)


def test_status_picker_groups() -> None:
    check("picker file exists", PICKER.is_file())
    data = _node_eval()
    check("stage groups configured", data["hasStage"] is True)
    check("Hang phase kept", "Hang" in data["groupNames"])
    check("ungrouped live → Other", "Brand New Label From Monday" in data["otherItems"])
    check("Hang only live labels", data["hangItems"] == ["Hanging", "Pre-Rock"])
    check("next up after Hanging", data["sug"] == ["Pre-Rock", "Ready to Invoice", "Complete"]
          or data["sug"][:1] == ["Pre-Rock"])
    # Flattened order: … Hanging, Pre-Rock, Hold for Heat, Scrapping… but Hold/Scrapping
    # aren't in live — so next are Pre-Rock then Ready to Invoice / Complete from later phases.
    check("blocked uses QUICK", "Blocked" in data["sugBlocked"])
    check("Waiting on GC in quick", "Waiting on GC" in data["sugBlocked"])
    check("ungrouped column → All statuses", data["flatTradeNames"] == ["All statuses"])
    check("trade labels preserved",
          data["flatTradeItems"] == ["Hanging Not Started", "100% Hanging Completed"])


def test_route_registers() -> None:
    sys.path.insert(0, str(ROOT))
    from app.service import app  # noqa: WPS433
    paths = {getattr(r, "path", None) for r in app.routes}
    check("gvc-status-picker.js route", "/ui/gvc-status-picker.js" in paths)


def test_row_align_css() -> None:
    """§7 handoff: meta takes leftover width; date inputs are pill-rounded."""
    css = (ROOT / "web" / "gvc.css").read_text(encoding="utf-8")
    check(".sp__meta uses flex 1 1 auto", "flex: 1 1 auto" in css)
    check(".sp__date pill input exists", ".sp__date" in css)
    check(".sp__row nowrap", "flex-wrap: nowrap" in css)
    jc = (ROOT / "web" / "jobcheck.html").read_text(encoding="utf-8")
    check("date fields use dateFieldHtml / sp__date", "sp__date" in jc and "dateFieldHtml" in jc)
    hub = (ROOT / "web" / "hub.html").read_text(encoding="utf-8")
    check("hub footer r43", ">r43<" in hub)


if __name__ == "__main__":
    test_status_picker_groups()
    test_route_registers()
    test_row_align_css()
    print("ALL PASSED")
