"""Stripe `invoice.paid` -> Monday Paid webhook (v1 slice).

Two layers, both offline / no network:
  - handle_stripe_event(): the orchestrator, tested against a fake
    MondayClient (no Monday, no Stripe SDK calls in this module at all).
  - POST /v1/webhooks/stripe: the FastAPI route, tested with a REAL
    Stripe-Signature header computed locally with hmac/sha256 — Stripe's own
    signature check (stripe.Webhook.construct_event) is pure local HMAC
    verification, no network I/O, so this exercises the real auth path
    instead of mocking it away.

Runs under pytest OR directly: python tests/test_stripe_paid_webhook.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Optional
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrators import stripe_paid_flow  # noqa: E402

WEBHOOK_SECRET = "whsec_test_only_not_a_real_secret"


# ---------------------------------------------------------------------------
# Fakes + fixtures
# ---------------------------------------------------------------------------

class _FakeMondayClient:
    """Minimal stand-in for MondayClient — just the two methods the
    orchestrator touches. No network."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.paid_calls: list[dict] = []

    def fetch_invoice_rows(self, *, open_only: bool = True, board_id=None) -> list[dict]:
        if open_only:
            return [r for r in self._rows if (r.get("status") or "").strip().lower() != "paid"]
        return list(self._rows)

    def set_invoice_paid(self, item_id, *, check_no=None, date_str=None,
                         board_id=None, covers=None, note_line=None) -> dict:
        self.paid_calls.append({"item_id": item_id, "note_line": note_line})
        for row in self._rows:
            if row["monday_item_id"] == item_id:
                row["status"] = "Paid"
        return {"item_id": item_id, "status": "Paid", "note_appended": True}


def _row(item_id: int = 501, identifier: str = "INV-2026-0804-001",
         status: str = "Invoice Sent", stripe_invoice_id: Optional[str] = "in_123") -> dict:
    return {"monday_item_id": item_id, "identifier": identifier, "status": status,
            "stripe_invoice_id": stripe_invoice_id, "customer": "Acme", "job": "Acme Job",
            "amount": 1500.0}


def _invoice_paid_event(*, identifier: Optional[str] = "INV-2026-0804-001",
                        stripe_invoice_id: str = "in_123",
                        amount_paid: int = 150000, event_id: str = "evt_1") -> dict:
    metadata = {"gvc_invoice_id": identifier} if identifier else {}
    return {
        "id": event_id,
        "object": "event",
        "type": "invoice.paid",
        "data": {"object": {
            "id": stripe_invoice_id,
            "object": "invoice",
            "amount_paid": amount_paid,
            "metadata": metadata,
        }},
    }


# ---------------------------------------------------------------------------
# handle_stripe_event — orchestrator, no HTTP involved
# ---------------------------------------------------------------------------

def test_ignores_non_invoice_paid_events():
    with patch.object(stripe_paid_flow, "MondayClient") as MC:
        result = stripe_paid_flow.handle_stripe_event(
            {"type": "invoice.payment_failed", "data": {"object": {"id": "in_1"}}})
    assert result["ok"] is True
    assert result["handled"] is False
    assert "invoice.payment_failed" in result["reason"]
    MC.assert_not_called()


def test_marks_monday_row_paid_by_document_number():
    row = _row()
    mc = _FakeMondayClient([row])
    with patch.object(stripe_paid_flow, "MondayClient", return_value=mc):
        result = stripe_paid_flow.handle_stripe_event(_invoice_paid_event())

    assert result["ok"] is True
    assert result["handled"] is True
    assert result["already_paid"] is False
    assert result["item_id"] == row["monday_item_id"]
    assert len(mc.paid_calls) == 1
    note = mc.paid_calls[0]["note_line"]
    assert note.startswith("Paid via Stripe online on ")
    assert row["status"] == "Paid"


def test_falls_back_to_stripe_invoice_id_when_document_number_differs():
    # Document # on the row doesn't match the metadata (e.g. a legacy row) —
    # the fallback stripe_invoice_id scan should still find it.
    row = _row(identifier="INV-LEGACY-000", stripe_invoice_id="in_123")
    mc = _FakeMondayClient([row])
    with patch.object(stripe_paid_flow, "MondayClient", return_value=mc):
        result = stripe_paid_flow.handle_stripe_event(
            _invoice_paid_event(identifier="INV-2026-0804-001", stripe_invoice_id="in_123"))

    assert result["handled"] is True
    assert result["item_id"] == row["monday_item_id"]
    assert len(mc.paid_calls) == 1


def test_already_paid_is_an_idempotent_noop():
    row = _row(status="Paid")
    mc = _FakeMondayClient([row])
    with patch.object(stripe_paid_flow, "MondayClient", return_value=mc):
        result = stripe_paid_flow.handle_stripe_event(_invoice_paid_event())

    assert result["ok"] is True
    assert result["handled"] is True
    assert result["already_paid"] is True
    assert result["item_id"] == row["monday_item_id"]
    assert mc.paid_calls == []  # no Monday write on the no-op path


