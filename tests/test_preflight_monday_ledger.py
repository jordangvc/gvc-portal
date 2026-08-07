"""preflight_stripe Monday-ledger fallback when Stripe Search lags."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import stripe_invoice as si  # noqa: E402
from adapters.monday.client import (  # noqa: E402
    INV_COL_DOCUMENT,
    INV_COL_STRIPE_INVOICE,
    MondayClient,
)


def _enriched(identifier: str = "INV-2026-0807-001") -> dict:
    return {
        "client": {"email": "a@x.com", "name": "Acme", "billing_address": ""},
        "invoice": {"identifier": identifier},
    }


class _FakeInvoice:
    def __init__(self, inv_id: str, *, customer: str = "cus_old",
                 zombie: bool = False):
        self.id = inv_id
        self.status = "open"
        self.hosted_invoice_url = f"https://pay.stripe.test/{inv_id}"
        self.amount_due = 10000
        self.customer = customer
        self.metadata = SimpleNamespace(
            gvc_invoice_id="INV-2026-0807-001",
            gvc_status="zombie_replaced" if zombie else None,
        )


def test_preflight_uses_monday_ledger_when_search_misses(monkeypatch):
    monkeypatch.setattr(
        si.stripe.Customer, "list",
        lambda **_kw: SimpleNamespace(data=[]),
    )
    monkeypatch.setattr(si, "_find_invoice_by_identifier_metadata", lambda _id: None)

    class FakeMC:
        def find_invoice_row_by_document(self, identifier, board_id=None):  # noqa: ARG002
            assert identifier == "INV-2026-0807-001"
            return {
                "item_id": 99,
                "name": "Acme job",
                "item_url": "https://monday.test/99",
                "stripe_invoice_id": "in_ledger_1",
            }

    import adapters.monday.client as monday_client
    monkeypatch.setattr(monday_client, "MondayClient", FakeMC)
    monkeypatch.setattr(
        si.stripe.Invoice, "retrieve",
        lambda sid: _FakeInvoice(sid, customer="cus_old"),
    )

    report = si.preflight_stripe(_enriched())
    assert report["existing_invoice_with_identifier"]["id"] == "in_ledger_1"
    assert report["existing_invoice_source"] == "monday_ledger"
    assert report["existing_invoice_email_mismatch"] is True
    assert report["existing_invoice_with_identifier"]["monday_item_id"] == 99


def test_find_invoice_via_monday_ledger_skips_empty_stripe_id(monkeypatch):
    import adapters.monday.client as monday_client

    class FakeMC:
        def find_invoice_row_by_document(self, identifier, board_id=None):  # noqa: ARG002
            return {"item_id": 1, "name": "x", "stripe_invoice_id": None}

    monkeypatch.setattr(monday_client, "MondayClient", FakeMC)
    assert si._find_invoice_via_monday_ledger("INV-2026-0807-001") is None


def test_find_invoice_via_monday_ledger_skips_zombie(monkeypatch):
    import adapters.monday.client as monday_client

    class FakeMC:
        def find_invoice_row_by_document(self, identifier, board_id=None):  # noqa: ARG002
            return {"item_id": 1, "name": "x", "stripe_invoice_id": "in_z"}

    monkeypatch.setattr(monday_client, "MondayClient", FakeMC)
    monkeypatch.setattr(
        si.stripe.Invoice, "retrieve",
        lambda sid: _FakeInvoice(sid, zombie=True),
    )
    assert si._find_invoice_via_monday_ledger("INV-2026-0807-001") is None


def test_find_invoice_row_by_document_returns_stripe_id():
    class FakeMC(MondayClient):
        def __init__(self):
            pass

        def _query(self, query, variables=None):  # noqa: ARG002
            return {
                "boards": [{
                    "items_page": {
                        "items": [{
                            "id": "55",
                            "name": "Job",
                            "column_values": [
                                {"id": INV_COL_DOCUMENT, "text": "INV-2026-0807-001"},
                                {"id": INV_COL_STRIPE_INVOICE, "text": "in_abc"},
                            ],
                        }],
                    },
                }],
            }

    row = FakeMC().find_invoice_row_by_document("INV-2026-0807-001")
    assert row["item_id"] == 55
    assert row["stripe_invoice_id"] == "in_abc"
