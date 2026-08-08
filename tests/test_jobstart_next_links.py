"""Job Start outcome screens must offer a Next path (flow continuity).

Run: .venv/bin/pytest tests/test_jobstart_next_links.py -q
  or: python tests/test_jobstart_next_links.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BODY = (ROOT / "web" / "jobstart.html").read_text(encoding="utf-8")


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_jobstart_outcomes_have_next_links() -> None:
    send_fn = BODY.split("function sendToOps()")[1].split("function acceptJob()")[0]
    accept_fn = BODY.split("function acceptJob()")[1].split("async function draftGcEmail()")[0]
    back_fn = BODY.split("function sendBackToSales()")[1].split("/* ---------- boot")[0]
    gc_fn = BODY.split("async function draftGcEmail()")[1].split("function sendBackToSales()")[0]

    check("send→ops has Next hub",
          'Next: <a href="/">hub</a>' in send_fn and "Morning Brief" in send_fn)
    check("send-back has Next",
          'Next:' in back_fn and 'href="/ui/jobstart"' in back_fn)
    check("accept success Next Job Check",
          "Open Job Check" in accept_fn and "Billing Hub" in accept_fn)
    check("incomplete accept Next retry",
          "tap <strong>Accept</strong> again" in accept_fn)
    check("already handed off Next",
          "Already handed off" in accept_fn and "Open Job Check" in accept_fn)
    check("GC draft Next", "Next: finish the packet fields" in gc_fn)


if __name__ == "__main__":
    test_jobstart_outcomes_have_next_links()
    print("ALL OK")
