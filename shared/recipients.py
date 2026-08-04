"""
Customer email recipients — multi-address + no-email delivery.
=========================================================================
Estimates and invoices historically required a single `client.email`. Office
reality: GCs want several people on the draft, and some homeowners (often
elderly) have no email at all — the PDF is printed, mailed, or handed over.

This module is pure (no network). Callers normalize once, then pass the
resolved To/Cc headers to Gmail and a Stripe-safe email to customer upsert.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DELIVERY_EMAIL = "email"
DELIVERY_PRINT = "print"
DELIVERY_MAIL = "mail"
DELIVERY_HAND = "hand_deliver"
NO_EMAIL_METHODS = frozenset({DELIVERY_PRINT, DELIVERY_MAIL, DELIVERY_HAND})
ALL_METHODS = frozenset({DELIVERY_EMAIL, *NO_EMAIL_METHODS})

# Synthetic Stripe identity when the customer has no real inbox. Uses the
# reserved `.invalid` TLD so nothing can deliver mail there by accident.
_STRIPE_NO_EMAIL_DOMAIN = "noemail.gvc.invalid"

_DEFAULT_OFFICE = "andrea@greenvalleycontractors.com"


def office_fallback_email() -> str:
    return (
        os.environ.get("GVC_OFFICE_REVIEW_EMAIL")
        or os.environ.get("GVC_NO_EMAIL_DRAFT_TO")
        or _DEFAULT_OFFICE
    ).strip()


def parse_email_list(value: Any) -> list[str]:
    """
    Accept a string (comma / semicolon / whitespace / newline separated),
    a list/tuple of strings, or None. Returns deduped lowercase-stable
    addresses preserving the first-seen casing. Invalid tokens are dropped.
    """
    if value is None:
        return []
    raw_parts: list[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if item is None:
                continue
            raw_parts.extend(re.split(r"[,;\s]+", str(item).strip()))
    else:
        raw_parts.extend(re.split(r"[,;\s]+", str(value).strip()))

    out: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        addr = part.strip().strip("<>").strip()
        if not addr or not EMAIL_RE.match(addr):
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(addr)
    return out


def is_no_email_client(client: Optional[dict]) -> bool:
    """True when the office marked the customer as having no email inbox."""
    c = client or {}
    if c.get("no_email") in (True, 1, "1", "true", "True", "yes", "YES"):
        return True
    method = str(c.get("delivery_method") or "").strip().lower()
    return method in NO_EMAIL_METHODS


def _delivery_method(client: dict) -> str:
    if is_no_email_client(client):
        method = str(client.get("delivery_method") or DELIVERY_PRINT).strip().lower()
        return method if method in NO_EMAIL_METHODS else DELIVERY_PRINT
    return DELIVERY_EMAIL


def _slug_for_stripe(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "customer").lower()).strip("-")
    return (slug or "customer")[:60]


def stripe_email_for_no_email(client_name: str) -> str:
    """Idempotent synthetic email for Stripe Customer upsert — never mailed."""
    return f"no-email.{_slug_for_stripe(client_name)}@{_STRIPE_NO_EMAIL_DOMAIN}"


def customer_emails_from_client(client: Optional[dict]) -> list[str]:
    """Real customer inboxes from email / emails fields (ignores no-email flag)."""
    c = client or {}
    emails = parse_email_list(c.get("emails"))
    if not emails:
        emails = parse_email_list(c.get("email"))
    return emails


def cc_emails_from_client(client: Optional[dict], *, top_level_cc: Any = None) -> list[str]:
    c = client or {}
    cc = parse_email_list(c.get("cc_emails")) or parse_email_list(c.get("cc"))
    for extra in parse_email_list(top_level_cc):
        if extra.lower() not in {x.lower() for x in cc}:
            cc.append(extra)
    return cc


def validate_client_recipients(client: Optional[dict], *, require_billing_email: bool = True) -> None:
    """
    Raise ValueError with a clear message when recipients are incomplete.
    `require_billing_email` is kept for call-site clarity; no-email clients
    always skip the customer-inbox requirement.
    """
    c = client or {}
    if is_no_email_client(c):
        method = _delivery_method(c)
        if method not in NO_EMAIL_METHODS:
            raise ValueError(
                "client.delivery_method must be print, mail, or hand_deliver "
                "when the customer has no email."
            )
        return
    if not require_billing_email:
        return
    emails = customer_emails_from_client(c)
    if not emails:
        raise ValueError(
            "client.email is required (or check “Customer has no email” "
            "and choose print/mail/hand-deliver)."
        )
    for addr in emails:
        if not EMAIL_RE.match(addr):
            raise ValueError(f"Invalid client email address: {addr!r}")


def normalize_client_recipients(
    client: Optional[dict],
    *,
    office_fallback: Optional[str] = None,
    top_level_cc: Any = None,
) -> dict:
    """
    Resolve Gmail To/Cc + Stripe email for a client dict.

    Returns {
      no_email, delivery_method,
      customer_emails, to_emails, cc_emails,
      to_header, cc_header,
      primary_email, stripe_email,
      office_notice,  # prepend to draft body when no_email
    }
    """
    c = client or {}
    office = (office_fallback or office_fallback_email()).strip()
    no_email = is_no_email_client(c)
    method = _delivery_method(c)
    customer = [] if no_email else customer_emails_from_client(c)
    cc = cc_emails_from_client(c, top_level_cc=top_level_cc)

    if no_email:
        to_emails = [office] if office else []
        delivery_label = {
            DELIVERY_PRINT: "print / hand-deliver the PDF",
            DELIVERY_MAIL: "mail a printed copy",
            DELIVERY_HAND: "hand-deliver on site",
        }.get(method, "deliver without email")
        office_notice = (
            f"⚠ CUSTOMER HAS NO EMAIL — {delivery_label}. "
            f"This draft is for the office only ({office or '—'}). "
            f"Do NOT send to the customer. Attach/print the PDF and deliver offline."
        )
        stripe_email = stripe_email_for_no_email(str(c.get("name") or "customer"))
        primary = stripe_email
    else:
        to_emails = list(customer)
        office_notice = None
        primary = customer[0] if customer else ""
        stripe_email = primary

    # Keep customer addresses out of Cc if they already appear in To.
    to_keys = {a.lower() for a in to_emails}
    cc_emails = [a for a in cc if a.lower() not in to_keys]

    return {
        "no_email": no_email,
        "delivery_method": method,
        "customer_emails": customer,
        "to_emails": to_emails,
        "cc_emails": cc_emails,
        "to_header": ", ".join(to_emails),
        "cc_header": ", ".join(cc_emails) if cc_emails else None,
        "primary_email": primary,
        "stripe_email": stripe_email,
        "office_notice": office_notice,
    }


def prepend_office_notice(body: str, notice: Optional[str]) -> str:
    if not notice:
        return body or ""
    return f"{notice}\n\n{body or ''}"
