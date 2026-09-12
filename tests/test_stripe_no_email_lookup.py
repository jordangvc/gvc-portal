"""
Regression: a no-email customer (print / mail / hand-deliver) has NO `email`
key. preflight_stripe and upsert_stripe_customer did client["email"] → KeyError
→ 500 (Andrea, INV-2026-0824-003, Sep 11 2026). Both must resolve the same
synthetic .invalid address the live create path uses.

Self-running and pytest-compatible. Stripe network calls are stubbed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import adapters.stripe_invoice as si  # noqa: E402

NO_EMAIL_CLIENT = {"name": "Krista Jeffries", "contact_name": "Krista Jeffries",
                   "no_email": True, "delivery_method": "print"}


def test_no_email_client_gets_synthetic_address():
    e = si._customer_lookup_email(NO_EMAIL_CLIENT)
    assert e.startswith("no-email.krista-jeffries@"), e
    assert e.endswith(".invalid"), e


def test_normal_client_email_is_used_verbatim():
    assert si._customer_lookup_email({"name": "X", "email": "  a@b.com "}) == "a@b.com"


def test_preflight_no_email_client_does_not_keyerror():
    class _Empty:
        data = []

    saved = si.stripe.Customer.list
    si.stripe.Customer.list = staticmethod(lambda **kw: _Empty())
    try:
        report = si.preflight_stripe({
            "client": dict(NO_EMAIL_CLIENT),
            "invoice": {"identifier": "INV-2026-0824-003"},
        })
    finally:
        si.stripe.Customer.list = saved
    assert report["customer"]["action"] == "would_create", report
    assert report["customer"]["email"].endswith(".invalid"), report
    assert report["existing_invoice_with_identifier"] is None


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
