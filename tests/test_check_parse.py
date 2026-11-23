"""Paid-by-Check OCR parsing: MICR trust rules + printed-number preference.

Regression cover for the check-reader bug: the MICR-line parse silently
won over the printed check number, and when the pre-routing MICR group
was missing/misread, the post-routing ACCOUNT number was recorded as
the check number (the office found full bank account numbers in check-#
notes on the Invoices board). Rules under test:
  - routing must pass the ABA checksum, else the MICR read is dropped;
  - no pre-routing group -> check_no is None, NEVER the account group;
  - the printed top-right number wins; both values are kept and a
    disagreement sets check_no_conflict.

Run: python tests/test_check_parse.py
  or: .venv/bin/pytest tests/test_check_parse.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subsystems.checks.deposit import parse_check_ocr, parse_micr  # noqa: E402

# 021000021 passes the ABA checksum (weights 3,7,1 -> total 30).
ROUTING_OK = "021000021"
# Same digits with the last digit bumped: checksum total 31 -> invalid.
ROUTING_BAD = "021000022"
ACCOUNT = "1234567890"

STANDARD = """\
1042
ACME BUILDERS LLC
123 Main Street
June 15, 2026
PAY TO THE ORDER OF Green Valley Contractors
$1,250.00
One thousand two hundred fifty and 00/100 DOLLARS
MEMO Invoice 2026-0611-001
⑈1042⑈ ⑆{routing}⑆ {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

MISSING_PRE_ROUTING = """\
2051
DANIS BUILDERS
PAY TO THE ORDER OF Green Valley Contractors
$4,800.00
MEMO Invoice 2026-0611-002
⑆{routing}⑆ {account}⑈
""".format(routing=ROUTING_OK, account=ACCOUNT)

CONFLICT = """\
1042
ACME BUILDERS LLC
PAY TO THE ORDER OF Green Valley Contractors
$1,250.00
⑈9999⑈ ⑆{routing}⑆ {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

BAD_CHECKSUM = """\
1042
ACME BUILDERS LLC
PAY TO THE ORDER OF Green Valley Contractors
$1,250.00
⑈1042⑈ ⑆{routing}⑆ {account}
""".format(routing=ROUTING_BAD, account=ACCOUNT)


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_standard_layout_agrees() -> None:
    p = parse_check_ocr(STANDARD)
    check("check number read", p.check_no == "1042")
    check("printed number kept", p.check_no_printed == "1042")
    check("micr number kept", p.check_no_micr == "1042")
    check("no conflict when they agree", p.check_no_conflict is False)
    check("routing read", p.routing == ROUTING_OK)
    check("account read", p.account == ACCOUNT)
    d = p.as_dict()
    check("as_dict carries the new fields",
          d["check_no_printed"] == "1042" and d["check_no_micr"] == "1042"
          and d["check_no_conflict"] is False)


def test_missing_pre_routing_group_never_returns_account() -> None:
    lines = [ln.strip() for ln in MISSING_PRE_ROUTING.splitlines() if ln.strip()]
    micr = parse_micr(lines)
    check("micr check_no is None, not the account number",
          micr.get("check_no") is None)
    check("micr account still parsed", micr.get("account") == ACCOUNT)
    p = parse_check_ocr(MISSING_PRE_ROUTING)
    check("account number is never the check number", p.check_no != ACCOUNT)
    check("printed number is used instead", p.check_no == "2051")
    check("micr check number recorded as missing", p.check_no_micr is None)


def test_printed_vs_micr_conflict_printed_wins() -> None:
    p = parse_check_ocr(CONFLICT)
    check("printed (top-right) number wins", p.check_no == "1042")
    check("micr number kept for the warning", p.check_no_micr == "9999")
    check("conflict flag set", p.check_no_conflict is True)


def test_routing_checksum_failure_drops_micr() -> None:
    lines = [ln.strip() for ln in BAD_CHECKSUM.splitlines() if ln.strip()]
    check("bad-checksum MICR treated as unread", parse_micr(lines) == {})
    p = parse_check_ocr(BAD_CHECKSUM)
    check("no routing recorded", p.routing is None)
    check("no micr check number recorded", p.check_no_micr is None)
    check("printed number still used", p.check_no == "1042")
    check("no conflict without a micr read", p.check_no_conflict is False)


if __name__ == "__main__":
    test_standard_layout_agrees()
    test_missing_pre_routing_group_never_returns_account()
    test_printed_vs_micr_conflict_printed_wins()
    test_routing_checksum_failure_drops_micr()
    print("test_check_parse: all checks passed")
