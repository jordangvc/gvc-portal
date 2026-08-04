"""
Estimate draft auto-QA — pure verification of what Andrea checks by hand.

No network. Callers (estimate_flow + slack_notify) supply the composed email
body/subject, the enriched payload, and the finalize writeback, then handle
Slack DM / office Gmail draft notices themselves.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import quote

DEFAULT_OFFICE_REVIEW_EMAIL = "andrea@greenvalleycontractors.com"
DEFAULT_PORTAL_PUBLIC_URL = "https://portal.greenvalleycontractors.com"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Money-like token: optional $, digits with optional commas, optional .cc
_MONEY_TOKEN_RE = re.compile(r"\$?\d[\d,]*(?:\.\d{1,2})?")


def office_review_email() -> str:
    return (
        os.environ.get("GVC_OFFICE_REVIEW_EMAIL") or DEFAULT_OFFICE_REVIEW_EMAIL
    ).strip()


def portal_base_url() -> str:
    return (
        os.environ.get("GVC_PORTAL_PUBLIC_URL") or DEFAULT_PORTAL_PUBLIC_URL
    ).rstrip("/")


def portal_estimate_url(identifier: str) -> str:
    """Deep link into the estimate tool with the estimate number prefilled."""
    ident = (identifier or "").strip()
    return f"{portal_base_url()}/ui/estimate?q={quote(ident, safe='')}"


def _digits_as_cents(value: Any) -> str:
    """
    Normalize a money string or number to an integer-cents digit string so
    `$12,345.00`, `12345`, and `12,345.00` all compare equal.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(int(round(float(value) * 100)))
    s = str(value).strip()
    if not s:
        return ""
    cleaned = re.sub(r"[^\d.]", "", s)
    if not cleaned or cleaned == ".":
        return ""
    try:
        return str(int(round(float(cleaned) * 100)))
    except ValueError:
        return re.sub(r"\D", "", s)


def _amount_appears_in_text(amount: Any, pretty: str, text: str) -> bool:
    body = text or ""
    pretty = (pretty or "").strip()
    if pretty and pretty in body:
        return True
    # Also tolerate missing $ / commas vs pretty form.
    if pretty:
        compact = pretty.replace(",", "").replace("$", "")
        body_compact = body.replace(",", "").replace("$", "")
        if compact and compact in body_compact:
            return True
    target = _digits_as_cents(pretty) or _digits_as_cents(amount)
    if not target:
        return False
    for match in _MONEY_TOKEN_RE.finditer(body):
        if _digits_as_cents(match.group()) == target:
            return True
    return False


def _check(
    check_id: str, label: str, ok: bool, detail: str
) -> dict[str, Any]:
    return {"id": check_id, "label": label, "ok": bool(ok), "detail": detail}


