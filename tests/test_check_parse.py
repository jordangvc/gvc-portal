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

Second regression (office live test, 2026-11-23): the printed-number
pick returned the first plausible token in reading order, so it saved
other numbers printed near the top of the check — the payer ZIP code
(47025 instead of check #10471) and the tail of a phone number (3376
instead of #1485). Rules under test:
  - the number is picked from the TOP-RIGHT: rightmost surviving word
    when Vision geometry is available, rightmost-on-line in text mode;
  - ZIP codes, phone segments (whole digit chains), date parts, amount
    echoes, street/PO-Box numbers, and the routing/account numbers are
    never the check number;
  - when more than one plausible number survives, check_no_ambiguous
    flags it for the confirm modal instead of saving quietly.

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



# --- Top-right selection (2026-11-23 live-test regressions) -----------------

def W(text, x0, y0, x1, y1):
    """One Vision word box, normalized page fractions (origin top-left)."""
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


# Andrea case A — JDC / Lattire, $6,400. Real check #10471 (top right);
# the reader saved 47025, the ZIP from the payer address line, because
# the address block precedes the number in OCR reading order.
CASE_A_ZIP = """\
JDC CONSTRUCTION LLC
Lawrenceburg, IN 47025
10471
November 18, 2026
PAY TO THE ORDER OF Green Valley Contractors
$6,400.00
Six thousand four hundred and 00/100 DOLLARS
MEMO Lattire job
\u240810471\u2408 \u2406021000021\u2406 {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

CASE_A_WORDS = [
    W("JDC", 0.03, 0.03, 0.07, 0.09), W("CONSTRUCTION", 0.08, 0.03, 0.24, 0.09),
    W("LLC", 0.25, 0.03, 0.29, 0.09),
    W("10471", 0.88, 0.02, 0.96, 0.09),
    W("Lawrenceburg,", 0.03, 0.12, 0.16, 0.18), W("IN", 0.17, 0.12, 0.20, 0.18),
    W("47025", 0.21, 0.12, 0.27, 0.18),
    W("November", 0.80, 0.13, 0.89, 0.19), W("18,", 0.90, 0.13, 0.93, 0.19),
    W("2026", 0.94, 0.13, 0.98, 0.19),
    W("PAY", 0.03, 0.30, 0.07, 0.36), W("TO", 0.08, 0.30, 0.11, 0.36),
    W("THE", 0.12, 0.30, 0.16, 0.36), W("ORDER", 0.17, 0.30, 0.23, 0.36),
    W("OF", 0.24, 0.30, 0.27, 0.36),
    W("Green", 0.30, 0.30, 0.36, 0.36), W("Valley", 0.37, 0.30, 0.43, 0.36),
    W("Contractors", 0.44, 0.30, 0.55, 0.36),
    W("$6,400.00", 0.80, 0.30, 0.92, 0.37),
    W("\u240810471\u2408", 0.05, 0.90, 0.14, 0.96), W("\u2406021000021\u2406", 0.18, 0.90, 0.32, 0.96),
    W("1234567890", 0.36, 0.90, 0.50, 0.96),
]

# Andrea case B — Stickford / Vevay paint, $16,000. Real check #1485;
# the reader saved 3376, the last group of the phone number printed
# under the payer name (the old scrub ate only "513-800" of the chain).
CASE_B_PHONE = """\
STICKFORD BUILDERS LLC
513-800-3376
Vevay, IN 47043
1485
November 19, 2026
PAY TO THE ORDER OF Green Valley Contractors
$16,000.00
Sixteen thousand and 00/100 DOLLARS
MEMO Vevay paint
\u24081485\u2408 \u2406021000021\u2406 {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

CASE_B_WORDS = [
    W("STICKFORD", 0.03, 0.03, 0.15, 0.09), W("BUILDERS", 0.16, 0.03, 0.26, 0.09),
    W("LLC", 0.27, 0.03, 0.31, 0.09),
    W("1485", 0.89, 0.02, 0.96, 0.09),
    W("513", 0.03, 0.12, 0.07, 0.18), W("800", 0.08, 0.12, 0.12, 0.18),
    W("3376", 0.13, 0.12, 0.18, 0.18),
    W("Vevay,", 0.03, 0.20, 0.09, 0.26), W("IN", 0.10, 0.20, 0.13, 0.26),
    W("47043", 0.14, 0.20, 0.20, 0.26),
    W("November", 0.80, 0.13, 0.89, 0.19), W("19,", 0.90, 0.13, 0.93, 0.19),
    W("2026", 0.94, 0.13, 0.98, 0.19),
    W("$16,000.00", 0.79, 0.30, 0.92, 0.37),
    W("\u24081485\u2408", 0.05, 0.90, 0.13, 0.96), W("\u2406021000021\u2406", 0.17, 0.90, 0.31, 0.96),
    W("1234567890", 0.35, 0.90, 0.49, 0.96),
]

ZIP_ONLY = """\
ACME BUILDERS LLC
Lawrenceburg, IN 47025
PAY TO THE ORDER OF Green Valley Contractors
$900.00
\u24087781\u2408 \u2406021000021\u2406 {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

PHONE_ONLY = """\
ACME BUILDERS LLC
513-800-3376
PAY TO THE ORDER OF Green Valley Contractors
$900.00
\u24087781\u2408 \u2406021000021\u2406 {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

STREET_NUMBER = """\
ACME BUILDERS LLC
4800 Industrial Pkwy
2314
PAY TO THE ORDER OF Green Valley Contractors
$900.00
\u24082314\u2408 \u2406021000021\u2406 {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

SAME_LINE = """\
ACME BUILDERS LLC
5821 9034
PAY TO THE ORDER OF Green Valley Contractors
$900.00
\u24089034\u2408 \u2406021000021\u2406 {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

PREFERENCE_TEXT = """\
ACME BUILDERS LLC
5821
9034
PAY TO THE ORDER OF Green Valley Contractors
$900.00
\u24089034\u2408 \u2406021000021\u2406 {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

# 5821 sits top-LEFT, 9034 top-RIGHT, and the account number is the
# rightmost word of all — it must still never be picked.
PREFERENCE_WORDS = [
    W("5821", 0.04, 0.03, 0.10, 0.09),
    W("9034", 0.88, 0.03, 0.95, 0.09),
    W("1234567890", 0.90, 0.14, 0.99, 0.20),
]

AMBIG_TEXT = """\
ACME BUILDERS LLC
4471
9034
PAY TO THE ORDER OF Green Valley Contractors
$900.00
\u24089034\u2408 \u2406021000021\u2406 {account}
""".format(routing=ROUTING_OK, account=ACCOUNT)

# Two distinct plain numbers in the top-right region: pick the rightmost
# but flag the ambiguity for the confirm modal.
AMBIG_WORDS = [
    W("4471", 0.60, 0.03, 0.67, 0.09),
    W("9034", 0.88, 0.03, 0.95, 0.09),
]


def test_case_a_zip_is_not_the_check_number() -> None:
    p = parse_check_ocr(CASE_A_ZIP)
    check("case A (text): check number is 10471", p.check_no == "10471")
    check("case A (text): printed number is 10471", p.check_no_printed == "10471")
    check("case A (text): ZIP never recorded", p.check_no != "47025")
    check("case A (text): no conflict — MICR agrees", p.check_no_conflict is False)
    p = parse_check_ocr(CASE_A_ZIP, words=CASE_A_WORDS)
    check("case A (words): check number is 10471", p.check_no == "10471")
    check("case A (words): ZIP never recorded", p.check_no != "47025")
    check("case A (words): not flagged ambiguous", p.check_no_ambiguous is False)


def test_case_b_phone_suffix_is_not_the_check_number() -> None:
    p = parse_check_ocr(CASE_B_PHONE)
    check("case B (text): check number is 1485", p.check_no == "1485")
    check("case B (text): printed number is 1485", p.check_no_printed == "1485")
    check("case B (text): phone suffix never recorded", p.check_no != "3376")
    p = parse_check_ocr(CASE_B_PHONE, words=CASE_B_WORDS)
    check("case B (words): check number is 1485", p.check_no == "1485")
    check("case B (words): phone suffix never recorded", p.check_no != "3376")
    check("case B (words): not flagged ambiguous", p.check_no_ambiguous is False)


def test_zip_only_falls_back_to_micr() -> None:
    p = parse_check_ocr(ZIP_ONLY)
    check("ZIP is not a printed check number", p.check_no_printed is None)
    check("MICR number used when no printed number survives", p.check_no == "7781")
    check("ZIP never recorded", p.check_no != "47025")


def test_phone_only_falls_back_to_micr() -> None:
    p = parse_check_ocr(PHONE_ONLY)
    check("phone tail is not a printed check number", p.check_no_printed is None)
    check("MICR number used when no printed number survives", p.check_no == "7781")
    check("phone suffix never recorded", p.check_no != "3376")


def test_street_number_is_not_the_check_number() -> None:
    p = parse_check_ocr(STREET_NUMBER)
    check("check number read past the street address", p.check_no == "2314")
    check("street number never recorded", p.check_no != "4800")
    check("single survivor — not ambiguous", p.check_no_ambiguous is False)


def test_rightmost_candidate_on_a_line_wins() -> None:
    p = parse_check_ocr(SAME_LINE)
    check("rightmost candidate on the line wins", p.check_no == "9034")


def test_geometry_prefers_rightmost_and_never_account() -> None:
    p = parse_check_ocr(PREFERENCE_TEXT, words=PREFERENCE_WORDS)
    check("geometry: top-right number wins over top-left", p.check_no == "9034")
    check("geometry: account number never picked", p.check_no != ACCOUNT)
    check("geometry: one right-region survivor — not ambiguous",
          p.check_no_ambiguous is False)


def test_ambiguous_top_right_is_flagged() -> None:
    p = parse_check_ocr(AMBIG_TEXT, words=AMBIG_WORDS)
    check("ambiguity: rightmost still filled in", p.check_no == "9034")
    check("ambiguity: flag set for the modal warning", p.check_no_ambiguous is True)
    check("ambiguity: not a MICR conflict (MICR agrees)", p.check_no_conflict is False)
    d = p.as_dict()
    check("as_dict carries the ambiguity flag", d["check_no_ambiguous"] is True)


if __name__ == "__main__":
    test_standard_layout_agrees()
    test_missing_pre_routing_group_never_returns_account()
    test_printed_vs_micr_conflict_printed_wins()
    test_routing_checksum_failure_drops_micr()
    test_case_a_zip_is_not_the_check_number()
    test_case_b_phone_suffix_is_not_the_check_number()
    test_zip_only_falls_back_to_micr()
    test_phone_only_falls_back_to_micr()
    test_street_number_is_not_the_check_number()
    test_rightmost_candidate_on_a_line_wins()
    test_geometry_prefers_rightmost_and_never_account()
    test_ambiguous_top_right_is_flagged()
    print("test_check_parse: all checks passed")
