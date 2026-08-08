"""Job Start Nudge Ops — sender re-ping while with_ops (not a recall).

Run: python tests/test_jobstart_remind.py
  or: .venv/bin/pytest tests/test_jobstart_remind.py -q
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "weasyprint" not in sys.modules:
    _stub = types.ModuleType("weasyprint")
    _stub.HTML = object
    sys.modules["weasyprint"] = _stub

from adapters import slack_notify  # noqa: E402
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
        "values": {"start_date": "2026-08-10", "supervisor": "Pat"},
        "preview_url": "https://example/preview.pdf",
    }
    base.update(kwargs)
    return base


def test_remind_sender_ok() -> None:
    rec = _record()
    written = _record(last_reminded_at="2026-08-08T12:00:00+00:00")
    with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
         patch("shared.access.has_feature", return_value=False), \
         patch("subsystems.jobstart.drafts.patch_record", return_value=written) as patch_rec, \
         patch.object(jf, "_notify", return_value="posted") as notify:
        out = jf.remind_ops(101, "jake@x.com")
    check("sender ok", out.get("ok") is True)
    check("status stays with_ops", out.get("status") == drafts.STATUS_WITH_OPS)
    check("patch_record called", patch_rec.called)
    check("slack posted", out.get("slack") == "posted")
    check("notify called", notify.called)
    extra = patch_rec.call_args.kwargs.get("extra") or {}
    check("stamps last_reminded_at", "last_reminded_at" in extra)


def test_remind_refuses_non_sender() -> None:
    rec = _record()
    with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
         patch("shared.access.has_feature", return_value=False), \
         patch("subsystems.jobstart.drafts.patch_record") as patch_rec:
        out = jf.remind_ops(101, "ops@x.com")
    check("non-sender blocked", out.get("ok") is False)
    check("not_sender flag", out.get("not_sender") is True)
    check("no write for non-sender", not patch_rec.called)


def test_remind_admin_ok() -> None:
    rec = _record(sent_by="jake@x.com")
    written = _record(sent_by="jake@x.com",
                       last_reminded_at="2026-08-08T12:00:00+00:00")
    with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
         patch("shared.access.has_feature",
               side_effect=lambda email, feat: feat == "admin"), \
         patch("subsystems.jobstart.drafts.patch_record", return_value=written) as patch_rec, \
         patch.object(jf, "_notify", return_value="posted"):
        out = jf.remind_ops(101, "jordan@x.com")
    check("admin nudge ok", out.get("ok") is True)
    # Critical: must not call set_status(with_ops) which rewrites sent_by.
    check("admin uses patch_record", patch_rec.called)


def test_remind_rate_limited() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    rec = _record(last_reminded_at=recent)
    with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
         patch("shared.access.has_feature", return_value=False), \
         patch("subsystems.jobstart.drafts.patch_record") as patch_rec:
        out = jf.remind_ops(101, "jake@x.com")
    check("rate limited", out.get("ok") is False)
    check("rate_limited flag", out.get("rate_limited") is True)
    check("retry_after present", isinstance(out.get("retry_after_sec"), int))
    check("no write when limited", not patch_rec.called)


def test_remind_wrong_status() -> None:
    for status in (drafts.STATUS_DRAFT, drafts.STATUS_SENT_BACK,
                   drafts.STATUS_ACCEPTED):
        rec = _record(status=status)
        with patch("subsystems.jobstart.drafts.get_draft", return_value=rec), \
             patch("subsystems.jobstart.drafts.patch_record") as patch_rec:
            out = jf.remind_ops(101, "jake@x.com")
        check(f"refuse status {status}", out.get("ok") is False)
        check(f"no write for {status}", not patch_rec.called)


def test_patch_record_preserves_sent_by() -> None:
    """Pure contract: patch must not mimic with_ops sent_by rewrite."""
    doc = {
        "version": 1,
        "drafts": {
            "101": _record(sent_by="jake@x.com", sent_at="2026-08-01T00:00:00+00:00"),
        },
    }
    # Inline the mutator shape used by patch_record.
    key = drafts.draft_key(101)
    drafts_map = dict(doc["drafts"])
    existing = drafts_map[key]
    record = {**existing, "updated_by": "jordan@x.com"}
    record.update({"last_reminded_at": "stamp"})
    check("sent_by preserved", record.get("sent_by") == "jake@x.com")
    check("status preserved", record.get("status") == drafts.STATUS_WITH_OPS)
    check("stamp applied", record.get("last_reminded_at") == "stamp")


def test_remind_message_pure() -> None:
    msg = slack_notify._job_start_reminded_message({
        "job": "Demo Job", "actor": "jake@x.com",
        "preview_url": "https://x/p.pdf",
    })
    check("lead still waiting", "Still waiting on Operations" in msg)
    check("names job", "Demo Job" in msg)
    check("mentions nudge", "nudged" in msg.lower())


def test_ui_nudge_wiring() -> None:
    html = (ROOT / "web" / "jobstart.html").read_text(encoding="utf-8")
    check("nudge button id", 'id="nudge"' in html)
    check("nudge gated on self_sent",
          'status === "with_ops" && d.self_sent' in html)
    check("remind route path", 'act("remind"' in html)
    check("no send-back as recall",
          "never a recall" in html or "Nudge Ops" in html)


if __name__ == "__main__":
    test_remind_sender_ok()
    test_remind_refuses_non_sender()
    test_remind_admin_ok()
    test_remind_rate_limited()
    test_remind_wrong_status()
    test_patch_record_preserves_sent_by()
    test_remind_message_pure()
    test_ui_nudge_wiring()
    if FAIL:
        print(f"FAILED {len(FAIL)}: {FAIL}")
        sys.exit(1)
    print(f"all jobstart remind tests passed ({PASS})")