def check_estimate_draft(
    *,
    enriched: dict,
    email_body: str,
    email_subject: str,
    writeback: dict,
    to_email: Optional[str] = None,
) -> dict:
    """
    Verify a finalized estimate draft against Andrea's manual checklist.

    Returns {
      "ok": bool,
      "checks": [{"id", "label", "ok", "detail"}, ...],
      "summary": str,
      "links": {"gmail_draft", "monday", "portal_estimate", "drive_pdf"},
    }
    """
    est = (enriched or {}).get("estimate") or {}
    client = (enriched or {}).get("client") or {}
    job = (enriched or {}).get("job") or {}
    wb = writeback or {}
    body = email_body or ""
    subject = email_subject or ""
    combined = f"{subject}\n{body}"
    body_l = body.lower()
    combined_l = combined.lower()

    identifier = str(est.get("identifier") or "").strip()
    client_name = str(client.get("name") or "").strip()
    client_email = str(client.get("email") or "").strip()
    job_name = str(job.get("name") or "").strip()
    main_total = est.get("main_total")
    main_pretty = str(est.get("main_total_pretty") or "").strip()
    recipient = (to_email if to_email is not None else client_email).strip()

    checks: list[dict[str, Any]] = []

    # 1) Client name in body
    if not client_name:
        checks.append(_check(
            "client_name", "Customer name", False,
            "client.name is empty",
        ))
    elif client_name.lower() in body_l:
        checks.append(_check(
            "client_name", "Customer name", True,
            f"Found {client_name!r} in email body",
        ))
    else:
        checks.append(_check(
            "client_name", "Customer name", False,
            f"{client_name!r} not found in email body",
        ))

    # 2) Customer email
    if not client_email:
        checks.append(_check(
            "client_email", "Customer email", False,
            "client.email is empty",
        ))
    elif not _EMAIL_RE.match(client_email):
        checks.append(_check(
            "client_email", "Customer email", False,
            f"{client_email!r} does not look like an email address",
        ))
    else:
        recipient_ok = (
            recipient.lower() == client_email.lower()
            if recipient
            else False
        )
        in_body = client_email.lower() in body_l
        if recipient_ok or in_body:
            detail_bits = []
            if recipient_ok:
                detail_bits.append(f"draft To matches {client_email}")
            if in_body:
                detail_bits.append("email appears in body")
            checks.append(_check(
                "client_email", "Customer email", True,
                "; ".join(detail_bits),
            ))
        else:
            checks.append(_check(
                "client_email", "Customer email", False,
                f"{client_email!r} not in body and draft To "
                f"({recipient or '—'}) does not match",
            ))

    # 3) Amount in body
    if main_total is None and not main_pretty:
        checks.append(_check(
            "amount", "Estimate amount", False,
            "estimate.main_total / main_total_pretty missing",
        ))
    elif _amount_appears_in_text(main_total, main_pretty, body):
        shown = main_pretty or str(main_total)
        checks.append(_check(
            "amount", "Estimate amount", True,
            f"Found amount {shown} in email body",
        ))
    else:
        shown = main_pretty or str(main_total)
        checks.append(_check(
            "amount", "Estimate amount", False,
            f"Amount {shown} not found in email body",
        ))

    # 4) Identifier in subject or body
    if not identifier:
        checks.append(_check(
            "identifier", "Estimate number", False,
            "estimate.identifier is empty",
        ))
    elif identifier.lower() in combined_l:
        checks.append(_check(
            "identifier", "Estimate number", True,
            f"Found {identifier} in subject/body",
        ))
    else:
        checks.append(_check(
            "identifier", "Estimate number", False,
            f"{identifier} not found in subject or body",
        ))

    # 5) Job name in subject or body
    if not job_name:
        checks.append(_check(
            "job_name", "Job name", False,
            "job.name is empty",
        ))
    elif job_name.lower() in combined_l:
        checks.append(_check(
            "job_name", "Job name", True,
            f"Found {job_name!r} in subject/body",
        ))
    else:
        checks.append(_check(
            "job_name", "Job name", False,
            f"{job_name!r} not found in subject or body",
        ))

    # 6) Gmail draft actually created
    gmail_url = (wb.get("gmail_draft_url") or "").strip()
    gmail_status = str(wb.get("gmail_status") or "")
    status_upper = gmail_status.upper()
    if status_upper.startswith("FAILED") or status_upper.startswith("SKIPPED"):
        checks.append(_check(
            "gmail_draft", "Gmail draft created", False,
            gmail_status or "gmail_status indicates draft was not created",
        ))
    elif gmail_url:
        checks.append(_check(
            "gmail_draft", "Gmail draft created", True,
            f"Draft URL present",
        ))
    else:
        checks.append(_check(
            "gmail_draft", "Gmail draft created", False,
            "writeback missing gmail_draft_url",
        ))

    # 7) Body has greeting / company sign-off markers
    has_estimate_word = "estimate" in body_l
    has_green_valley = "green valley" in body_l
    if has_estimate_word or has_green_valley:
        markers = []
        if has_estimate_word:
            markers.append("'estimate'")
        if has_green_valley:
            markers.append("'Green Valley'")
        checks.append(_check(
            "body_markers", "Email body content", True,
            f"Found marker(s): {', '.join(markers)}",
        ))
    else:
        checks.append(_check(
            "body_markers", "Email body content", False,
            "Body missing expected greeting/sign-off markers "
            "('estimate' or 'Green Valley')",
        ))

    ok = all(c["ok"] for c in checks)
    failed = [c for c in checks if not c["ok"]]
    if ok:
        summary = (
            f"Estimate {identifier or '—'} QA passed — "
            f"draft ready for office review"
        )
    else:
        labels = ", ".join(c["label"] for c in failed)
        summary = (
            f"Estimate {identifier or '—'} QA needs fix — "
            f"{len(failed)} check(s) failed: {labels}"
        )

    links = {
        "gmail_draft": gmail_url or None,
        "monday": (wb.get("monday_item_url") or None),
        "portal_estimate": portal_estimate_url(identifier) if identifier else None,
        "drive_pdf": (wb.get("drive_pdf_url") or None),
    }

    return {
        "ok": ok,
        "checks": checks,
        "summary": summary,
        "links": links,
    }


