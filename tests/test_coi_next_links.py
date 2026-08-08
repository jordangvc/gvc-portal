"""COI success screens must offer a Next path (flow audit continuity).

Run: .venv/bin/pytest tests/test_coi_next_links.py -q
  or: python tests/test_coi_next_links.py
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


def test_coi_success_has_next_links() -> None:
    body = (ROOT / "web" / "coi.html").read_text(encoding="utf-8")
    single = 'Next: <a href="/">hub</a> · <a href="/ui/admin">Admin</a> (swap blank template)'
    bulk = 'Next: <a href="/">hub</a> · <a href="/ui/admin">Admin</a> (template)'
    check("single finalize Next", single in body)
    check("bulk finalize Next", bulk in body)


def main() -> None:
    print("test_coi_next_links")
    test_coi_success_has_next_links()
    print("ALL OK")


if __name__ == "__main__":
    main()
