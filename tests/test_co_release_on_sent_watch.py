"""
CO Operations-task release rides the sent-watch sweep — and stays independent
of it. Finalize defers a CO's Operations task until the CO is Approved in
Monday (PR #186); release_ops_tasks() is the release valve, and check_sent()
is its trigger (the gvc-sent-watch job already runs every 10 minutes — no new
scheduler job). The guarantees pinned here:

  • the CO leg runs even when the Gmail legs abort (Gmail scope/config
    failure) — the release touches Monday only;
  • a CO-leg failure (raise OR an ok=False result) never changes the
    sent-watch result for invoices / estimates / GC confirmations — it is
    reported in its own "co_ops_release" sub-object;
  • the response gains the co_ops_release summary without changing the
    shape of any existing key;
  • a dry run skips the leg (a dry run writes nothing; the release writes
    Operations tasks to Monday).

Everything external (Gmail, Monday, Slack, the sibling flows) is stubbed in
sys.modules — no network. Runs under pytest OR directly:
`python tests/test_co_release_on_sent_watch.py`.
"""
from __future__ import annotations

import contextlib
import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrators.sent_watch_flow import check_sent  # noqa: E402


_MISSING = object()


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@contextlib.contextmanager
def _stub_modules(stubs: dict):
    """Install stub modules into sys.modules (and as parent attributes),
    restoring whatever was there before — the suite runs in one process
    under pytest, so nothing may leak into other test files."""
    saved_modules: dict = {}
    saved_attrs: list = []
    for name, mod in stubs.items():
        saved_modules[name] = sys.modules.get(name, _MISSING)
        sys.modules[name] = mod
        parent_name, _, attr = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            saved_attrs.append((parent, attr, getattr(parent, attr, _MISSING)))
            setattr(parent, attr, mod)
    try:
        yield
    finally:
        for name, old in saved_modules.items():
            if old is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        for parent, attr, old in saved_attrs:
            if old is _MISSING:
                try:
                    delattr(parent, attr)
                except AttributeError:
                    pass
            else:
                setattr(parent, attr, old)


class _GmailScopeMissing(Exception):
    pass


class _GmailNotConfigured(Exception):
    pass


class _FakeMonday:
    def __init__(self, invoice_rows=()):
        self._invoice_rows = list(invoice_rows)
        self.stamped_invoices: list = []

    def fetch_invoice_rows(self, open_only=True):
        return list(self._invoice_rows)

    def stamp_invoice_emailed(self, item_id, date_str, current_status=None):
        self.stamped_invoices.append((item_id, date_str, current_status))


def _invoice_row() -> dict:
    return {"identifier": "2026-1101-01", "monday_item_id": "111",
            "emailed_on": None, "status": "Draft Ready",
            "issue_date": date.today().isoformat(),
            "customer": "Acme", "job": "Roof job", "amount": 100.0}


def _stubs(*, mc, find_sent_message, release_ops_tasks, slack_calls):
    """The full stub set check_sent() imports at call time."""
    gmail = _module("adapters.gmail",
                    GmailScopeMissing=_GmailScopeMissing,
                    GmailNotConfigured=_GmailNotConfigured,
                    find_sent_message=find_sent_message)
    slack = _module(
        "adapters.slack_notify",
        notify_invoice_emailed=lambda payload: slack_calls.append(("invoice", payload)),
        notify_estimate_emailed=lambda payload: slack_calls.append(("estimate", payload)),
        notify_gc_scope_emailed=lambda payload: slack_calls.append(("gc", payload)),
    )
    monday_pkg = _module("adapters.monday")
    estimate = _module("adapters.monday.estimate",
                       fetch_pending_estimates=lambda client: [],
                       stamp_estimate_emailed=lambda client, item_id, d: None)
    client = _module("adapters.monday.client", MondayClient=lambda: mc)
    monday_pkg.estimate = estimate
    monday_pkg.client = client
    adapters_pkg = _module("adapters")
    adapters_pkg.slack_notify = slack
    adapters_pkg.gmail = gmail
    adapters_pkg.monday = monday_pkg
    jobstart = _module("orchestrators.jobstart_flow",
                       gc_pending_confirmations=lambda: [],
                       stamp_gc_confirmed=lambda bid_id, d: None)
    co_flow = _module("orchestrators.change_order_flow",
                      release_ops_tasks=release_ops_tasks)
    return {"adapters": adapters_pkg,
            "adapters.slack_notify": slack,
            "adapters.gmail": gmail,
            "adapters.monday": monday_pkg,
            "adapters.monday.estimate": estimate,
            "adapters.monday.client": client,
            "orchestrators.jobstart_flow": jobstart,
            "orchestrators.change_order_flow": co_flow}