def _link_lines(links: dict) -> list[str]:
    labels = (
        ("gmail_draft", "Gmail draft"),
        ("monday", "Monday bid"),
        ("portal_estimate", "Portal estimate"),
        ("drive_pdf", "Drive PDF"),
    )
    lines = []
    for key, label in labels:
        url = (links or {}).get(key)
        if url:
            lines.append(f"• {label}: {url}")
    return lines


def format_qa_slack_message(result: dict, enriched: dict) -> str:
    """Slack DM body for Andrea — pass/fail, failed checks, raw deep links."""
    est = (enriched or {}).get("estimate") or {}
    identifier = (est.get("identifier") or "—").strip() or "—"
    ok = bool((result or {}).get("ok"))
    icon = "✅" if ok else "❌"
    status = "READY" if ok else "NEEDS FIX"
    parts = [
        f"{icon} *Estimate QA — {identifier} — {status}*",
        (result or {}).get("summary") or "",
    ]
    failed = [c for c in (result or {}).get("checks") or [] if not c.get("ok")]
    if failed:
        parts.append("")
        parts.append("*Failed checks:*")
        for c in failed:
            parts.append(f"• ❌ {c.get('label')}: {c.get('detail')}")
    link_lines = _link_lines((result or {}).get("links") or {})
    if link_lines:
        parts.append("")
        parts.append("*Links:*")
        parts.extend(link_lines)
    return "\n".join(p for p in parts if p is not None).rstrip() + "\n"


def format_qa_email_body(result: dict, enriched: dict) -> str:
    """Office Gmail draft body — bullet checks + links. Never auto-sent."""
    est = (enriched or {}).get("estimate") or {}
    client = (enriched or {}).get("client") or {}
    job = (enriched or {}).get("job") or {}
    identifier = (est.get("identifier") or "—").strip() or "—"
    ok = bool((result or {}).get("ok"))
    status = "READY" if ok else "NEEDS FIX"

    lines = [
        f"Estimate QA — {identifier} — {status}",
        "",
        (result or {}).get("summary") or "",
        "",
        f"Client: {client.get('name') or '—'}",
        f"Job: {job.get('name') or '—'}",
        f"Total: {est.get('main_total_pretty') or '—'}",
        "",
        "Checks:",
    ]
    for c in (result or {}).get("checks") or []:
        mark = "PASS" if c.get("ok") else "FAIL"
        lines.append(f"• [{mark}] {c.get('label')}: {c.get('detail')}")

    link_lines = _link_lines((result or {}).get("links") or {})
    if link_lines:
        lines.append("")
        lines.append("Links:")
        lines.extend(link_lines)

    lines.extend([
        "",
        "This is an automated office notice. The customer draft was NOT sent.",
        "— GVC Portal Estimate QA",
    ])
    return "\n".join(lines)


def qa_email_subject(result: dict, enriched: dict) -> str:
    est = (enriched or {}).get("estimate") or {}
    identifier = (est.get("identifier") or "—").strip() or "—"
    status = "READY" if (result or {}).get("ok") else "NEEDS FIX"
    return f"Estimate QA — {identifier} — {status}"
