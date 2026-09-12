"""
Regression: the sent-watcher must search Gmail for the phrase that is actually
in the subject. Bid Board stores Estimate # as the bare core (numbers column),
the outbound subject carries EST-; searching the bare phrase matched nothing
(62 estimates "not sent" every sweep, Sep 2026).

Self-running (python tests/test_sent_watch_needles.py) and pytest-compatible.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrators.sent_watch_flow import (  # noqa: E402
    estimate_subject_needle, invoice_subject_needle)


def test_bare_core_gets_est_prefix():
    assert estimate_subject_needle("2026-0831-001") == "Estimate EST-2026-0831-001"


def test_already_prefixed_unchanged():
    assert estimate_subject_needle("EST-2026-0831-001") == "Estimate EST-2026-0831-001"


def test_pro_or_inv_prefix_is_rewritten_to_est():
    # Same spine number, estimate context → EST- is the subject form.
    assert estimate_subject_needle("PRO-2026-0831-001") == "Estimate EST-2026-0831-001"


def test_rev_suffix_preserved():
    assert estimate_subject_needle("2026-0831-001 Rev 2") == "Estimate EST-2026-0831-001 Rev 2"


def test_legacy_number_passes_through():
    assert estimate_subject_needle("C-005") == "Estimate C-005"


def test_invoice_needle_untouched():
    # Invoice Document # is written exactly as the subject uses it.
    assert invoice_subject_needle("2026-0902-01") == "Invoice 2026-0902-01"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"OK — {len(tests)} tests passed.")