def test_no_matching_row_is_a_graceful_noop_not_an_error():
    mc = _FakeMondayClient([])
    with patch.object(stripe_paid_flow, "MondayClient", return_value=mc):
        result = stripe_paid_flow.handle_stripe_event(_invoice_paid_event())

    assert result["ok"] is True
    assert result["handled"] is False
    assert "no matching" in result["reason"]
    assert mc.paid_calls == []


def test_missing_metadata_and_id_is_a_graceful_noop():
    event = {"id": "evt_x", "type": "invoice.paid",
             "data": {"object": {"metadata": {}, "amount_paid": 100}}}
    with patch.object(stripe_paid_flow, "MondayClient") as MC:
        result = stripe_paid_flow.handle_stripe_event(event)
    assert result["ok"] is True
    assert result["handled"] is False
    MC.assert_not_called()


# ---------------------------------------------------------------------------
# POST /v1/webhooks/stripe — the FastAPI route
# ---------------------------------------------------------------------------

def _sign(payload: bytes, secret: str, *, timestamp: Optional[int] = None) -> str:
    """Build a real Stripe-Signature header value the same way Stripe does —
    pure local HMAC-SHA256, no network involved."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.{payload.decode('utf-8')}"
    sig = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _test_client(monkeypatch, *, secret: Optional[str] = WEBHOOK_SECRET):
    if secret is None:
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    else:
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    from fastapi.testclient import TestClient
    from app.service import app
    return TestClient(app)


def test_webhook_bad_signature_returns_400(monkeypatch):
    client = _test_client(monkeypatch)
    payload = json.dumps(_invoice_paid_event()).encode("utf-8")
    bad_header = _sign(payload, "not_the_real_secret")
    resp = client.post("/v1/webhooks/stripe", content=payload,
                       headers={"stripe-signature": bad_header})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "BAD_SIGNATURE"


def test_webhook_missing_signature_header_returns_400(monkeypatch):
    client = _test_client(monkeypatch)
    payload = json.dumps(_invoice_paid_event()).encode("utf-8")
    resp = client.post("/v1/webhooks/stripe", content=payload)
    assert resp.status_code == 400


def test_webhook_not_configured_returns_503(monkeypatch):
    client = _test_client(monkeypatch, secret=None)
    payload = json.dumps(_invoice_paid_event()).encode("utf-8")
    resp = client.post("/v1/webhooks/stripe", content=payload,
                       headers={"stripe-signature": "t=1,v1=deadbeef"})
    assert resp.status_code == 503


def test_webhook_wrong_event_type_returns_200_ignored(monkeypatch):
    client = _test_client(monkeypatch)
    event = {"id": "evt_2", "object": "event", "type": "invoice.payment_failed",
             "data": {"object": {"id": "in_1", "object": "invoice", "metadata": {}}}}
    payload = json.dumps(event).encode("utf-8")
    header = _sign(payload, WEBHOOK_SECRET)
    resp = client.post("/v1/webhooks/stripe", content=payload,
                       headers={"stripe-signature": header})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["handled"] is False


def test_webhook_happy_path_marks_monday_row_paid(monkeypatch):
    client = _test_client(monkeypatch)
    row = _row()
    mc = _FakeMondayClient([row])
    event = _invoice_paid_event()
    payload = json.dumps(event).encode("utf-8")
    header = _sign(payload, WEBHOOK_SECRET)

    with patch.object(stripe_paid_flow, "MondayClient", return_value=mc):
        resp = client.post("/v1/webhooks/stripe", content=payload,
                           headers={"stripe-signature": header})

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["handled"] is True
    assert body["result"]["already_paid"] is False
    assert body["result"]["item_id"] == row["monday_item_id"]
    assert len(mc.paid_calls) == 1
    assert row["status"] == "Paid"


def test_webhook_already_paid_is_a_200_noop(monkeypatch):
    client = _test_client(monkeypatch)
    row = _row(status="Paid")
    mc = _FakeMondayClient([row])
    event = _invoice_paid_event()
    payload = json.dumps(event).encode("utf-8")
    header = _sign(payload, WEBHOOK_SECRET)

    with patch.object(stripe_paid_flow, "MondayClient", return_value=mc):
        resp = client.post("/v1/webhooks/stripe", content=payload,
                           headers={"stripe-signature": header})

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["already_paid"] is True
    assert mc.paid_calls == []


def test_webhook_no_matching_row_is_still_a_200(monkeypatch):
    # No matching Monday row is a business no-op, NOT a signal to make
    # Stripe retry forever — must stay 200.
    client = _test_client(monkeypatch)
    mc = _FakeMondayClient([])
    event = _invoice_paid_event()
    payload = json.dumps(event).encode("utf-8")
    header = _sign(payload, WEBHOOK_SECRET)

    with patch.object(stripe_paid_flow, "MondayClient", return_value=mc):
        resp = client.post("/v1/webhooks/stripe", content=payload,
                           headers={"stripe-signature": header})

    assert resp.status_code == 200
    assert resp.json()["result"]["handled"] is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
