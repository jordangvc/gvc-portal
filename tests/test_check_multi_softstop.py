"""Paid-by-Check multi-check soft-stop (FLOW-AUDIT #10).

Run: python tests/test_check_multi_softstop.py
  or: .venv/bin/pytest tests/test_check_multi_softstop.py -q
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = (ROOT / "web" / "check.html").read_text(encoding="utf-8")


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_check_multi_softstop_in_ui() -> None:
    check("tracks MULTI_CHECK_BLOCKED", "MULTI_CHECK_BLOCKED" in CHECK)
    check("locks Confirm when multi",
          'MULTI_CHECK_BLOCKED = !!(data.multi_check && data.multi_check > 1)' in CHECK)
    fill = CHECK.split("MULTI_CHECK_BLOCKED = !!(data.multi_check")[1].split(
        "const p = data.parsed")[0]
    check("disables confirm button", 'disabled = true' in fill and "$(\"#confirm\")" in fill)
    check("warn says Confirm is locked", "Confirm is locked" in CHECK)
    check("confirm() early-returns when blocked",
          "if (MULTI_CHECK_BLOCKED)" in CHECK.split("async function confirm")[1])
    check("error names one-at-a-time",
          "one check at a time" in CHECK.split("async function confirm")[1])


if __name__ == "__main__":
    test_check_multi_softstop_in_ui()
    print("all check multi soft-stop tests passed")
