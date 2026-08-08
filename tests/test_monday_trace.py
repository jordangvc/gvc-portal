"""Monday request-trace helpers (measurement, not production behavior)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_trace_records_ok_and_error_calls():
    os.environ["GVC_MONDAY_TRACE"] = "1"
    import adapters.monday.client as mc_mod
    mc_mod._TRACE_ENABLED = True
    mc_mod.reset_monday_trace()

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"me": {"name": "x"}}}

    class _Bad:
        status_code = 429

        def raise_for_status(self):
            raise RuntimeError("429 Client Error")

        def json(self):
            return {}

    with patch.dict(os.environ, {"MONDAY_API_TOKEN": "tok"}):
        client = mc_mod.MondayClient(token="tok")
        client.session = MagicMock()
        client.session.post.return_value = _Resp()
        assert client._query("query { me { name } }") == {"me": {"name": "x"}}

        client.session.post.return_value = _Bad()
        try:
            client._query("query { me { name } }")
            raise AssertionError("expected raise")
        except RuntimeError:
            pass

    summary = mc_mod.monday_trace_summary()
    assert summary["count"] == 2
    assert summary["error_count"] == 1
    assert summary["rate_limited"] is True
    assert summary["calls"][0]["ok"] is True
    assert summary["calls"][1]["status"] == 429


def test_measure_script_budget_runs_without_token():
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "MONDAY_API_TOKEN"}
    env["GVC_MONDAY_TRACE"] = "0"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "measure_monday_paths.py")],
        cwd=str(ROOT), env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "office_hub" in proc.stdout
    assert "billing_search" in proc.stdout
    assert "no GFolder" in proc.stdout or "skips GFolder" in proc.stdout
    assert "retries=False" in proc.stdout


def _run_all() -> bool:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
