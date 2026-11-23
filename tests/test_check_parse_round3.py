"""Paid-by-Check OCR parsing: round-3 live-retest regressions.

Third regression round (office live retest, 2026-11-23, on the build with
the PR #186/#189/#190 fixes live). Three more real checks:

  - Kaiker, real check #4837: the printed number was missed, the MICR
    fallback kept its leading zeros ("#004837"), and the DATE was taken
    from the bank routing fraction "24-22/1230" instead of the real
    date 11/03/2026. (The fraction-as-date bug dates to the original
    July 2026 report.)
  - Jed barn, real check #1056: the reader saved "#123" from the job
    address "State Route 123" instead of the top-right corner.
  - Zicka, real check #14145: saved correctly, but a false mismatch
    warning fired because the MICR line reads 014145 -- the same number
    with a leading zero.

The office's three rules under test:
  - always use the number in the top right corner;
  - never use the bank code (routing fraction) as the date;
  - ignore leading zeros when comparing to the bottom (MICR) line.

Run: python tests/test_check_parse_round3.py
  or: .venv/bin/pytest tests/test_check_parse_round3.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subsystems.checks.deposit import (  # noqa: E402
    find_check_number, find_date, parse_check_ocr)

# 021000021 passes the ABA checksum (weights 3,7,1 -> total 30).
ROUTING_OK = "021000021"
ACCOUNT = "1234567890"


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def W(text, x0, y0, x1, y1):
    """One Vision word box, normalized page fractions (origin top-left)."""
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


# --- Kaiker: fraction code must never be the date ---------------------------

KAIKER = """\
KAIKER DEVELOPMENT LLC
4837
24-22/1230
11/03/2026
PAY TO THE ORDER OF Green Valley Contractors
$5,150.00
\u2408004837\u2408 \u2406021000021\u2406 {account}
""".format(account=ACCOUNT)

# Same check with the printed number absent from the OCR text: the MICR
# group (zero-padded 004837) is all the reader has.
KAIKER_MICR_ONLY = """\
KAIKER DEVELOPMENT LLC
24-22/1230
11/03/2026
PAY TO THE ORDER OF Green Valley Contractors
$5,150.00
\u2408004837\u2408 \u2406021000021\u2406 {account}
""".format(account=ACCOUNT)

# The printed number as Vision actually emits it at check print sizes:
# the tiny "No." label merged into the number word.
KAIKER_WORDS = [
    W("KAIKER", 0.03, 0.03, 0.12, 0.09), W("DEVELOPMENT", 0.13, 0.03, 0.28, 0.09),
    W("LLC", 0.29, 0.03, 0.34, 0.09),
    W("No.4837", 0.84, 0.02, 0.96, 0.09),
    W("24-22/1230", 0.80, 0.12, 0.96, 0.18),
    W("11/03/2026", 0.80, 0.20, 0.96, 0.26),
    W("\u2408004837\u2408", 0.05, 0.90, 0.16, 0.96),
    W("\u2406021000021\u2406", 0.20, 0.90, 0.34, 0.96),
    W("1234567890", 0.38, 0.90, 0.52, 0.96),
]

FRACTION_ONLY = """\
ACME BUILDERS LLC
7781
24-22/1230
PAY TO THE ORDER OF Green Valley Contractors
$900.00
\u24087781\u2408 \u2406021000021\u2406 {account}
""".format(account=ACCOUNT)


def test_fraction_is_never_the_date() -> None:
    check("fraction alone is not a date", find_date("24-22/1230") is None)
    check("real date beats the earlier fraction",
          find_date("24-22/1230\n11/03/2026") == "11/03/2026")
    p = parse_check_ocr(KAIKER)
    check("Kaiker: date is the real date, not the fraction",
          p.date == "11/03/2026")
    check("Kaiker: check number is 4837", p.check_no == "4837")


def test_fraction_only_date_comes_out_empty() -> None:
    p = parse_check_ocr(FRACTION_ONLY)
    check("fraction-only check: date is None, never the fraction",
          p.date is None)


# --- Leading zeros are cosmetic ---------------------------------------------

def test_micr_leading_zeros_are_stripped() -> None:
    p = parse_check_ocr(KAIKER_MICR_ONLY)
    check("MICR-only: printed number absent", p.check_no_printed is None)
    check("MICR-only: check number stored unpadded", p.check_no == "4837")
    check("MICR-only: micr read stored unpadded", p.check_no_micr == "4837")
    check("MICR-only: date still the real date", p.date == "11/03/2026")


ZICKA = """\
ZICKA HOMES LLC
14145
November 21, 2026
PAY TO THE ORDER OF Green Valley Contractors
$3,275.00
\u2408014145\u2408 \u2406021000021\u2406 {account}
""".format(account=ACCOUNT)


def test_leading_zero_micr_is_not_a_conflict() -> None:
    p = parse_check_ocr(ZICKA)
    check("Zicka: check number is 14145", p.check_no == "14145")
    check("Zicka: micr read canonicalized to 14145", p.check_no_micr == "14145")
    check("Zicka: NO false mismatch warning", p.check_no_conflict is False)


def test_label_merged_word_is_the_printed_number() -> None:
    p = parse_check_ocr(KAIKER_MICR_ONLY, words=KAIKER_WORDS)
    check("label-merged word: printed number found", p.check_no_printed == "4837")
    check("label-merged word: check number is 4837", p.check_no == "4837")
    check("label-merged word: agrees with MICR, no conflict",
          p.check_no_conflict is False)


def test_label_forms() -> None:
    check("label form 'No. 4837'", find_check_number("No. 4837") == "4837")
    check("label form 'Check # 4837'",
          find_check_number("Check # 4837") == "4837")
    check("label form '#4837'", find_check_number("#4837") == "4837")


# --- Top-right beats address tokens, always ---------------------------------

JED = """\
JED BARN BUILDERS LLC
456 State Route 123
1056
November 20, 2026
PAY TO THE ORDER OF Green Valley Contractors
$2,300.00
MEMO Jed barn
\u24081056\u2408 \u2406021000021\u2406 {account}
""".format(account=ACCOUNT)

JED_WORDS = [
    W("JED", 0.03, 0.03, 0.07, 0.09), W("BARN", 0.08, 0.03, 0.14, 0.09),
    W("BUILDERS", 0.15, 0.03, 0.25, 0.09), W("LLC", 0.26, 0.03, 0.31, 0.09),
    W("No.1056", 0.86, 0.02, 0.96, 0.09),
    W("456", 0.03, 0.12, 0.07, 0.18), W("State", 0.08, 0.12, 0.14, 0.18),
    W("Route", 0.15, 0.12, 0.21, 0.18), W("123", 0.22, 0.12, 0.26, 0.18),
    W("\u24081056\u2408", 0.05, 0.90, 0.14, 0.96),
    W("\u2406021000021\u2406", 0.18, 0.90, 0.32, 0.96),
    W("1234567890", 0.36, 0.90, 0.50, 0.96),
]

# The address words WITHOUT any printed-number word: geometry must find
# no candidate at all (the text/MICR path then supplies 1056) -- the
# address token must not win just because it is in the top band.
JED_WORDS_NO_PRINTED = [w for w in JED_WORDS if w["text"] != "No.1056"]


def test_state_route_number_is_not_the_check_number() -> None:
    p = parse_check_ocr(JED)
    check("Jed (text): check number is 1056", p.check_no == "1056")
    check("Jed (text): printed number is 1056", p.check_no_printed == "1056")
    check("Jed (text): route number never recorded", p.check_no != "123")


def test_state_route_words_top_right_wins() -> None:
    p = parse_check_ocr(JED, words=JED_WORDS)
    check("Jed (words): check number is 1056", p.check_no == "1056")
    check("Jed (words): route number never recorded", p.check_no != "123")
    check("Jed (words): not flagged ambiguous", p.check_no_ambiguous is False)


def test_address_word_alone_in_top_band_never_wins() -> None:
    p = parse_check_ocr(JED, words=JED_WORDS_NO_PRINTED)
    check("Jed (words, no printed word): route number never recorded",
          p.check_no != "123")
    check("Jed (words, no printed word): text path supplies 1056",
          p.check_no == "1056")


if __name__ == "__main__":
    test_fraction_is_never_the_date()
    test_fraction_only_date_comes_out_empty()
    test_micr_leading_zeros_are_stripped()
    test_leading_zero_micr_is_not_a_conflict()
    test_label_merged_word_is_the_printed_number()
    test_label_forms()
    test_state_route_number_is_not_the_check_number()
    test_state_route_words_top_right_wins()
    test_address_word_alone_in_top_band_never_wins()
    print("test_check_parse_round3: all checks passed")
