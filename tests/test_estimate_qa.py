"""
Estimate draft auto-QA — pure verification tests.

Runs under pytest OR directly: `python tests/test_estimate_qa.py`.
No network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.estimate import qa  # noqa: E402


def _enriched(**overrides):
    base = {
        "estimate": {
            "identifier": "2026-0804-001",
            "main_total": 12345.0,
            "main_total_pretty": "$12,345.00",
        },
        "client": {
            "name": "Acme Builders",
            "email": "billing@acmebuilders.com",
            "contact_name": "Pat",
        },
        "job": {
            "name": "123 Main | Acme Builders",
            "project_type": "Residential",
        },
        "prepared_by": {"name": "Green Valley Contractors"},
    }
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **val}
        else:
            base[key] = val
    return base


def _good_body(enriched=None):
    e = enriched or _enriched()
    est = e["estimate"]
    client = e["client"]
    job = e["job"]
    return (
        f"{client.get('contact_name') or 'there'},\n\n"
        f"Thank you for the opportunity to bid {job['name']}. "
        f"Attached is our estimate ({est['identifier']}), valid through Aug 31, 2026.\n\n"
        f"The estimate total is {est['main_total_pretty']}.\n\n"
        f"Please review the attached scope and pricing.\n\n"
        f"Thanks,\n"
        f"Green Valley Contractors\n"
    )


def _good_subject(enriched=None):
    e = enriched or _enriched()
    return (
        f"Green Valley Contractors — Estimate {e['estimate']['identifier']} "
        f"— {e['job']['name']}"
    )


def _good_writeback(**extra):
    wb = {
        "gmail_draft_url": "https://mail.google.com/mail/u/0/#drafts/r123",
        "gmail_draft_id": "r123",
        "monday_item_url": (
            "https://greenvalleycontractors.monday.com/boards/1/pulses/2"
        ),
        "drive_pdf_url": "https://drive.google.com/file/d/abc/view",
    }
    wb.update(extra)
    return wb


def _by_id(result, check_id):
    return next(c for c in result["checks"] if c["id"] == check_id)


# ---------------------------------------------------------------- pass case

def test_pass_case():
    enriched = _enriched()
    body = _good_body(enriched)
    subject = _good_subject(enriched)
    result = qa.check_estimate_draft(
        enriched=enriched,
        email_body=body,
        email_subject=subject,
        writeback=_good_writeback(),
        to_email=enriched["client"]["email"],
    )
    assert result["ok"] is True
    assert all(c["ok"] for c in result["checks"])
    assert "passed" in result["summary"].lower()
    assert result["links"]["gmail_draft"]
    assert result["links"]["portal_estimate"].endswith(
        "/ui/estimate?q=2026-0804-001"
    )


# -------------------------------------------------------- amount variants

def test_amount_formatting_variants():
    enriched = _enriched()
    subject = _good_subject(enriched)
    # Body uses bare dollars without $ / commas — must still pass.
    body = (
        "Pat,\n\n"
        "Thank you for the opportunity to bid 123 Main | Acme Builders. "
        "Attached is our estimate (2026-0804-001).\n\n"
        "The estimate total is 12345.\n\n"
        "Please review.\n\nThanks,\nGreen Valley Contractors\n"
    )
    result = qa.check_estimate_draft(
        enriched=enriched,
        email_body=body,
        email_subject=subject,
        writeback=_good_writeback(),
        to_email=enriched["client"]["email"],
    )
    assert _by_id(result, "amount")["ok"] is True

    # Pretty form with commas
    body2 = body.replace("12345.", "$12,345.00.")
    result2 = qa.check_estimate_draft(
        enriched=enriched,
        email_body=body2,
        email_subject=subject,
        writeback=_good_writeback(),
        to_email=enriched["client"]["email"],
    )
    assert _by_id(result2, "amount")["ok"] is True


def test_missing_amount_in_body():
    enriched = _enriched()
    body = (
        "Pat,\n\n"
        "Thank you for the opportunity to bid 123 Main | Acme Builders. "
        "Attached is our estimate (2026-0804-001).\n\n"
        "Please review the attached scope.\n\n"
        "Thanks,\nGreen Valley Contractors\n"
    )
    result = qa.check_estimate_draft(
        enriched=enriched,
        email_body=body,
        email_subject=_good_subject(enriched),
        writeback=_good_writeback(),
        to_email=enriched["client"]["email"],
    )
    assert result["ok"] is False
    assert _by_id(result, "amount")["ok"] is False
    assert "amount" in result["summary"].lower() or "Estimate amount" in result["summary"]


# --------------------------------------------------------- client name

def test_wrong_or_missing_client_name():
    enriched = _enriched()
    body = _good_body(enriched).replace("Acme Builders", "Wrong Client")
    # Job name still has Acme in it from job.name — strip that too for name check.
    # Client name check only looks at body for client.name; job name is separate.
    # After replace, "Acme Builders" may still be in job line of body.
    body = body.replace("123 Main | Acme Builders", "123 Main | Other")
    result = qa.check_estimate_draft(
        enriched=enriched,
        email_body=body,
        email_subject="Green Valley Contractors — Estimate 2026-0804-001 — 123 Main",
        writeback=_good_writeback(),
        to_email=enriched["client"]["email"],
    )
    assert _by_id(result, "client_name")["ok"] is False

    enriched_empty = _enriched(client={"name": "", "email": "billing@acmebuilders.com"})
    result2 = qa.check_estimate_draft(
        enriched=enriched_empty,
        email_body=_good_body(enriched),
        email_subject=_good_subject(enriched),
        writeback=_good_writeback(),
        to_email="billing@acmebuilders.com",
    )
    assert _by_id(result2, "client_name")["ok"] is False


# --------------------------------------------------------- gmail draft

def test_missing_gmail_draft():
    enriched = _enriched()
    result = qa.check_estimate_draft(
        enriched=enriched,
        email_body=_good_body(enriched),
        email_subject=_good_subject(enriched),
        writeback={},
        to_email=enriched["client"]["email"],
    )
    assert result["ok"] is False
    assert _by_id(result, "gmail_draft")["ok"] is False

    result2 = qa.check_estimate_draft(
        enriched=enriched,
        email_body=_good_body(enriched),
        email_subject=_good_subject(enriched),
        writeback={"gmail_status": "SKIPPED — not configured"},
        to_email=enriched["client"]["email"],
    )
    assert _by_id(result2, "gmail_draft")["ok"] is False

    result3 = qa.check_estimate_draft(
        enriched=enriched,
        email_body=_good_body(enriched),
        email_subject=_good_subject(enriched),
        writeback={"gmail_status": "FAILED — boom"},
        to_email=enriched["client"]["email"],
    )
    assert _by_id(result3, "gmail_draft")["ok"] is False


# --------------------------------------------------------- client email

def test_client_email_via_to_email_param():
    enriched = _enriched()
    # Body does not include the email address — to_email match is enough.
    body = _good_body(enriched)
    assert "billing@acmebuilders.com" not in body
    result = qa.check_estimate_draft(
        enriched=enriched,
        email_body=body,
        email_subject=_good_subject(enriched),
        writeback=_good_writeback(),
        to_email="billing@acmebuilders.com",
    )
    assert _by_id(result, "client_email")["ok"] is True

    result_bad = qa.check_estimate_draft(
        enriched=enriched,
        email_body=body,
        email_subject=_good_subject(enriched),
        writeback=_good_writeback(),
        to_email="wrong@example.com",
    )
    assert _by_id(result_bad, "client_email")["ok"] is False


# --------------------------------------------------------- portal URL

def test_portal_estimate_url_building():
    assert qa.portal_estimate_url("2026-0804-001") == (
        "https://portal.greenvalleycontractors.com/ui/estimate?q=2026-0804-001"
    )
    old = os.environ.get("GVC_PORTAL_PUBLIC_URL")
    try:
        os.environ["GVC_PORTAL_PUBLIC_URL"] = "https://example.test/"
        assert qa.portal_estimate_url("ABC") == (
            "https://example.test/ui/estimate?q=ABC"
        )
    finally:
        if old is None:
            os.environ.pop("GVC_PORTAL_PUBLIC_URL", None)
        else:
            os.environ["GVC_PORTAL_PUBLIC_URL"] = old


# --------------------------------------------------------- formatters

def test_format_qa_messages_include_status_and_links():
    enriched = _enriched()
    result = qa.check_estimate_draft(
        enriched=enriched,
        email_body=_good_body(enriched),
        email_subject=_good_subject(enriched),
        writeback=_good_writeback(),
        to_email=enriched["client"]["email"],
    )
    slack = qa.format_qa_slack_message(result, enriched)
    assert "✅" in slack
    assert "READY" in slack
    assert "mail.google.com" in slack

    email_body = qa.format_qa_email_body(result, enriched)
    assert "PASS" in email_body
    assert "2026-0804-001" in email_body
    assert qa.qa_email_subject(result, enriched) == (
        "Estimate QA — 2026-0804-001 — READY"
    )

    fail = qa.check_estimate_draft(
        enriched=enriched,
        email_body="hello estimate",
        email_subject="x",
        writeback={},
        to_email="wrong@x.com",
    )
    slack_fail = qa.format_qa_slack_message(fail, enriched)
    assert "❌" in slack_fail
    assert "NEEDS FIX" in slack_fail
    assert "Failed checks" in slack_fail


# --------------------------------------------------------- runner

def _run_all():
    tests = [
        test_pass_case,
        test_amount_formatting_variants,
        test_missing_amount_in_body,
        test_wrong_or_missing_client_name,
        test_missing_gmail_draft,
        test_client_email_via_to_email_param,
        test_portal_estimate_url_building,
        test_format_qa_messages_include_status_and_links,
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
