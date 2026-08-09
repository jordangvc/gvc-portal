"""Field-experience UI tests for /ui/inventory.

Static assertions on web/inventory.html (the mobile field tool), JS parse
checks via ``node --check`` on FILE PATHS (never stdin — Windows encoding
trap), and outbox behavior checks running web/gvc-inventory.js inside a
Node ``vm`` sandbox with a localStorage stub (the tests/test_field_jump.py
harness style).

Runs under pytest OR directly: ``python tests/test_inventory_ui.py``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
PAGE = WEB / "inventory.html"
JS = WEB / "gvc-inventory.js"


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def _node(args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", *args], cwd=str(cwd), capture_output=True, text=True,
        check=False,
    )


# ----------------------------------------------------------- page structure

def test_page_structure() -> None:
    html = _html()
    check("page exists", PAGE.is_file())
    check("links gvc-ui.css", 'href="/ui/gvc-ui.css"' in html)
    check("does NOT link legacy gvc.css", 'href="/ui/gvc.css"' not in html)
    m = re.search(r'<meta name="viewport" content="([^"]+)"', html)
    check("viewport meta present", m is not None)
    check("viewport-fit=cover", "viewport-fit=cover" in m.group(1))
    check("emerald palette", 'data-palette="emerald"' in html)
    check("FOUC theme boot in head",
          'localStorage.getItem' in html.split("</head>")[0]
          and "data-theme" in html.split("</head>")[0])
    check("EMAIL_JSON token present", "{{EMAIL_JSON}}" in html)
    check("EMAIL token present", "{{EMAIL}}" in html)


def test_no_select_elements() -> None:
    html = _html()
    markup, _, _ = html.lower().partition("<script")
    check("no <select> before first script", "<select" not in markup)
    # Belt and braces: no <select> anywhere outside script bodies either —
    # chip/list pickers only (DESIGN_SYSTEM.md §2).
    stripped = re.sub(r"<script[^>]*>.*?</script>", "", html,
                      flags=re.S | re.I)
    check("no <select> anywhere in markup", "<select" not in stripped.lower())


def test_field_tool_affordances() -> None:
    html = _html()
    for label in (">Drop off<", ">Pick up<", ">Transfer<", ">Count<"):
        check(f"action button {label}", label in html)
    check("manual scan fallback input", 'id="scan-manual"' in html)
    check("manual fallback wording",
          "Type the code printed on the label" in html)
    check("outbox localStorage key", "gvc_inv_outbox_v1" in html)
    check("Not listed affordance", "Not listed" in html)
    check("pending-sync banner", 'id="sync-banner"' in html)
    check("scan continuously toggle", 'id="cont-scan"' in html)
    check("live status region", 'role="status"' in html)
    check("decimal keyboard on qty", 'inputmode="decimal"' in html)
    check("camera capture photo input", 'capture="environment"' in html)
    check("app shell", '<div class="app"' in html
          and 'class="rail"' in html and 'class="topbar"' in html
          and 'class="tabbar"' in html)
    check("honest offline wording", "Waiting to sync" in html)
    check("admin link is gated markup", 'id="rail-admin"' in html
          and "/ui/inventory/admin" in html)


# ------------------------------------------------------------- JS parses

def test_gvc_inventory_js_parses() -> None:
    check("gvc-inventory.js exists", JS.is_file())
    r = _node(["--check", "web/gvc-inventory.js"])
    if r.returncode != 0:
        raise AssertionError(f"node --check failed:\n{r.stderr}")
    print("  ok  node --check gvc-inventory.js")


def test_inline_scripts_parse_after_token_substitution() -> None:
    """The page must parse as JS AFTER the route substitutes {{TOKENS}}."""
    html = _html()
    scripts = []
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        if "src=" in attrs or not body.strip():
            continue
        scripts.append(body)
    check("page has inline scripts", len(scripts) >= 2)  # theme boot + app
    for i, src in enumerate(scripts):
        rendered = re.sub(r"\{\{[A-Z0-9_]+\}\}", '"x@y"', src)
        fd, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(rendered)
            r = _node(["--check", path])
            if r.returncode != 0:
                raise AssertionError(
                    f"inline script #{i} failed node --check:\n{r.stderr}")
            print(f"  ok  inline script #{i} parses")
        finally:
            os.unlink(path)


def test_page_scripts_wire_the_shared_module() -> None:
    html = _html()
    check("loads /ui/gvc-inventory.js", '/ui/gvc-inventory.js' in html)
    check("loads /ui/gvc-theme.js", '/ui/gvc-theme.js' in html)
    check("client_uuid comes from the shared cart",
          "GvcInventory" in html and "newCart" in html)
    check("txn endpoint used", "/ui/api/inventory/txn" in html)
    check("scan resolve endpoint used", "/ui/api/inventory/scan" in html)
    check("unknown-item endpoint used",
          "/ui/api/inventory/unknown-item" in html)


# ------------------------------------------------------ outbox unit checks

_OUTBOX_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync('web/gvc-inventory.js', 'utf8');
function storage() {
  const m = new Map();
  return {
    getItem: k => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k),
  };
}
const sandbox = { console };
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const G = sandbox.GvcInventory;

(async () => {
  const out = {};

  // 1. enqueue -> persist -> reload round-trip (restart-safe queueing).
  const st = storage();
  const ob1 = G.createOutbox({ email: 'X@Y.com', storage: st });
  ob1.enqueue('/ui/api/inventory/txn',
    { client_uuid: 'uuid-1', type: 'RECEIVE', dst: 'L1',
      lines: [{ item_id: 'i1', qty: '2', unit: 'each' }] },
    { label: 'test drop off' });
  const ob2 = G.createOutbox({ email: 'x@y.com', storage: st });
  const q = ob2.list();
  out.roundtrip = q.length === 1 && q[0].body.client_uuid === 'uuid-1'
    && q[0].state === 'pending' && q[0].label === 'test drop off';
  out.email_scoped_key = ob1.key === 'gvc_inv_outbox_v1:x@y.com';

  // 2. SAME client_uuid across retries; a 200 removes the entry.
  const sent = [];
  let calls = 0;
  const flaky = (path, init) => {
    sent.push(JSON.parse(init.body).client_uuid);
    calls += 1;
    if (calls === 1) return Promise.reject(new TypeError('network down'));
    return Promise.resolve({ ok: true, status: 200,
      json: async () => ({ ok: true, txn: { txn_no: 'INV-000001' } }) });
  };
  const st2 = storage();
  const ob3 = G.createOutbox({ email: 'a@b', storage: st2, fetch: flaky });
  ob3.enqueue('/ui/api/inventory/txn',
    { client_uuid: 'keep-me', type: 'RECEIVE', dst: 'L1', lines: [] });
  await ob3.sync({ force: true });
  const afterFail = ob3.list()[0];
  out.network_failure_stays_pending =
    afterFail.state === 'pending' && afterFail.tries === 1
    && afterFail.last_error.length > 0;
  const r2 = await ob3.sync({ force: true });
  out.same_uuid_across_retries =
    JSON.stringify(sent) === JSON.stringify(['keep-me', 'keep-me']);
  out.two_hundred_removes_entry = ob3.list().length === 0;
  out.posted_reported = r2.length === 1 && r2[0].state === 'posted';

  // 3. A 409 moves the entry to needs_attention and is NOT retried.
  let calls409 = 0;
  const conflict = () => {
    calls409 += 1;
    return Promise.resolve({ ok: false, status: 409,
      json: async () => ({ detail: { ok: false, code: 'INSUFFICIENT_STOCK',
        detail: "Only 1 each of 'Screws' at the source.",
        advice: 'Lower the amount.' } }) });
  };
  const st3 = storage();
  const ob4 = G.createOutbox({ email: 'a@b', storage: st3, fetch: conflict });
  ob4.enqueue('/ui/api/inventory/txn',
    { client_uuid: 'u409', type: 'ISSUE', src: 'L1', dst: 'L2', lines: [] });
  await ob4.sync({ force: true });
  const parked = ob4.list()[0];
  out.conflict_needs_attention = parked.state === 'needs_attention';
  out.conflict_message_kept = parked.last_error.indexOf('Screws') !== -1;
  await ob4.sync({ force: true });
  await ob4.sync({ force: true });
  out.conflict_not_retried_silently = calls409 === 1;
  out.counts_shape = JSON.stringify(ob4.counts()) ===
    JSON.stringify({ total: 1, pending: 0, attention: 1 });
  // Explicit user retry re-arms it (and it 409s again -> parked again).
  ob4.retry(parked.id);
  await ob4.sync({ force: true });
  out.user_retry_rearms = calls409 === 2
    && ob4.list()[0].state === 'needs_attention';
  // Edit-and-retry keeps the ORIGINAL client_uuid even if the edit lies.
  const edited = ob4.update(parked.id,
    { client_uuid: 'HACKED', type: 'ISSUE', src: 'L1', dst: 'L2',
      lines: [{ item_id: 'i1', qty: '1', unit: 'each' }] });
  out.edit_keeps_uuid = edited.body.client_uuid === 'u409'
    && edited.state === 'pending';
  // Discard removes it for good.
  ob4.discard(parked.id);
  out.discard_removes = ob4.list().length === 0;

  // 4. Cart: uuid generated once and carried into the txn payload.
  const cart = G.newCart('TRANSFER');
  const u = cart.client_uuid;
  G.cartAddQty(cart, { id: 'i1', name: 'Screws', base_unit: 'each' }, 2, 'each');
  G.cartAddQty(cart, { id: 'i1', name: 'Screws', base_unit: 'each' }, 3, 'each');
  const dupAsset = (G.cartAddAsset(cart, { id: 'A-1', name: 'Lift' }),
                    G.cartAddAsset(cart, { id: 'A-1', name: 'Lift' }));
  cart.src = 'L1'; cart.dst = 'L2';
  const txn = G.cartToTxn(cart);
  out.cart_uuid_stable = txn.client_uuid === u && u.length === 36;
  out.cart_merges_same_unit = txn.lines[0].qty === '5';
  out.cart_dedupes_assets = dupAsset === null
    && cart.lines.filter(l => l.kind === 'asset').length === 1;
  const st5 = storage();
  G.saveCart(st5, 'a@b', cart);
  const back = G.loadCart(st5, 'a@b');
  out.cart_survives_reload = back.client_uuid === u
    && back.lines.length === cart.lines.length;

  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


def test_outbox_behavior_in_node_vm() -> None:
    r = _node(["-e", _OUTBOX_HARNESS])
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "node harness failed")
    data = json.loads(r.stdout)
    for key, val in sorted(data.items()):
        check(f"outbox: {key}", val is True)


# -------------------------------------------------------- route registration

def test_inventory_page_route_registered() -> None:
    # Env + stub pattern from tests/test_inventory_api.py — set BEFORE the
    # app import so this file also passes standalone.
    os.environ.setdefault("GVC_SESSION_SECRET", "t" * 64)
    os.environ.setdefault("GVC_GRANTS_BACKEND", "env")
    _wp = types.ModuleType("weasyprint")
    _wp.HTML = object
    _wp.CSS = object
    sys.modules.setdefault("weasyprint", _wp)
    sys.path.insert(0, str(ROOT))

    from app.service import app

    paths = {getattr(r, "path", None) for r in app.routes}
    check("/ui/inventory route", "/ui/inventory" in paths)
    check("txn API route", "/ui/api/inventory/txn" in paths)
    check("scan API route", "/ui/api/inventory/scan" in paths)
    check("gvc-inventory.js served", "/ui/gvc-inventory.js" in paths
          or (WEB / "gvc-inventory.js").is_file())


if __name__ == "__main__":
    test_page_structure()
    test_no_select_elements()
    test_field_tool_affordances()
    test_gvc_inventory_js_parses()
    test_inline_scripts_parse_after_token_substitution()
    test_page_scripts_wire_the_shared_module()
    test_outbox_behavior_in_node_vm()
    test_inventory_page_route_registered()
    print("ALL PASSED")
