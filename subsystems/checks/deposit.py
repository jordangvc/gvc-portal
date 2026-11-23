"""
Paid-by-Check — pure parsing + matching core.
=========================================================================
The two pieces that must be *correct* and are fully unit-testable without
touching Google Vision, Stripe, Monday, or Drive:

  - parse_check_ocr(text)  — turn raw OCR text into best-guess fields. Tuned
                             against a real GVC customer check (Danis Builders).
                             Every field is a guess the human confirms/edits in
                             the modal; OCR is a head start, not the truth.
  - match_invoice(check, open_rows) — pick the one open invoice a check pays,
                             grounded in the "Invoices Sent" board schema.

The live path (Vision call lives in vision.py; Stripe paid_out_of_band, Monday
Status→Paid, Drive upload) is a 3-stage gate that mutates money state. Per the original design:
the already-deposited check is caught AFTER the confirm modal, when we pull the
matched invoice's status from Stripe. See docs/portal-check-deposit-design.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

# A money token like 1,234.56 / 110,610.00 / 750.00 (optionally $-prefixed).
_MONEY_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})\b")
# Numeric dates: 6/15/2026, 06-02-26, 2026-06-15.
_NUM_DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
# Textual dates: Jun 2, 2026 / June 2 2026.
_MONTH_DATE_RE = re.compile(
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
    re.I,
)
# A bank routing fraction ("24-22/1230"): digit groups joined by BOTH a
# dash and a slash. It is printed at the top-right of the check, it matches
# the numeric-date pattern above, and it was saved as the check date
# (office report, July 2026; live retest 2026-11-23). It is never a date.
_FRACTION_RE = re.compile(r"\b\d{1,2}\s*-\s*\d{1,2}\s*/\s*\d{2,4}\b")
# Check-number label (require >=4 digits so the routing fraction "73-27/421"
# can't masquerade as the check number).
_CHECK_NO_LABEL_RE = re.compile(
    r"(?:check\s*(?:no|number|#)|#|(?<!form )\bno\.)\s*[:.]?\s*(\d{4,10})", re.I
)
# A maximal chain of digit groups joined by - or / : a phone number
# ("513-800-3376"), a routing fraction ("73-27/421"), a ZIP+4, a numeric
# date. EVERY group in the chain is part of that larger number — the old
# scrub removed only the first two groups of a chain, so the tail of a
# phone number ("...-3376") survived and was saved as a check number
# (office live test, 2026-11-23).
_DIGIT_CHAIN_RE = re.compile(r"\d+(?:\s*[-/]\s*\d+)+")
# Phone numbers with space/dot separators ("513 800 3376").
_PHONE_RE = re.compile(r"\b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b")
# ZIP in an address: the 5 digits right after a "City, ST" state token.
_STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+(\d{5})(?:-\d{4})?\b")
# A standalone check-number candidate.
_CHECK_NO_TOKEN_RE = re.compile(r"\b(\d{3,10})\b")
# Lines whose numbers belong to an address or a phone label, never to the
# check number (a street number / PO Box number is not a check number).
# "route"/"rte"/"sr" matter: "State Route 123" contains no older keyword
# ("State" is not "st"), and its 123 was saved as a check number
# (office live retest, 2026-11-23).
_ADDRESS_LINE_RE = re.compile(
    r"\b(st|street|ave|avenue|blvd|rd|road|route|rte|sr|dr|drive|ln|lane|pkwy|parkway|"
    r"ct|court|hwy|highway|box|suite|ste|trl|trail|cir|circle)\b", re.I)
_PHONE_LABEL_RE = re.compile(r"\b(tel|telephone|phone|fax)\b", re.I)
# MICR E-13B symbols Vision emits around the bottom number line: the OCR
# block glyphs (U+2446-U+2449) and the Control Pictures variants
# (U+2406-U+2409). Both encodings occur in OCR output, and a MICR band in
# either must be recognized: an unrecognized MICR line leaks into the
# printed-number candidates and its check-number group gets recorded as
# the printed number (deploy-gate failures, 2026-11-23). Built with chr()
# so no look-alike glyph literals sit in the source.
_MICR_SYMBOLS = "".join(chr(c) for c in (0x2446, 0x2447, 0x2448, 0x2449,
                                        0x2406, 0x2407, 0x2408, 0x2409))
# Company-ish suffixes used to find the payer (remitter) line.
_COMPANY_RE = re.compile(
    r"\b(L\.?L\.?C\.?|INC\.?|INCORPORATED|LTD\.?|CO\.?|CORP\.?|COMPANY|BUILDERS?|"
    r"CONSTRUCTION|CONTRACTORS?|HOMES?|DEVELOPMENT|GROUP|PARTNERS|ENTERPRISES?)\b",
    re.I,
)
# Lines that are never the payer (security strip / labels / bank / boilerplate).
_BOILERPLATE = (
    "WATERMARK", "HOLD AT AN ANGLE", "DO NOT CASH", "VOID IN", "PAYMENT REFERENCE",
    "CHECK NO", "CHECK NUMBER", "DATE OF CHECK", "CHECK AMOUNT", "AUTHORIZED SIGNATURE",
    "MEMO", "PAY:", "PAY ", "TO THE ORDER OF", "FORM NO",
)


def to_cents(value) -> Optional[int]:
    """Coerce a money-ish value to integer cents, or None if unparseable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100
    try:
        d = Decimal(str(value).replace(",", "").replace("$", "").strip())
        return int((d * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def find_money(text: str) -> list[Decimal]:
    """Money amounts in document order, deduped preserving order."""
    out: list[Decimal] = []
    for whole, cents in _MONEY_RE.findall(text or ""):
        try:
            amt = Decimal(f"{whole.replace(chr(44), chr(39))}.{cents}")
        except InvalidOperation:
            continue
        if amt not in out:
            out.append(amt)
    return out
