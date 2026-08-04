"""shared/recipients — multi-email + no-email delivery (pure)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import recipients as R  # noqa: E402


def test_parse_email_list_mixed_separators():
    got = R.parse_email_list("a@x.com, b@y.com; c@z.com\nd@w.com")
    assert got == ["a@x.com", "b@y.com", "c@z.com", "d@w.com"]


def test_parse_email_list_dedupes_and_drops_junk():
    got = R.parse_email_list(["A@x.com", "a@x.com", "not-an-email", "b@y.com"])
    assert got == ["A@x.com", "b@y.com"]


def test_multi_email_normalize():
    rec = R.normalize_client_recipients({
        "name": "Acme",
        "emails": ["one@acme.com", "two@acme.com"],
        "cc_emails": "office@acme.com",
    })
    assert rec["no_email"] is False
    assert rec["to_header"] == "one@acme.com, two@acme.com"
    assert rec["cc_header"] == "office@acme.com"
    assert rec["stripe_email"] == "one@acme.com"
    assert rec["office_notice"] is None


def test_legacy_single_email_string_with_commas():
    rec = R.normalize_client_recipients({
        "name": "Acme",
        "email": "a@x.com, b@x.com",
    })
    assert rec["customer_emails"] == ["a@x.com", "b@x.com"]
    assert rec["to_emails"] == ["a@x.com", "b@x.com"]


def test_no_email_routes_draft_to_office():
    rec = R.normalize_client_recipients(
        {"name": "Pat Elderly", "no_email": True, "delivery_method": "print"},
        office_fallback="andrea@greenvalleycontractors.com",
    )
    assert rec["no_email"] is True
    assert rec["to_emails"] == ["andrea@greenvalleycontractors.com"]
    assert rec["customer_emails"] == []
    assert "no-email.pat-elderly@" in rec["stripe_email"]
    assert rec["stripe_email"].endswith(".invalid")
    assert "NO EMAIL" in (rec["office_notice"] or "").upper()


def test_validate_requires_email_or_no_email_flag():
    try:
        R.validate_client_recipients({"name": "X"})
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "no email" in str(e).lower() or "client.email" in str(e)

    R.validate_client_recipients({"name": "X", "email": "a@b.com"})
    R.validate_client_recipients({"name": "X", "no_email": True, "delivery_method": "mail"})


def test_prepend_office_notice():
    assert R.prepend_office_notice("Hi", "NOTE").startswith("NOTE\n\nHi")
    assert R.prepend_office_notice("Hi", None) == "Hi"


def _run_all():
    tests = [
        test_parse_email_list_mixed_separators,
        test_parse_email_list_dedupes_and_drops_junk,
        test_multi_email_normalize,
        test_legacy_single_email_string_with_commas,
        test_no_email_routes_draft_to_office,
        test_validate_requires_email_or_no_email_flag,
        test_prepend_office_notice,
    ]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append((fn.__name__, e))
            print(f" FAIL {fn.__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
