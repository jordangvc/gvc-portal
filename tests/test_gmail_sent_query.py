"""
Regression: the sent-watcher's Gmail search must not count the internal
"[NO EMAIL — PRINT]" office copy as a client send (Sep 12 2026: a print-only
estimate got stamped "emailed" from its office copy).
Self-running and pytest-compatible; no network.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.gmail import sent_search_query  # noqa: E402


def test_query_searches_sent_by_subject_phrase():
    q = sent_search_query("Estimate EST-2026-0831-001", 60)
    assert q.startswith('in:sent subject:"Estimate EST-2026-0831-001" ')
    assert q.endswith("newer_than:60d")


def test_query_excludes_office_copies():
    assert '-subject:"NO EMAIL"' in sent_search_query("Invoice 2026-0910-01")


if __name__ == "__main__":
    failed = 0
    for t in (test_query_searches_sent_by_subject_phrase, test_query_excludes_office_copies):
        try:
            t()
        except AssertionError as e:
            failed += 1; print(f"FAIL {t.__name__}: {e}")
    sys.exit(1 if failed else print("OK — 2 tests passed.") or 0)
