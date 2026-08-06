"""Orphan Stripe draft reuse — creation-side safety net.

A live create that fails after Invoice.create but before finalize leaves a
draft. The next run must finalize-and-reuse that draft instead of creating a
duplicate (which also trips identifier-scoped idempotency).

Runs under pytest OR: python tests/test_orphan_draft_reuse.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.stripe_invoice import (  # noqa: E402
    finalize_draft_invoice,
    is_reusable_stripe_status,
)


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_reusable_statuses() -> None:
    for s in ("open", "paid", "draft", "OPEN", " Draft "):
        check(f"reusable:{s!r}", is_reusable_stripe_status(s) is True)
    for s in (None, "", "void", "uncollectible", "deleted"):
        check(f"not-reusable:{s!r}", is_reusable_stripe_status(s) is False)


def test_finalize_draft_invoice_shapes_result() -> None:
    fake = SimpleNamespace(
        id="in_orphan",
        status="open",
        hosted_invoice_url="https://invoice.stripe.com/i/test",
        amount_due=1200,
        customer="cus_1",
    )
    with patch("adapters.stripe_invoice.stripe.Invoice.finalize_invoice",
               return_value=fake) as m:
        out = finalize_draft_invoice("in_orphan")
    m.assert_called_once_with("in_orphan")
    check("id", out["id"] == "in_orphan")
    check("status open", out["status"] == "open")
    check("hosted url", out["hosted_invoice_url"].startswith("https://"))
    check("customer", out["customer"] == "cus_1")


def test_finalize_draft_invoice_requires_id() -> None:
    try:
        finalize_draft_invoice("")
        raise AssertionError("expected ValueError")
    except ValueError:
        check("empty id raises", True)


def test_invoice_flow_imports_helpers() -> None:
    from orchestrators import invoice_flow as flow
    check("helper re-exported path usable",
          flow.is_reusable_stripe_status("draft") is True)
    check("finalize symbol present", callable(flow.finalize_draft_invoice))


if __name__ == "__main__":
    test_reusable_statuses()
    test_finalize_draft_invoice_shapes_result()
    test_finalize_draft_invoice_requires_id()
    test_invoice_flow_imports_helpers()
    print("ALL PASSED")
