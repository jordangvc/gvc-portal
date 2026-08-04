"""Invoice input validation, enrichment, and environment checks (from invoice.py)."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from shared import paths
from shared.doc_number import core_number, for_invoice
from shared.money import fmt_date, fmt_money
from shared.recipients import validate_client_recipients

OUTPUT_DIR = paths.OUTPUT_DIR
GOOGLE_SERVICE_ACCOUNT_PATH = paths.DEFAULT_SA_PATH
GMAIL_TOKEN_PATH = paths.DEFAULT_GMAIL_TOKEN_PATH

STRIPE_KEY_PREFIXES = ("sk_live_", "rk_live_", "sk_test_", "rk_test_")

# Email is validated separately via shared.recipients (multi-address OR
# explicit no-email / print-mail-hand-deliver path for customers without inbox).
REQUIRED_CLIENT_FIELDS = ["name", "billing_address"]
# identifier may be blank when job.project_number carries the spine — see
# ensure_invoice_identifier() (auto INV-YYYY-MMDD-NNN).
REQUIRED_INVOICE_FIELDS = ["issue_date", "payment_terms", "line_items"]


def validate_environment(
    *,
    mode: str,
    needs_stripe: bool,
    monday_source: bool,
) -> tuple[list[str], list[str]]:
    """
    Inspect the environment for this run and return (warnings, errors).

    Hard requirements (errors) abort the run with a clear message. Soft
    requirements (warnings) print to stderr but the run continues — the
    affected integration will simply graceful-skip later.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if needs_stripe:
        key = os.environ.get("STRIPE_API_KEY", "")
        if not key:
            errors.append(
                "STRIPE_API_KEY env var not set. "
                "Set it in .env or export it. Use --dry-run for preview without Stripe."
            )
        elif not key.startswith(STRIPE_KEY_PREFIXES):
            warnings.append(
                f"STRIPE_API_KEY does not start with a recognized prefix "
                f"({', '.join(STRIPE_KEY_PREFIXES)}). Stripe calls will likely fail."
            )
        elif mode == "live" and "_test_" in key:
            warnings.append(
                "STRIPE_API_KEY is a TEST key. Invoices will be created in the "
                "Stripe test dashboard, not visible to real customers."
            )

    if monday_source and not os.environ.get("MONDAY_API_TOKEN"):
        errors.append(
            "MONDAY_API_TOKEN env var not set, but --monday-item was passed. "
            "Set the token in .env or remove --monday-item."
        )

    if mode == "live":
        if not GOOGLE_SERVICE_ACCOUNT_PATH.exists():
            warnings.append(
                f"Google service account JSON not found at {GOOGLE_SERVICE_ACCOUNT_PATH}. "
                "Drive upload will be skipped."
            )
        elif not os.environ.get("GVC_DRIVE_SHARED_DRIVE_ID"):
            warnings.append(
                "GVC_DRIVE_SHARED_DRIVE_ID env var not set. "
                "Drive upload will be skipped."
            )

        if not GMAIL_TOKEN_PATH.exists():
            warnings.append(
                f"Gmail OAuth token not found at {GMAIL_TOKEN_PATH}. "
                "Gmail draft will be skipped. Run `./gvc gmail.py setup` to fix."
            )

    return warnings, errors


def ensure_invoice_identifier(data: dict) -> None:
    """
    In-place: normalize / auto-assign the invoice Document #.

    Portal spine: INV-{same core as EST-/PRO-}. Prefer an explicit identifier;
    otherwise derive from job.project_number. Legacy non-spine ids (GVC-…,
    C-005) are left untouched.
    """
    inv = data.setdefault("invoice", {})
    raw = (inv.get("identifier") or "").strip()
    if raw:
        if core_number(raw):
            inv["identifier"] = for_invoice(raw)
        else:
            inv["identifier"] = raw
        return
    src = ((data.get("job") or {}).get("project_number") or "").strip()
    if core_number(src):
        inv["identifier"] = for_invoice(src)


def validate(data: dict) -> None:
    """Cheap validation — clear errors beat silent Stripe failures."""
    if "client" not in data or "invoice" not in data or "job" not in data:
        raise ValueError("Input must have top-level keys: client, job, invoice")

    ensure_invoice_identifier(data)

    for f in REQUIRED_CLIENT_FIELDS:
        if not data["client"].get(f):
            raise ValueError(f"client.{f} is required")
    validate_client_recipients(data["client"])

    for f in REQUIRED_INVOICE_FIELDS:
        if not data["invoice"].get(f):
            raise ValueError(f"invoice.{f} is required")

    if not (data["invoice"].get("identifier") or "").strip():
        raise ValueError(
            "invoice.identifier is required (or set job.project_number so "
            "INV-YYYY-MMDD-NNN can be auto-assigned)."
        )

    if not data["job"].get("name"):
        raise ValueError("job.name is required")

    if not data["invoice"]["line_items"]:
        raise ValueError("invoice.line_items must have at least one entry")

    for i, li in enumerate(data["invoice"]["line_items"]):
        if not li.get("description"):
            raise ValueError(f"invoice.line_items[{i}].description is required")
        # Accept either `amount` directly OR `quantity` + `unit_price` for back-compat
        has_amount = li.get("amount") is not None
        has_qty_rate = li.get("quantity") is not None and li.get("unit_price") is not None
        if not (has_amount or has_qty_rate):
            raise ValueError(
                f"invoice.line_items[{i}] needs either `amount` or both `quantity` and `unit_price`"
            )


