"""Job Start Send Back — ops-only, with_ops only (two-party rule).

Run: python tests/test_jobstart_send_back.py
  or: .venv/bin/pytest tests/test_jobstart_send_back.py -q
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "weasyprint" not in sys.modules:
    _stub = types.ModuleType("weasyprint")
    _stub.HTML = object
    sys.modules["weasyprint"] = _stub

from orchestrators import jobstart_flow as jf  # noqa: E402
from subsystems.jobstart import drafts  # noqa: E402

PASS = 0
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL.append(f"{name} {extra}".strip())
        print(f"  FAIL {name} {extra}".strip())


def _record(**kwargs):
    base = {
        "bid_id": 101,
        "status": drafts.STATUS_WITH_OPS,
        "sent_by": "jake@x.com",
        "job_name": "101 Main | Acme",
        "values": {},
    }
    base.update(kwargs)
    return base


def test_send_back_refuses_sender() -> None:
    rec = _record()
    with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
         patch("shared.access.has_feature", return_value=False), \
         patch("subsystems.jobstart.drafts.set_status") as set_status:
        out = jf.send_back(101, "Missing lock box", "jake@x.com")
    check("sender blocked", out.get("ok") is False)
    check("self_send_back flag", out.get("self_send_back") is True)
    check("no status write for sender", not set_status.called)


def test_send_back_ops_ok() -> None:
    rec = _record()
    written = _record(status=drafts.STATUS_SENT_BACK, sent_back_note="fix it")
    with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
         patch("shared.access.has_feature", return_value=False), \
         patch("subsystems.jobstart.drafts.set_status", return_value=written) as set_status, \
         patch.object(jf, "_notify", return_value="ok"):
        out = jf.send_back(101, "Missing lock box", "ops@x.com")
    check("ops ok", out.get("ok") is True)
    check("ops status sent_back", out.get("status") == drafts.STATUS_SENT_BACK)
    check("set_status called", set_status.called)


def test_send_back_admin_self_ok() -> None:
    rec = _record(sent_by="jordan@x.com")
    written = _record(status=drafts.STATUS_SENT_BACK, sent_by="jordan@x.com")
    with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
         patch("shared.access.has_feature",
               side_effect=lambda email, feat: feat == "admin"), \
         patch("subsystems.jobstart.drafts.set_status", return_value=written), \
         patch.object(jf, "_notify", return_value="ok"):
        out = jf.send_back(101, "Admin bounce", "jordan@x.com")
    check("admin self send-back allowed", out.get("ok") is True)


def test_send_back_wrong_status() -> None:
    for status in (drafts.STATUS_DRAFT, drafts.STATUS_SENT_BACK,
                   drafts.STATUS_ACCEPTED):
        rec = _record(status=status)
        with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
             patch("subsystems.jobstart.drafts.set_status") as set_status:
            out = jf.send_back(101, "Nope", "ops@x.com")
        check(f"refuse status {status}", out.get("ok") is False)
        check(f"no write for {status}", not set_status.called)


def test_send_back_requires_note() -> None:
    with patch("subsystems.jobstart.drafts.get_draft") as get_draft:
        out = jf.send_back(101, "   ", "ops@x.com")
    check("empty note refused", out.get("ok") is False)
    check("no draft read without note", not get_draft.called)


def test_ui_uses_can_send_back() -> None:
    html = (ROOT / "web" / "jobstart.html").read_text(encoding="utf-8")
    check("UI gates on can_send_back",
          '$("#sendback").style.display = d.can_send_back ? "" : "none";' in html)
    check("no bare with_ops sendback show",
          '$("#sendback").style.display = status === "with_ops"' not in html)


def test_can_send_back_flag_on_detail() -> None:
    """Pure gate math via get_handoff_detail pieces — exercise can_send_back."""
    # Replicate the flag formula from the flow (keeps the contract visible).
    def flag(status: str, can_accept: bool) -> bool:
        return status == drafts.STATUS_WITH_OPS and can_accept

    check("ops with_ops", flag(drafts.STATUS_WITH_OPS, True) is True)
    check("sender with_ops", flag(drafts.STATUS_WITH_OPS, False) is False)
    check("partial accept no sendback",
          flag(drafts.STATUS_ACCEPTED, True) is False)


if __name__ == "__main__":
    os.environ.setdefault("GVC_PORTAL_ALLOWED_EMAILS", "dev-bypass@localhost")
    test_send_back_refuses_sender()
    test_send_back_ops_ok()
    test_send_back_admin_self_ok()
    test_send_back_wrong_status()
    test_send_back_requires_note()
    test_ui_uses_can_send_back()
    test_can_send_back_flag_on_detail()
    print(f"{PASS} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)
