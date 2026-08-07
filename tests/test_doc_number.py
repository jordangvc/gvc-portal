"""
Unified EST-/PRO-/INV- document spine.
Self-running:  python tests/test_doc_number.py
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.doc_number import (  # noqa: E402
    core_number,
    for_estimate,
    for_invoice,
    for_project,
    is_spine_number,
    monday_estimate_cell,
    search_needles,
    with_prefix,
)
from subsystems.estimate.number import (  # noqa: E402
    ESTIMATE_NUMBER_RE,
    format_number,
    next_estimate_number,
    normalize_estimate_identifier,
    parse_counter,
)
from subsystems.invoice.model import ensure_invoice_identifier, validate  # noqa: E402
from orchestrators.change_order_flow import normalize_base_number  # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} {extra}".strip())



def main() -> int:
    global PASS, FAIL
    PASS = 0
    FAIL = []
    # --- core / prefix -----------------------------------------------------------

    check("core from bare", core_number("2026-0804-001") == "2026-0804-001")
    check("core from EST-", core_number("EST-2026-0804-001") == "2026-0804-001")
    check("core from PRO-", core_number("pro-2026-0804-001") == "2026-0804-001")
    check("core from INV- Rev", core_number("INV-2026-0804-001 Rev 2") == "2026-0804-001")
    check("legacy C-005 is not spine", core_number("C-005") is None)
    check("is_spine bare", is_spine_number("2026-0804-001"))
    check("for_estimate", for_estimate("2026-0804-001") == "EST-2026-0804-001")
    check("for_project", for_project("EST-2026-0804-001") == "PRO-2026-0804-001")
    check("for_invoice", for_invoice("PRO-2026-0804-001") == "INV-2026-0804-001")
    check("for_invoice keeps Rev",
          for_invoice("2026-0804-001 Rev 1") == "INV-2026-0804-001 Rev 1")
    check("legacy passthrough", for_invoice("GVC-2026-C-005") == "GVC-2026-C-005")
    check("monday cell is bare",
          monday_estimate_cell("EST-2026-0804-001") == "2026-0804-001")
    check("search needles EST- → typed + core",
          search_needles("EST-2026-0804-007") == ["EST-2026-0804-007", "2026-0804-007"])
    check("search needles PRO- → typed + core",
          search_needles("PRO-2026-0804-007") == ["PRO-2026-0804-007", "2026-0804-007"])
    check("search needles bare is single",
          search_needles("2026-0804-007") == ["2026-0804-007"])
    check("search needles empty", search_needles("  ") == [])
    check("search needles legacy passthrough",
          search_needles("C-005") == ["C-005"])
    check("same core across kinds",
          core_number(for_estimate("2026-0804-007"))
          == core_number(for_project("2026-0804-007"))
          == core_number(for_invoice("2026-0804-007"))
          == "2026-0804-007")

    # Invoice Find-the-Project must recognize prefixed spine numbers (r26 gap).
    _invoice_html = (Path(__file__).resolve().parents[1] / "web" / "invoice.html").read_text(
        encoding="utf-8")
    check("invoice looksLike accepts EST|PRO|INV",
          "EST|PRO|INV" in _invoice_html and "looksLikeProjectNumber" in _invoice_html)

    # INV- autofill prefers project_number; bare estimate spine is enough when
    # Project # is missing (Look-up-&-fill path).
    check("INV from bid estimate spine",
          for_invoice("2026-0804-009") == "INV-2026-0804-009")
    check("INV from EST- spine",
          for_invoice("EST-2026-0804-009") == "INV-2026-0804-009")

    # --- estimate number ---------------------------------------------------------

    check("format_number is EST-",
          format_number(date(2026, 8, 4), 3) == "EST-2026-0804-003")
    check("parse_counter EST-",
          parse_counter("EST-2026-0804-003", prefix="2026-0804") == 3)
    check("parse_counter bare",
          parse_counter("2026-0804-003", prefix="2026-0804") == 3)
    check("normalize bare→EST",
          normalize_estimate_identifier("2026-0804-001") == "EST-2026-0804-001")
    check("ESTIMATE_NUMBER_RE accepts EST-",
          bool(ESTIMATE_NUMBER_RE.match("EST-2026-0804-001")))
    check("ESTIMATE_NUMBER_RE accepts bare",
          bool(ESTIMATE_NUMBER_RE.match("2026-0804-001")))

    tmp = Path("/tmp/gvc-doc-number-test-out")
    tmp.mkdir(exist_ok=True)
    for p in tmp.glob("*.pdf"):
        p.unlink()
    (tmp / "EST-2026-0804-001.pdf").write_bytes(b"%PDF")
    (tmp / "2026-0804-002.pdf").write_bytes(b"%PDF")
    nxt = next_estimate_number(output_dir=tmp, today=date(2026, 8, 4))
    check("next_estimate_number bumps past EST+bare",
          nxt == "EST-2026-0804-003", nxt)

    # --- invoice auto-assign -----------------------------------------------------

    payload = {
        "client": {"name": "Acme", "billing_address": "1 Main",
                   "email": "a@example.com"},
        "job": {"name": "Job", "project_number": "PRO-2026-0804-009"},
        "invoice": {
            "issue_date": "2026-08-04",
            "payment_terms": "Net 30",
            "line_items": [{"description": "Work", "amount": 100}],
        },
    }
    ensure_invoice_identifier(payload)
    check("invoice auto INV- from PRO-",
          payload["invoice"]["identifier"] == "INV-2026-0804-009",
          payload["invoice"].get("identifier"))

    payload2 = {
        "client": {"name": "Acme", "billing_address": "1 Main",
                   "email": "a@example.com"},
        "job": {"name": "Job", "project_number": "PRO-2026-0804-009"},
        "invoice": {
            "identifier": "2026-0804-009",
            "issue_date": "2026-08-04",
            "payment_terms": "Net 30",
            "line_items": [{"description": "Work", "amount": 100}],
        },
    }
    validate(payload2)
    check("validate normalizes bare id to INV-",
          payload2["invoice"]["identifier"] == "INV-2026-0804-009")

    # --- CO base strips prefixes -------------------------------------------------

    check("CO base strips EST-",
          normalize_base_number("EST-2026-0804-001") == "2026-0804-001")
    check("CO base strips CO wrapper then prefix",
          normalize_base_number("CO.2-PRO-2026-0804-001") == "2026-0804-001")
    check("CO base keeps legacy",
          normalize_base_number("C-005") == "C-005")
    check("with_prefix unknown kind passthrough",
          with_prefix("XYZ", "2026-0804-001") == "2026-0804-001")


    print(f"\n{PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("FAIL:", f)
    return 1 if FAIL else 0


def test_doc_number_standalone():
    """Pytest entry — keeps this file collectable and CI-gated."""
    code = main()
    assert code == 0, FAIL


if __name__ == "__main__":
    raise SystemExit(main())