_RELEASE_OK = {"ok": True, "approved": 1, "released": ["CO.1-2026-1101-01"],
               "already_had_task": [], "errors": []}


# ------------------------------------------------------------ (a) Gmail abort

def test_co_leg_runs_when_gmail_aborts():
    calls: list = []

    def release():
        calls.append(True)
        return dict(_RELEASE_OK)

    def find_sent_message(needle, newer_than_days=None):
        raise _GmailScopeMissing("gmail scope missing")

    stubs = _stubs(mc=_FakeMonday([_invoice_row()]),
                   find_sent_message=find_sent_message,
                   release_ops_tasks=release, slack_calls=[])
    with _stub_modules(stubs):
        out = check_sent()
    # The Gmail abort itself behaves exactly as before…
    assert out["ok"] is False and out["code"] == "GmailScopeMissing", out
    assert out["estimates"] == [] and out["gc_confirmations"] == [], out
    # …but the Monday-only CO leg still ran and reported in.
    assert calls == [True], "CO release leg was skipped on Gmail abort"
    assert out["co_ops_release"]["released"] == ["CO.1-2026-1101-01"], out


# ------------------------------------------- (b) CO failure hurts nothing else

def test_co_leg_raise_does_not_change_other_legs():
    slack_calls: list = []
    mc = _FakeMonday([_invoice_row()])
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    def release():
        raise RuntimeError("monday exploded")

    stubs = _stubs(mc=mc,
                   find_sent_message=lambda needle, newer_than_days=None:
                       {"sent_epoch_ms": now_ms},
                   release_ops_tasks=release, slack_calls=slack_calls)
    with _stub_modules(stubs):
        out = check_sent()
    assert out["ok"] is True, out
    assert len(out["invoices"]) == 1, out
    entry = out["invoices"][0]
    assert entry["stamped"] is True and entry["notified"] is True, entry
    assert mc.stamped_invoices, "invoice stamp was lost"
    assert [kind for kind, _ in slack_calls] == ["invoice"], slack_calls
    assert out["errors"] == [], out
    # The failure is quarantined in the CO sub-object.
    rel = out["co_ops_release"]
    assert rel["ok"] is False and rel["code"] == "RELEASE_FAILED", rel
    assert "monday exploded" in rel["detail"], rel


def test_co_leg_failure_result_passes_through():
    failure = {"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": "no token"}
    stubs = _stubs(mc=_FakeMonday(),
                   find_sent_message=lambda needle, newer_than_days=None: None,
                   release_ops_tasks=lambda: dict(failure), slack_calls=[])
    with _stub_modules(stubs):
        out = check_sent()
    assert out["ok"] is True, out
    assert out["co_ops_release"] == failure, out


# ------------------------------------------------- (c) response gains the leg

def test_response_includes_co_summary_and_keeps_existing_shape():
    summary = {"ok": True, "approved": 2, "released": ["CO.2-2026-1101-01"],
               "already_had_task": ["CO.1-2026-1101-01"], "errors": []}
    stubs = _stubs(mc=_FakeMonday(),
                   find_sent_message=lambda needle, newer_than_days=None: None,
                   release_ops_tasks=lambda: dict(summary), slack_calls=[])
    with _stub_modules(stubs):
        out = check_sent()
    assert out["co_ops_release"] == summary, out
    assert out["ok"] is True and out["dry_run"] is False, out
    assert out["invoices"] == [] and out["estimates"] == [], out
    assert out["gc_confirmations"] == [], out
    assert out["skipped"] == {"invoices": 0, "estimates": 0,
                              "gc_confirmations": 0}, out
    assert out["errors"] == [], out


# ------------------------------------------------------- dry run skips the leg

def test_dry_run_skips_co_leg():
    calls: list = []

    def release():
        calls.append(True)
        return dict(_RELEASE_OK)

    stubs = _stubs(mc=_FakeMonday(),
                   find_sent_message=lambda needle, newer_than_days=None: None,
                   release_ops_tasks=release, slack_calls=[])
    with _stub_modules(stubs):
        out = check_sent(dry_run=True)
    assert calls == [], "dry run must not release CO ops tasks"
    assert out["co_ops_release"] == {"ok": True, "skipped": "dry_run"}, out


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"OK — {len(tests)} tests passed.")