def enrich(data: dict) -> dict:
    """Add computed/pretty fields used by the template. Mutates a copy."""
    out = json.loads(json.dumps(data))  # cheap deep copy
    inv = out["invoice"]

    # Auto-compute due_date = issue_date + 30 days if not provided
    if not inv.get("due_date"):
        issue = datetime.strptime(inv["issue_date"], "%Y-%m-%d").date()
        inv["due_date"] = (issue + timedelta(days=30)).strftime("%Y-%m-%d")

    # Pretty dates
    inv["issue_date_pretty"] = fmt_date(inv["issue_date"])
    inv["due_date_pretty"] = fmt_date(inv["due_date"])
    if inv.get("period_end_date"):
        inv["period_end_date_pretty"] = fmt_date(inv["period_end_date"])

    # Line item math + pretty.
    # `kind` is optional: "work" (default — base contract work) or "co"/"tm"
    # (change order / T&M, billed in addition to base work). The distinction
    # matters for: (a) retainage allocation (CO lines are not subject to
    # retainage), (b) Stripe description suffixing ("plusTM"), and (c) the PDF
    # rendering, which shows CO lines under a distinct section header.
    CO_KINDS = {"co", "tm", "change_order", "change-order"}
    subtotal = 0.0
    subtotal_work = 0.0
    subtotal_co = 0.0
    for li in inv["line_items"]:
        if li.get("amount") is not None:
            amt = float(li["amount"])
        else:
            amt = float(li["quantity"]) * float(li["unit_price"])
        li["amount"] = round(amt, 2)
        li["amount_pretty"] = fmt_money(li["amount"])
        li["_is_co"] = (li.get("kind") or "").lower() in CO_KINDS
        subtotal += amt
        if li["_is_co"]:
            subtotal_co += amt
        else:
            subtotal_work += amt

    inv["subtotal"] = round(subtotal, 2)
    inv["subtotal_pretty"] = fmt_money(inv["subtotal"])
    inv["subtotal_work"] = round(subtotal_work, 2)
    inv["subtotal_work_pretty"] = fmt_money(subtotal_work)
    inv["subtotal_co"] = round(subtotal_co, 2)
    inv["subtotal_co_pretty"] = fmt_money(subtotal_co)
    inv["has_co"] = subtotal_co > 0

    # Discount (architecture: visible line item, never hidden)
    discount_amount = 0.0
    if inv.get("discount"):
        d = inv["discount"]
        # Always store discount as negative number internally
        amt = -abs(float(d["amount"]))
        d["amount"] = round(amt, 2)
        d["amount_pretty"] = fmt_money(amt, signed=True)
        discount_amount = amt

    # Retainage (commercial progress billing): subtracts from PDF total but
    # NEVER pushed to Stripe as a line item. The Stripe invoice reflects the
    # net amount due; the retainage held this period is shown on the GVC PDF
    # only, mirroring the AIA G702 "Net amount due this application" line.
    #
    # `retainage.scope` controls which lines the held amount is allocated
    # against (used for the Stripe line-item net calculation and for the PDF
    # label):
    #   - "base" (default): only the base-contract (non-CO) lines are subject
    #     to retainage. CO / T&M lines bill at full value. Used when the
    #     contract / GC convention treats change-order work as non-retained.
    #   - "all": retainage applies to every line including CO / T&M.
    #     Allocated proportionally across all lines so each Stripe line item
    #     reflects its share of the held amount.
    retainage_amount = 0.0
    if inv.get("retainage"):
        r = inv["retainage"]
        amt = -abs(float(r["amount"]))
        r["amount"] = round(amt, 2)
        r["amount_pretty"] = fmt_money(amt, signed=True)
        if r.get("percentage") is not None:
            pct = float(r["percentage"])
            r["percentage_pretty"] = (
                f"{int(pct)}%" if pct == int(pct) else f"{pct:g}%"
            )
        scope = (r.get("scope") or "base").lower()
        if scope not in ("base", "all"):
            raise ValueError(f"invoice.retainage.scope must be 'base' or 'all', got {scope!r}")
        r["scope"] = scope
        retainage_amount = amt

    inv["total"] = round(subtotal + discount_amount + retainage_amount, 2)
    inv["total_pretty"] = fmt_money(inv["total"])

    # Past-due flag for the badge
    today = date.today()
    due = datetime.strptime(inv["due_date"], "%Y-%m-%d").date()
    out["is_past_due"] = today > due

    return out
