"""Owner Pulse must not dead-end on load failure or empty decisions.

Run: .venv/bin/pytest tests/test_owner_pulse_continuity.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_owner_pulse_error_and_next() -> None:
    body = (ROOT / "web" / "morning-owner.html").read_text(encoding="utf-8")
    check("decision cards", "function decisionCard" in body)
    check("safety cards", "function safetyCard" in body)
    check("error retry", "err-retry" in body and "Try again" in body)
    check("error links morning+hub", 'href="/ui/morning"' in body and 'href="/"' in body)
    check("next strip", "id=\"nextstrip\"" in body or 'id="nextstrip"' in body)
    check("no JSON.stringify dump", "JSON.stringify" not in body)


def main() -> None:
    print("test_owner_pulse_continuity")
    test_owner_pulse_error_and_next()
    print("ALL OK")


if __name__ == "__main__":
    main()
