"""
Monday.com integration for the GVC invoice system.
=========================================================================
Two responsibilities:
  1. READ — given a Monday item ID, pull the project + linked customer
     and assemble the canonical invoice-JSON dict in memory.
  2. WRITE — after a successful live run, push Stripe IDs / hosted URL /
     Drive link back to the Monday item as a writeback step.

Designed to work as production HTTP code (no MCP dependency), so the same
module runs on the user's Mac, inside Cloud Run, or from any future caller.

Auth: MONDAY_API_TOKEN env var. Generate at Monday → user avatar →
Developers → My API tokens.

Board IDs default to GVC's production boards but are overridable via env
for testing / migration.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

from adapters.monday import cache as monday_cache
from shared.doc_number import (
    core_number,
    for_invoice,
    for_project,
    is_spine_number,
    search_needles,
)

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_API_VERSION = "2024-10"  # pin so query semantics don't drift on us

# Process-local Monday timing (enable with GVC_MONDAY_TRACE=1). Not a correctness
# layer — used to measure request count + wall time on hub/billing/jobcheck paths.
_TRACE_LOCK = threading.Lock()
_TRACE: list[dict[str, Any]] = []
_TRACE_ENABLED: Optional[bool] = None


def monday_trace_enabled() -> bool:
    global _TRACE_ENABLED
    if _TRACE_ENABLED is None:
        _TRACE_ENABLED = (os.environ.get("GVC_MONDAY_TRACE") or "").strip() == "1"
    return _TRACE_ENABLED


def reset_monday_trace() -> None:
    """Clear the in-process Monday request log (tests / measure scripts)."""
    with _TRACE_LOCK:
        _TRACE.clear()


def get_monday_trace() -> list[dict[str, Any]]:
    """Copy of traced Monday POSTs: {ms, ok, status, err, query_head, vars_keys}."""
    with _TRACE_LOCK:
        return list(_TRACE)


def monday_trace_summary() -> dict[str, Any]:
    rows = get_monday_trace()
    total_ms = sum(float(r.get("ms") or 0) for r in rows)
    errors = [r for r in rows if not r.get("ok")]
    return {
        "count": len(rows),
        "total_ms": round(total_ms, 1),
        "error_count": len(errors),
        "max_ms": round(max((float(r.get("ms") or 0) for r in rows), default=0.0), 1),
        "rate_limited": any(
            (r.get("status") == 429)
            or ("ComplexityException" in str(r.get("err") or ""))
            or ("rate" in str(r.get("err") or "").lower())
            for r in rows
        ),
        "calls": rows,
    }


def _query_head(query: str) -> str:
    """First meaningful line of a GraphQL document (for logs, not secrets)."""
    for line in (query or "").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:160]
    return ""


def _record_trace(**kwargs: Any) -> None:
    with _TRACE_LOCK:
        _TRACE.append(kwargs)
        # Bound memory if someone leaves TRACE on in prod.
        if len(_TRACE) > 500:
            del _TRACE[:-400]

# Board IDs (env-overridable for testing)
from shared.boards import (PROJECTS_BOARD_ID, CUSTOMERS_BOARD_ID,
                           INVOICES_SENT_BOARD_ID)

# Invoices board column IDs (board 1931784889). Verified against get_board_info
# 2026-06-19 after the 2026-06 board migration. NOTE: the board's item NAME now
# holds the human-readable project name; the invoice identifier lives in the
# dedicated "Document #" column (INV_COL_DOCUMENT) and is the UNIQUE dedupe key —
# never dedupe on item name (project names repeat across pay apps).
INV_COL_DOCUMENT = "text_mm4440p6"          # "Document #" — invoice identifier (UNIQUE KEY)
INV_COL_AMOUNT = "amount"
INV_COL_STATUS = "status"
INV_COL_JOB_TYPE = "color_mm3qyj4g"         # status: Residential | Commercial | AIA
INV_COL_ISSUE_DATE = "issue_date"
INV_COL_DUE_DATE = "date_mm3q2dkw"
INV_COL_STRIPE_INVOICE = "text_mm3qse5f"
INV_COL_STRIPE_URL = "link_mm3qg47g"
INV_COL_GMAIL = "link_mm3q677"
INV_COL_JOB = "text_mm3qy9t5"
INV_COL_DRIVE_FOLDER = "link_mm3q8r5x"
INV_COL_CUSTOMER = "board_relation_mm3qfwzv"        # -> Customers board 1919766765
INV_COL_LINKED_PROJECT = "board_relation_mm40j9h9"  # -> Projects board 1918846405
INV_COL_NOTE = "long_text_mm3qpsay"  # free-text note; check-deposit appends here

# Status semantics on the Invoices board (active labels): "Invoice Sent",
# "Ready for Invoicing", "Create Invoice", "Draft Ready", "Paid", "Overdue",
# "Void". The legacy "Draft" label is DEACTIVATED — do not write it. A freshly
# billed invoice (Stripe invoice finalized + Gmail draft created, not yet sent
# by the office) is recorded as "Draft Ready"; the office flips it to
# "Invoice Sent" after clicking Send. Both overridable via env.
LEDGER_CREATE_STATUS_LABEL = os.environ.get("GVC_MONDAY_LEDGER_CREATE_STATUS", "Draft Ready")
LEDGER_GROUP_ID = os.environ.get("GVC_MONDAY_LEDGER_GROUP_ID", "topics")  # "Billed Invoices"
# An invoice is CLOSED only when Paid or Void. Everything else — "Invoice Sent"
# (the board's actual active label), "Overdue", a blank status, etc. — counts as
# open for check-matching. Defined as a closed-set so new active labels don't
# silently drop rows from the picker.
CLOSED_INVOICE_STATUSES = {"paid", "void"}
PAID_INVOICE_STATUS_LABEL = "Paid"
# Partial check payments: rows keep an open-class status so the check picker
# still offers them for the follow-up check ("partially paid" is NOT in
# CLOSED_INVOICE_STATUSES — deliberate). Label auto-creates on first write.
PARTIALLY_PAID_STATUS_LABEL = "Partially Paid"
# "Balance Due" numeric column — remaining balance synced from Stripe at each
# check commit. Created once via scripts/add_balance_due_column.py; set the
# resulting column id here via env. Empty = balance tracking disabled (reads
# and writes skip gracefully; matching falls back to the Amount column).
INV_COL_BALANCE_DUE = os.environ.get("GVC_MONDAY_BALANCE_DUE_COL", "")
# "Emailed on" (date) — stamped by the sent-watcher when the invoice email is
# detected as ACTUALLY sent from hello@ (empty = draft still waiting). Created
# 2026-07-24 via the Monday MCP; distinct from Status, which the office may
# also flip by hand.
INV_COL_EMAILED_ON = os.environ.get("GVC_MONDAY_INV_EMAILED_ON_COL", "date_mm5kfwr8")
SENT_INVOICE_STATUS_LABEL = "Invoice Sent"


def _invoice_column_ids(*extra: str) -> str:
    """The JSON list of Invoices-board column ids to fetch, as a GraphQL-ready
    string. Includes Balance Due only when configured."""
    ids = [INV_COL_DOCUMENT, INV_COL_AMOUNT, INV_COL_STATUS, INV_COL_STRIPE_INVOICE,
           INV_COL_JOB, INV_COL_DRIVE_FOLDER, INV_COL_CUSTOMER,
           INV_COL_ISSUE_DATE, INV_COL_EMAILED_ON, *extra]
    if INV_COL_BALANCE_DUE:
        ids.append(INV_COL_BALANCE_DUE)
    return json.dumps(ids)

# Column IDs on the Projects board (see docs/monday-board-roadmap.md for the map)
COL_BUILDER = "text"
COL_CONTACT_NAME = "customer_name"
COL_LOCATION = "location5"
COL_BILLING_CATEGORY = "color_mm14qmyy"
COL_INVOICE_STATUS = "status0"
COL_BOARD_COUNT = "board_counts"
COL_LOT_TYPE = "text7"
COL_SCOPE_DETAILS = "details"
COL_INVOICE_DATE = "date6"
COL_DEAL_STAGE = "deal_stage"
COL_CUSTOMERS_LINK = "connect_boards9"
COL_PROJECT_TYPE_STATUS = "status"          # Project Type status (Residential/Commercial)
COL_AIA_REQUIRED = "color_mm406ey0"         # "AIA Required?" status
COL_GFOLDER_LINK = "link_mkwr6ef9"          # "GFolder Link" — project Drive folder
# Canonical Project # (text), created 2026-06-19 (text_mm4fvj91). Keys the invoice
# identifier GVC-<year>-<Project #> and is the portal lookup search key.
COL_PROJECT_NUMBER = os.environ.get("GVC_MONDAY_COL_PROJECT_NUMBER", "text_mm4fvj91")
# "Linked Opportunity" -> Bid Board (board_relation, same column monday/co.py
# reads as P_COL_LINKED_OPPORTUNITY). Used ONLY for the invoice's estimate-$
# import shortcut (the Bid Board's rounded total) — never for identity/prefill.
COL_LINKED_OPPORTUNITY = "board_relation_mm40rg52"
# Bid Board (Opportunity) columns read through the linked-opportunity hop above.
# Same ids as adapters/monday/co.py's BID_COL_ESTIMATE_NUMBER and
# adapters/monday/estimate.py's COL_TOTAL_ROUNDED — kept local here so this
# module doesn't need a cross-adapter import for two column ids.
BID_COL_ESTIMATE_NUMBER = "numbers18"
BID_COL_TOTAL_ROUNDED = "number"

# Customers board (1919766765) column IDs (verified 2026-06-19).
CUST_COL_EMAIL = "contact_email"
CUST_COL_CONTACT_NAME = "priority"          # "Contact Name" (text)
CUST_COL_PHONE = "contact_phone"
CUST_COL_BILLING_ADDRESS = "billing_address"
CUST_COL_TYPE = "status"                    # Lead | Customer | Vendor | Partner
COL_INVOICE_TYPE = os.environ.get("GVC_MONDAY_COL_INVOICE_TYPE", "color_mm3fp150")
COL_PAY_APP_NUMBER = os.environ.get("GVC_MONDAY_COL_PAY_APP_NUMBER", "numeric_mm3fcjmn")
COL_PERIOD_END_DATE = os.environ.get("GVC_MONDAY_COL_PERIOD_END_DATE", "date_mm3fds5f")

# Tier 2 writeback columns from the roadmap — set these env vars once you've
# added the columns to the board. Until set, writeback skips gracefully.
WRITEBACK_COL_STRIPE_CUSTOMER = os.environ.get("GVC_MONDAY_COL_STRIPE_CUSTOMER")
WRITEBACK_COL_STRIPE_INVOICE = os.environ.get("GVC_MONDAY_COL_STRIPE_INVOICE")
WRITEBACK_COL_HOSTED_URL = os.environ.get("GVC_MONDAY_COL_HOSTED_URL")
WRITEBACK_COL_DRIVE_LINK = os.environ.get("GVC_MONDAY_COL_DRIVE_LINK")

# Per-customer pricing rules (the locked rates from the PandaDoc handoff).
# Match against (a) the linked customer name, (b) the builder text, in order.
CUSTOMER_RULES: dict[str, dict[str, Any]] = {
    "Zicka": {
        "billing_model": "per_board",
        "board_rate": 40.0,
        "line_description_template": "Hang and Finish {boards}Bd Drywall @ ${rate}/bd",
        "payment_terms": "Net 30",
        "default_email": "jamies@zickahomes.com",
        "default_cc": "pzicka@outlook.com",
    },
    "Berkey": {
        "billing_model": "per_board",
        "board_rate": 40.0,
        "line_description_template": "Hang and Finish {boards}Bd Drywall @ ${rate}/bd",
        "payment_terms": "Net 30",
        "default_email": "invoices1768@gmail.com",
        "default_cc": None,
    },
}


class MondayNotConfigured(Exception):
    """Raised when MONDAY_API_TOKEN is missing."""


class MondayInsufficientData(Exception):
    """Raised when a project lacks the data needed to auto-generate an invoice."""


class MondayClient:
    def __init__(self, token: Optional[str] = None) -> None:
        token = token or os.environ.get("MONDAY_API_TOKEN")
        if not token:
            raise MondayNotConfigured(
                "MONDAY_API_TOKEN env var not set. "
                "Generate one at Monday → user avatar → Developers → My API tokens, "
                "then add MONDAY_API_TOKEN=... to your .env file."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": token,
            "API-Version": MONDAY_API_VERSION,
            "Content-Type": "application/json",
        })

    def _query(self, query: str, variables: Optional[dict] = None) -> dict:
        """
        Single GraphQL POST. No retries, no 429/complexity backoff — a rate
        limit or complexity error surfaces immediately as HTTPError/RuntimeError.
        Set GVC_MONDAY_TRACE=1 to log every call's duration into get_monday_trace().
        """
        trace = monday_trace_enabled()
        t0 = time.perf_counter() if trace else 0.0
        status: Optional[int] = None
        err: Optional[str] = None
        ok = False
        try:
            resp = self.session.post(
                MONDAY_API_URL,
                json={"query": query, "variables": variables or {}},
                timeout=30,
            )
            status = resp.status_code
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                err = str(data["errors"])[:400]
                raise RuntimeError(f"Monday API error: {data['errors']}")
            ok = True
            return data["data"]
        except Exception as exc:  # noqa: BLE001 — re-raise after optional trace
            if err is None:
                err = f"{type(exc).__name__}: {exc}"[:400]
            raise
        finally:
            if trace:
                _record_trace(
                    ms=round((time.perf_counter() - t0) * 1000.0, 1),
                    ok=ok,
                    status=status,
                    err=err,
                    query_head=_query_head(query),
                    vars_keys=sorted((variables or {}).keys()),
                )

    # ---- Read ----

    def get_project(self, item_id: int) -> dict:
        """Fetch a Projects board item with all columns we care about."""
        query = """
        query ($itemId: [ID!]) {
          items(ids: $itemId) {
            id
            name
            url
            column_values {
              id
              text
              value
              ... on BoardRelationValue {
                linked_item_ids
                linked_items { id name board { id } }
              }
            }
          }
        }
        """
        data = self._query(query, {"itemId": [str(item_id)]})
        items = data.get("items", [])
        if not items:
            raise MondayInsufficientData(f"Monday item {item_id} not found.")
        return items[0]

    def get_customer(self, customer_item_id: int) -> dict:
        """Fetch a Customers board entry (email, billing address, etc.)."""
        query = """
        query ($itemId: [ID!]) {
          items(ids: $itemId) {
            id
            name
            column_values { id text value }
          }
        }
        """
        data = self._query(query, {"itemId": [str(customer_item_id)]})
        items = data.get("items", [])
        if not items:
            raise MondayInsufficientData(f"Customer item {customer_item_id} not found.")
        return items[0]

    # ---- Build invoice JSON ----

    def build_invoice_dict(self, item_id: int) -> dict:
        """
        Fetch a Monday project and assemble the canonical invoice-JSON shape.
        Raises MondayInsufficientData with a clear message if data is missing.
        """
        project = self.get_project(item_id)
        cols = {cv["id"]: cv for cv in project["column_values"]}

        def col_text(col_id: str) -> Optional[str]:
            v = cols.get(col_id, {}).get("text") or None
            return v.strip() if v else None

        # ---- Customer resolution ----
        linked_customer_ids = []
        cust_col = cols.get(COL_CUSTOMERS_LINK, {})
        if cust_col.get("linked_item_ids"):
            linked_customer_ids = cust_col["linked_item_ids"]

        customer_record = None
        if linked_customer_ids:
            customer_record = self.get_customer(int(linked_customer_ids[0]))

        builder = col_text(COL_BUILDER)
        customer_name = (customer_record["name"] if customer_record else builder) or None

        if not customer_name:
            raise MondayInsufficientData(
                f"Monday item {item_id}: no customer linked and no Builder text set."
            )

        # Look up customer billing email + address from the Customers board entry
        customer_email = None
        customer_billing_address = None
        if customer_record:
            ccols = {cv["id"]: cv for cv in customer_record["column_values"]}
            customer_email = (ccols.get("contact_email", {}).get("text") or "").strip() or None
            customer_billing_address = (
                ccols.get("billing_address", {}).get("text") or ""
            ).strip() or None

        # ---- Apply pricing rule ----
        rule = _match_customer_rule(customer_name, builder)
        if not rule:
            raise MondayInsufficientData(
                f"Monday item {item_id}: customer '{customer_name}' has no auto-pricing "
                f"rule. Add an Invoice Amount column to Monday or use --input <json>."
            )

        # Fallbacks if the Customers board didn't yield email/address
        if not customer_email and rule.get("default_email"):
            customer_email = rule["default_email"]
        if not customer_email:
            raise MondayInsufficientData(
                f"Monday item {item_id}: no email for customer '{customer_name}'."
            )

        # ---- Build line items ----
        if rule["billing_model"] == "per_board":
            board_count = _parse_board_count(col_text(COL_BOARD_COUNT))
            if board_count is None or board_count <= 0:
                raise MondayInsufficientData(
                    f"Monday item {item_id}: Per-Board customer '{customer_name}' "
                    f"requires a Board Count. Found: {col_text(COL_BOARD_COUNT)!r}"
                )
            amount = round(board_count * rule["board_rate"], 2)
            lot = col_text(COL_LOT_TYPE)
            job_short = project["name"]
            line_desc = rule["line_description_template"].format(
                boards=board_count, rate=int(rule["board_rate"]) if rule["board_rate"].is_integer() else rule["board_rate"]
            ) + f" — {job_short}"
            line_detail = (
                col_text(COL_SCOPE_DETAILS)
                or f"Hang and finish drywall. Builder supplies drywall; GVC supplies labor and accessories."
            )
            line_items = [{
                "description": line_desc,
                "detail": line_detail,
                "amount": amount,
            }]
        else:
            raise MondayInsufficientData(
                f"Billing model '{rule['billing_model']}' not yet implemented in auto-pricing."
            )

        # ---- Dates ----
        issue_text = col_text(COL_INVOICE_DATE)
        if issue_text:
            try:
                issue_date = datetime.strptime(issue_text, "%Y-%m-%d").date()
            except ValueError:
                issue_date = date.today()
        else:
            issue_date = date.today()
        due_date = issue_date + timedelta(days=30)

        # ---- Identifier = the canonical project number (carried from the
        # estimate). No separate invoice numbering. ----
        project_number = col_text(COL_PROJECT_NUMBER)
        if not project_number:
            raise MondayInsufficientData(
                f"Monday item {item_id}: no Project # set. The invoice number is the "
                f"project's Project # (carried over from the estimate) — set it on the "
                f"project, then retry."
            )
        identifier = project_number

        # ---- Assemble ----
        site_address = col_text(COL_LOCATION) or ""
        billing_address = customer_billing_address or site_address

        invoice_type = (col_text(COL_INVOICE_TYPE) or "standard").lower()
        pay_app_raw = col_text(COL_PAY_APP_NUMBER)
        pay_app_number = int(float(pay_app_raw)) if pay_app_raw else None
        period_end_date = col_text(COL_PERIOD_END_DATE)

        return {
            "client": {
                "name": customer_name,
                "contact_name": col_text(COL_CONTACT_NAME),
                "email": customer_email,
                "billing_address": billing_address,
                "stripe_customer_id": None,
            },
            "job": {
                "name": project["name"],
                "site_address": site_address,
            },
            "invoice": {
                "identifier": identifier,
                "issue_date": issue_date.strftime("%Y-%m-%d"),
                "due_date": due_date.strftime("%Y-%m-%d"),
                "payment_terms": rule.get("payment_terms", "Net 30"),
                "notes": "Thank you for your business. ACH preferred — no processing fee. "
                         "Credit card payments incur a 3% processing fee.",
                "line_items": line_items,
                "invoice_type": invoice_type,
                "pay_app_number": pay_app_number,
                "period_end_date": period_end_date,
            },
            "_monday": {
                "item_id": int(item_id),
                "item_url": project.get("url"),
                "linked_customer_id": linked_customer_ids[0] if linked_customer_ids else None,
                "cc_email": rule.get("default_cc"),
            },
        }

    # ---- Read: Invoices Sent ledger (for the check-deposit tool) ----

    def fetch_invoice_rows(self, *, open_only: bool = True,
                           board_id: Optional[int] = None) -> list[dict]:
        """
        Read rows off the 'Invoices Sent' ledger, normalized for check matching.
        Each row: {monday_item_id, identifier, amount, status, stripe_invoice_id,
        customer, job, drive_folder_url}. `open_only` drops Paid/Void.
        """
        board_id = board_id or INVOICES_SENT_BOARD_ID
        query = """
        query ($boardId: [ID!], $cursor: String) {
          boards(ids: $boardId) {
            items_page(limit: 200, cursor: $cursor) {
              cursor
              items {
                id
                name
                column_values(ids: %s) {
                  id
                  text
                  value
                  ... on BoardRelationValue { linked_items { id name } }
                }
              }
            }
          }
        }
        """ % _invoice_column_ids()
        rows: list[dict] = []
        cursor: Optional[str] = None
        while True:
            data = self._query(query, {"boardId": [str(board_id)], "cursor": cursor})
            boards = data.get("boards") or []
            if not boards:
                break
            page = boards[0]["items_page"]
            for item in page["items"]:
                row = self._normalize_invoice_row(item)
                if open_only and (row.get("status") or "").strip().lower() in CLOSED_INVOICE_STATUSES:
                    continue
                rows.append(row)
            cursor = page.get("cursor")
            if not cursor:
                break
        return rows

    @staticmethod
    def _normalize_invoice_row(item: dict) -> dict:
        cols = {cv["id"]: cv for cv in item["column_values"]}

        def text(cid: str) -> Optional[str]:
            v = cols.get(cid, {}).get("text")
            return v.strip() if v else None

        amount = None
        at = text(INV_COL_AMOUNT)
        if at:
            try:
                amount = float(at.replace(",", "").replace("$", ""))
            except ValueError:
                amount = None

        customer = None
        linked = cols.get(INV_COL_CUSTOMER, {}).get("linked_items") or []
        if linked:
            customer = linked[0].get("name")
        customer = customer or text(INV_COL_CUSTOMER)

        drive_url = None
        raw = cols.get(INV_COL_DRIVE_FOLDER, {}).get("value")
        if raw:
            try:
                drive_url = (json.loads(raw) or {}).get("url")
            except (json.JSONDecodeError, TypeError):
                drive_url = None

        # Identifier is the "Document #" column (UNIQUE key), NOT the item name —
        # the board's item name now holds the human-readable project name, which
        # repeats across pay apps. Fall back to the name only if Document # is blank
        # (legacy rows created before the migration).
        identifier = text(INV_COL_DOCUMENT) or item["name"]

        balance_due = None
        if INV_COL_BALANCE_DUE:
            bt = text(INV_COL_BALANCE_DUE)
            if bt:
                try:
                    balance_due = float(bt.replace(",", "").replace("$", ""))
                except ValueError:
                    balance_due = None

        return {
            "monday_item_id": int(item["id"]),
            "identifier": identifier,
            "amount": amount,
            "status": text(INV_COL_STATUS),
            "stripe_invoice_id": text(INV_COL_STRIPE_INVOICE),
            "customer": customer,
            "job": text(INV_COL_JOB),
            "drive_folder_url": drive_url,
            "note": text(INV_COL_NOTE),  # None unless the note column was fetched
            "balance_due": balance_due,  # None unless the column is configured + set
            "issue_date": text(INV_COL_ISSUE_DATE),
            "emailed_on": text(INV_COL_EMAILED_ON),
        }

    def stamp_invoice_emailed(self, item_id: int, date_str: str,
                              *, current_status: Optional[str] = None,
                              board_id: Optional[int] = None) -> None:
        """
        Sent-watcher writeback: set 'Emailed on' to `date_str` (YYYY-MM-DD) and,
        ONLY when the row still carries the creation-time label ("Draft Ready"),
        flip Status to "Invoice Sent". Any other status — Paid, Void, Partially
        Paid, Overdue, or an office-set "Invoice Sent" — is left untouched: the
        watcher records the send date, it never regresses a money state.
        """
        board_id = board_id or INVOICES_SENT_BOARD_ID
        values: dict[str, Any] = {INV_COL_EMAILED_ON: {"date": date_str}}
        if (current_status or "").strip().lower() == LEDGER_CREATE_STATUS_LABEL.lower():
            values[INV_COL_STATUS] = {"label": SENT_INVOICE_STATUS_LABEL}
        self._set_invoice_columns(int(item_id), values, board_id=board_id)

    def get_invoice_row(self, item_id: int,
                        board_id: Optional[int] = None) -> Optional[dict]:
        """
        Fetch ONE 'Invoices Sent' row by item id, normalized like
        fetch_invoice_rows (plus the free-text note). Used by the check-deposit
        commit step so the live write never trusts client-supplied stripe id /
        folder / status. Returns None if the item doesn't exist.
        """
        query = """
        query ($itemId: [ID!]) {
          items(ids: $itemId) {
            id
            name
            column_values(ids: %s) {
              id
              text
              value
              ... on BoardRelationValue { linked_items { id name } }
            }
          }
        }
        """ % _invoice_column_ids(INV_COL_NOTE)
        data = self._query(query, {"itemId": [str(item_id)]})
        items = data.get("items") or []
        if not items:
            return None
        return self._normalize_invoice_row(items[0])

    # ---- Write (writeback after live invoice run) ----

    def writeback(
        self,
        item_id: int,
        *,
        stripe_customer_id: Optional[str] = None,
        stripe_invoice_id: Optional[str] = None,
        hosted_invoice_url: Optional[str] = None,
        drive_web_view_link: Optional[str] = None,
    ) -> dict:
        """
        Update writeback columns on the Monday project item.
        Skips any column whose env-configured ID isn't set yet.
        Returns: {written: [...], skipped: [...]}
        """
        column_values: dict[str, Any] = {}
        skipped: list[str] = []

        def add(col_id: Optional[str], value: Any, label: str) -> None:
            if not col_id:
                skipped.append(label)
                return
            column_values[col_id] = value

        if stripe_customer_id is not None:
            add(WRITEBACK_COL_STRIPE_CUSTOMER, stripe_customer_id, "Stripe Customer ID")
        if stripe_invoice_id is not None:
            add(WRITEBACK_COL_STRIPE_INVOICE, stripe_invoice_id, "Stripe Invoice ID")
        if hosted_invoice_url is not None:
            add(WRITEBACK_COL_HOSTED_URL,
                {"url": hosted_invoice_url, "text": "Pay Now (Stripe)"},
                "Hosted Invoice URL")
        if drive_web_view_link is not None:
            add(WRITEBACK_COL_DRIVE_LINK,
                {"url": drive_web_view_link, "text": "Invoice PDF (Drive)"},
                "PDF Drive Link")

        if not column_values:
            return {"written": [], "skipped": skipped}

        query = """
        mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
          change_multiple_column_values(
            board_id: $boardId,
            item_id: $itemId,
            column_values: $values
          ) { id }
        }
        """
        self._query(query, {
            "boardId": str(PROJECTS_BOARD_ID),
            "itemId": str(item_id),
            "values": json.dumps(column_values),
        })
        return {"written": list(column_values.keys()), "skipped": skipped}

    def set_invoice_paid(
        self,
        item_id: int,
        *,
        check_no: Optional[str] = None,
        date_str: Optional[str] = None,
        board_id: Optional[int] = None,
        covers: Optional[list[str]] = None,
        note_line: Optional[str] = None,
    ) -> dict:
        """
        Mark an 'Invoices Sent' row Paid and append a note. Writes the Status
        column (by label "Paid") and appends a note line to the free-text note
        column on the INVOICES board (not Projects).

        By default the note is "Paid by check #<no> on <date>" (the paid-by-
        check flow). Pass `note_line` to use a DIFFERENT note instead — e.g.
        the Stripe-paid webhook flow passes "Paid via Stripe online on
        <date>" so the note reflects how the money actually came in. When one
        check pays several invoices, pass `covers` (all identifiers on the
        check) so each row's note records its siblings (ignored when
        `note_line` is explicitly set).

        Idempotent: if the note line is already present it isn't duplicated, so a
        partial-failure retry is safe. Returns {item_id, status, note_appended}.
        """
        board_id = board_id or INVOICES_SENT_BOARD_ID
        line = note_line or f"Paid by check #{check_no or '?'} on {date_str or 'unknown date'}"
        if note_line is None and covers and len(covers) > 1:
            line += f" (check covers {len(covers)} invoices: {', '.join(covers)})"

        existing_note = ""
        try:
            row = self.get_invoice_row(item_id, board_id=board_id)
            existing_note = (row or {}).get("note") or ""
        except Exception:  # noqa: BLE001 — note is best-effort; status is the truth
            existing_note = ""

        appended = line not in existing_note
        new_note = (existing_note + ("\n" if existing_note else "") + line) if appended else existing_note

        column_values: dict[str, Any] = {INV_COL_STATUS: {"label": PAID_INVOICE_STATUS_LABEL}}
        if appended:
            # long_text columns take an object {"text": ...}, not a bare string.
            column_values[INV_COL_NOTE] = {"text": new_note}
        if INV_COL_BALANCE_DUE:
            column_values[INV_COL_BALANCE_DUE] = "0"  # fully settled

        query = """
        mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
          change_multiple_column_values(
            board_id: $boardId,
            item_id: $itemId,
            column_values: $values
          ) { id }
        }
        """
        self._query(query, {
            "boardId": str(board_id),
            "itemId": str(item_id),
            "values": json.dumps(column_values),
        })
        return {"item_id": item_id, "status": PAID_INVOICE_STATUS_LABEL, "note_appended": appended}

    def set_invoice_partially_paid(
        self,
        item_id: int,
        *,
        note_line: str,
        remaining_cents: Optional[int] = None,
        board_id: Optional[int] = None,
    ) -> dict:
        """
        Mark an 'Invoices Sent' row PARTIALLY paid: Status -> "Partially Paid"
        (label auto-created on first use), append the partial-payment note line
        (idempotent — never duplicated, so a retry is safe), and sync the
        Balance Due column to the invoice's remaining cents (Stripe is the
        source; this column is the board-side cache the check picker matches
        against). "Partially Paid" is an OPEN-class status — the row stays in
        the picker for the follow-up check.
        Returns {item_id, status, note_appended}.
        """
        board_id = board_id or INVOICES_SENT_BOARD_ID

        existing_note = ""
        try:
            row = self.get_invoice_row(item_id, board_id=board_id)
            existing_note = (row or {}).get("note") or ""
        except Exception:  # noqa: BLE001 — note is best-effort; status is the truth
            existing_note = ""

        appended = bool(note_line) and note_line not in existing_note
        new_note = (existing_note + ("\n" if existing_note else "") + note_line) if appended else existing_note

        column_values: dict[str, Any] = {
            INV_COL_STATUS: {"label": PARTIALLY_PAID_STATUS_LABEL}}
        if appended:
            column_values[INV_COL_NOTE] = {"text": new_note}
        if INV_COL_BALANCE_DUE and remaining_cents is not None:
            column_values[INV_COL_BALANCE_DUE] = f"{remaining_cents / 100:.2f}"

        # create_labels_if_missing: the "Partially Paid" label may not exist on
        # the Status column yet — first write creates it (same pattern as the
        # Commission Recipient labels on the Bid Board).
        query = """
        mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
          change_multiple_column_values(
            board_id: $boardId,
            item_id: $itemId,
            column_values: $values,
            create_labels_if_missing: true
          ) { id }
        }
        """
        self._query(query, {
            "boardId": str(board_id),
            "itemId": str(item_id),
            "values": json.dumps(column_values),
        })
        return {"item_id": item_id, "status": PARTIALLY_PAID_STATUS_LABEL,
                "note_appended": appended}

    def set_invoice_void(
        self,
        item_id: int,
        *,
        note: Optional[str] = None,
        board_id: Optional[int] = None,
    ) -> dict:
        """
        Mark an 'Invoices Sent' row Void and optionally append a note (e.g.
        "Voided — reissued as GVC-2026-MV-007 Rev 1 on 2026-06-23"). Mirrors
        set_invoice_paid: writes the Status column by label "Void" and appends to
        the free-text note column. Idempotent — the note isn't duplicated, so a
        partial-failure retry is safe. Returns {item_id, status, note_appended}.
        """
        board_id = board_id or INVOICES_SENT_BOARD_ID

        existing_note = ""
        try:
            row = self.get_invoice_row(item_id, board_id=board_id)
            existing_note = (row or {}).get("note") or ""
        except Exception:  # noqa: BLE001 — note is best-effort; status is the truth
            existing_note = ""

        appended = bool(note) and note not in existing_note
        new_note = (existing_note + ("\n" if existing_note else "") + note) if appended else existing_note

        column_values: dict[str, Any] = {INV_COL_STATUS: {"label": "Void"}}
        if appended:
            column_values[INV_COL_NOTE] = {"text": new_note}

        query = """
        mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
          change_multiple_column_values(
            board_id: $boardId,
            item_id: $itemId,
            column_values: $values
          ) { id }
        }
        """
        self._query(query, {
            "boardId": str(board_id),
            "itemId": str(item_id),
            "values": json.dumps(column_values),
        })
        return {"item_id": item_id, "status": "Void", "note_appended": appended}

    def repoint_invoice_stripe(
        self,
        item_id: int,
        stripe_invoice_id: str,
        *,
        hosted_url: Optional[str] = None,
        note: Optional[str] = None,
        board_id: Optional[int] = None,
    ) -> dict:
        """
        Point an existing Invoices-board row at a DIFFERENT Stripe invoice —
        the self-heal for a stale row that still references a voided/reissued
        invoice (root cause of the CU-0166 'Voided invoices cannot be paid'
        failure, 2026-07-06). Touches ONLY the Stripe id + pay-link columns,
        plus an optional idempotent note line; Status is never changed here.
        Returns {item_id, written, note_appended}.
        """
        board_id = board_id or INVOICES_SENT_BOARD_ID
        column_values: dict[str, Any] = {INV_COL_STRIPE_INVOICE: stripe_invoice_id}
        if hosted_url:
            column_values[INV_COL_STRIPE_URL] = {"url": hosted_url, "text": "Pay invoice"}

        appended = False
        if note:
            existing_note = ""
            try:
                row = self.get_invoice_row(item_id, board_id=board_id)
                existing_note = (row or {}).get("note") or ""
            except Exception:  # noqa: BLE001 — note is best-effort
                existing_note = ""
            appended = note not in existing_note
            if appended:
                column_values[INV_COL_NOTE] = {
                    "text": existing_note + ("\n" if existing_note else "") + note
                }

        self._set_invoice_columns(int(item_id), column_values, board_id=board_id)
        return {"item_id": item_id, "written": sorted(column_values),
                "note_appended": appended}

    # ---- Read: project lookup by Project # (canonical portal prefill) ----

    def find_project_by_number(self, project_number: str) -> Optional[dict]:
        """
        Find a Projects-board item by its canonical Project # (COL_PROJECT_NUMBER).
        Returns {item_id, name} for the best match, or None.

        Accepts bare core or EST-/PRO-/INV- (Project # cells store PRO-{core}).
        Probes Monday with search_needles so EST-/INV- paste still hits.
        Prefers an exact PRO-/core match on a non-CO parent; skips top-level
        ``CO.{n}-…`` items when ranking so a bare-core contains hit does not
        adopt a change-order pulse.

        Short-TTL cached (search TTL) — Billing/Invoice deep links often hit
        the same Project # twice in one sitting.
        """
        raw = (project_number or "").strip()
        if not raw:
            return None
        cache_key = (
            "find:project_by_number:"
            + (for_project(raw) if is_spine_number(raw) else raw).lower()
        )
        return monday_cache.get_or_set(
            cache_key,
            lambda: self._find_project_by_number_uncached(raw),
            ttl=monday_cache.search_ttl(),
        )

    def _find_project_by_number_uncached(self, raw: str) -> Optional[dict]:
        needles = search_needles(raw) or [raw]
        query = """
        query ($boardId: [ID!], $val: CompareValue!, $col: [String!]) {
          boards(ids: $boardId) {
            items_page(limit: 25, query_params: {
              rules: [{column_id: "%s", compare_value: $val, operator: contains_text}]}) {
              items { id name column_values(ids: $col) { id text } }
            }
          }
        }
        """ % COL_PROJECT_NUMBER

        def _probe(needle: str) -> list[dict]:
            # Fresh client per thread — requests.Session is not thread-safe.
            token = None
            try:
                token = self.session.headers.get("Authorization")
            except Exception:  # noqa: BLE001
                token = None
            local = MondayClient(token=token) if token else MondayClient()
            data = local._query(query, {
                "boardId": [str(PROJECTS_BOARD_ID)],
                "val": needle,
                "col": [COL_PROJECT_NUMBER],
            })
            out: list[dict] = []
            for board in data.get("boards") or []:
                out.extend((board.get("items_page") or {}).get("items") or [])
            return out

        seen: set[int] = set()
        candidates: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(needles)))) as pool:
            futs = {pool.submit(_probe, n): n for n in needles}
            for fut in as_completed(futs):
                try:
                    items = fut.result()
                except Exception as exc:  # noqa: BLE001 — one needle fail ≠ total fail
                    print(f"[monday] find_project_by_number needle "
                          f"{futs[fut]!r} failed: {exc}", file=sys.stderr)
                    continue
                for it in items:
                    try:
                        iid = int(it["id"])
                    except (TypeError, ValueError, KeyError):
                        continue
                    if iid in seen:
                        continue
                    seen.add(iid)
                    candidates.append(it)
        if not candidates:
            return None

        want_core = (core_number(raw) or "").lower()
        want_pro = for_project(raw).lower() if is_spine_number(raw) else ""
        want_raw = raw.lower()

        def _project_text(it: dict) -> str:
            cv = {c["id"]: (c.get("text") or "") for c in it.get("column_values") or []}
            return (cv.get(COL_PROJECT_NUMBER) or "").strip()

        def _is_co_row(it: dict) -> bool:
            return (it.get("name") or "").strip().upper().startswith("CO.")

        def _exact(it: dict) -> bool:
            text = _project_text(it)
            low = text.lower()
            if low and low in {want_raw, want_pro}:
                return True
            cell_core = (core_number(text) or "").lower()
            return bool(want_core and cell_core == want_core)

        for it in candidates:
            if not _is_co_row(it) and _exact(it):
                return {"item_id": int(it["id"]), "name": it.get("name") or ""}
        for it in candidates:
            if _exact(it):
                return {"item_id": int(it["id"]), "name": it.get("name") or ""}
        for it in candidates:
            if not _is_co_row(it):
                return {"item_id": int(it["id"]), "name": it.get("name") or ""}
        it0 = candidates[0]
        return {"item_id": int(it0["id"]), "name": it0.get("name") or ""}

    def _read_bid_snapshot(self, bid_item_id: int) -> dict:
        """
        Read the Estimate # + rounded total off a Bid Board item — the coarse,
        always-quick half of the invoice's estimate-$ import shortcut (the full
        line items come from the Drive JSON sidecar instead; see
        subsystems.invoice.estimate_import + app.service.ui_invoice_lookup).
        Raises on a hard API error; callers treat this as best-effort.
        """
        query = """
        query ($itemId: [ID!], $cols: [String!]) {
          items(ids: $itemId) { column_values(ids: $cols) { id text } }
        }
        """
        data = self._query(query, {
            "itemId": [str(bid_item_id)],
            "cols": [BID_COL_ESTIMATE_NUMBER, BID_COL_TOTAL_ROUNDED],
        })
        items = data.get("items") or []
        texts = {
            cv["id"]: (cv.get("text") or "").strip()
            for cv in (items[0].get("column_values") or [])
        } if items else {}
        return {
            "estimate_number": texts.get(BID_COL_ESTIMATE_NUMBER) or None,
            "total_rounded_text": texts.get(BID_COL_TOTAL_ROUNDED) or None,
        }

    def build_invoice_prefill(self, item_id: int) -> dict:
        """
        Read a Projects-board item and assemble an EDITABLE invoice-form prefill:
        the project is the source of truth for who/what (customer, contact, billing
        identity, job name/site, classification, Drive folder); the office still
        enters the dollar line items (those come from the billing materials, not
        the project record). Mirrors monday_estimate.lookup_bid.

        Returns {client, job, invoice, _matched, _notes}. `job.monday_item_id` is
        set so the live run links the ledger row + writes back to THIS project.
        Raises MondayInsufficientData if the item doesn't exist.
        """
        project = self.get_project(item_id)
        cols = {cv["id"]: cv for cv in project["column_values"]}
        notes: list[str] = []

        def col_text(col_id: str) -> Optional[str]:
            v = cols.get(col_id, {}).get("text") or None
            return v.strip() if v else None

        # ---- Customer (linked) ----
        linked_ids = cols.get(COL_CUSTOMERS_LINK, {}).get("linked_item_ids") or []
        customer_name = None
        customer_email = customer_phone = customer_contact = customer_billing = None
        if linked_ids:
            try:
                cust = self.get_customer(int(linked_ids[0]))
                customer_name = cust.get("name")
                ccols = {cv["id"]: cv for cv in cust["column_values"]}
                customer_email = (ccols.get(CUST_COL_EMAIL, {}).get("text") or "").strip() or None
                customer_phone = (ccols.get(CUST_COL_PHONE, {}).get("text") or "").strip() or None
                customer_contact = (ccols.get(CUST_COL_CONTACT_NAME, {}).get("text") or "").strip() or None
                customer_billing = (ccols.get(CUST_COL_BILLING_ADDRESS, {}).get("text") or "").strip() or None
            except Exception:  # noqa: BLE001 — fall back to builder text below
                notes.append("Linked customer could not be read; using Builder text.")
        if not customer_name:
            customer_name = col_text(COL_BUILDER)
        if not customer_name:
            notes.append("No customer linked and no Builder set — fill the customer in below.")

        client: dict = {}
        if customer_name:
            client["name"] = customer_name
        contact = customer_contact or col_text(COL_CONTACT_NAME)
        if contact:
            client["contact_name"] = contact
        if customer_email:
            client["email"] = customer_email
        else:
            notes.append("No billing email on the linked customer — add one before finalizing.")
        if customer_phone:
            client["phone"] = customer_phone
        site_address = col_text(COL_LOCATION)
        if customer_billing:
            client["billing_address"] = customer_billing
        elif site_address:
            client["billing_address"] = site_address

        # ---- Classification (AIA Required? + Project Type) ----
        aia_text = (col_text(COL_AIA_REQUIRED) or "").lower()
        ptype_text = (col_text(COL_PROJECT_TYPE_STATUS) or "").lower()
        is_aia = any(k in aia_text for k in ("yes", "required", "aia", "true"))
        if is_aia:
            monday_job_type, project_type = "AIA", "commercial"
        elif "resid" in ptype_text:
            monday_job_type, project_type = "Residential", "residential"
        elif "commerc" in ptype_text:
            monday_job_type, project_type = "Commercial", "commercial"
        else:
            monday_job_type, project_type = None, None
            notes.append("Project Type not set on the project — pick Residential/Commercial/AIA below.")

        # ---- Drive project folder (GFolder Link) ----
        drive_project_folder_url = None
        raw = cols.get(COL_GFOLDER_LINK, {}).get("value")
        if raw:
            try:
                drive_project_folder_url = (json.loads(raw) or {}).get("url")
            except (json.JSONDecodeError, TypeError):
                drive_project_folder_url = None
        drive_project_folder_url = drive_project_folder_url or col_text(COL_GFOLDER_LINK)

        # ---- Project # → invoice Document # = INV-{same core} ----
        # Estimate assigns the core once (EST-…); Job Start stamps PRO-… on
        # the project; invoicing uses INV-… — letters change, number doesn't.
        # When Project # is still empty, fall back to the linked bid's
        # Estimate # so Look-up-&-fill still auto-populates the invoice number.
        project_number = col_text(COL_PROJECT_NUMBER)
        if project_number and is_spine_number(project_number):
            project_number = for_project(project_number)

        # ---- Estimate $ import shortcut (Bid Board rounded total) ----
        # Best-effort only: dollars still come from the office below unless
        # they opt into this on the invoice form (see estimate_import). A
        # missing link or a Monday hiccup here must never break the lookup.
        bid_estimate_number = None
        bid_total_text = None
        bid_ids = (cols.get(COL_LINKED_OPPORTUNITY, {}) or {}).get("linked_item_ids") or []
        if bid_ids:
            try:
                snap = self._read_bid_snapshot(int(bid_ids[0]))
                bid_estimate_number = snap.get("estimate_number")
                bid_total_text = snap.get("total_rounded_text")
            except Exception:  # noqa: BLE001 — the import shortcut is best-effort
                notes.append(
                    "Could not read the linked estimate's Monday total — "
                    "enter dollars manually or try the import card again."
                )

        spine_src = project_number or bid_estimate_number
        suggested_identifier = (
            for_invoice(spine_src) if spine_src and is_spine_number(spine_src) else None
        )

        job: dict = {"name": project["name"], "monday_item_id": int(item_id)}
        if site_address:
            job["site_address"] = site_address
        if project_type:
            job["project_type"] = project_type
        if monday_job_type:
            job["monday_job_type"] = monday_job_type
        if project_number:
            job["project_number"] = project_number
        elif bid_estimate_number and is_spine_number(bid_estimate_number):
            # Soft-fill Project # from the estimate spine so finalize can also
            # derive INV-… when the office accepts without retyping.
            job["project_number"] = for_project(bid_estimate_number)
        if drive_project_folder_url:
            job["drive_project_folder_url"] = drive_project_folder_url

        invoice: dict = {}
        if suggested_identifier:
            invoice["identifier"] = suggested_identifier
        scope = col_text(COL_SCOPE_DETAILS)

        notes.append("Line items and amounts aren't stored on the project — add them below.")
        return {
            "client": client,
            "job": job,
            "invoice": invoice,
            "_matched": {
                "item_id": int(item_id),
                "item_name": project["name"],
                "project_number": project_number or job.get("project_number"),
                "url": project.get("url") or (
                    f"https://greenvalleycontractors.monday.com/boards/"
                    f"{PROJECTS_BOARD_ID}/pulses/{item_id}"),
                "scope_summary": scope,
            },
            "_notes": notes,
            "_bid_snapshot": {
                "estimate_number": bid_estimate_number,
                "monday_total_raw": bid_total_text,
            },
        }

    # ---- Read: customer search (Customers board) ----

    def search_customers(self, query_text: str, *, limit: int = 10,
                         customers_only: bool = True) -> list[dict]:
        """
        Search the Customer & Vendor Directory by name. Returns
        [{item_id, name, email, contact_name, phone, billing_address, type}],
        best matches first. `customers_only` keeps Customer/Lead/Partner and
        drops rows explicitly typed Vendor. Short-TTL cached — the invoice
        form types through this on every keystroke (debounced in the UI).
        """
        needle = (query_text or "").strip()
        if not needle:
            return []
        cache_key = (
            f"search:customers:{needle.lower()}:{int(limit)}:"
            f"{1 if customers_only else 0}"
        )
        return monday_cache.get_or_set(
            cache_key,
            lambda: self._search_customers_uncached(
                needle, limit=limit, customers_only=customers_only),
            ttl=monday_cache.search_ttl(),
        )

    def _search_customers_uncached(self, needle: str, *, limit: int = 10,
                                   customers_only: bool = True) -> list[dict]:
        query = """
        query ($boardId: [ID!], $val: CompareValue!) {
          boards(ids: $boardId) {
            items_page(limit: %d, query_params: {
              rules: [{column_id: "name", compare_value: $val, operator: contains_text}]}) {
              items {
                id name
                column_values(ids: ["contact_email","priority","contact_phone","billing_address","status"]) {
                  id text
                }
              }
            }
          }
        }
        """ % max(1, min(limit, 50))
        data = self._query(query, {"boardId": [str(CUSTOMERS_BOARD_ID)], "val": needle})
        out: list[dict] = []
        for board in data.get("boards") or []:
            for it in (board.get("items_page") or {}).get("items") or []:
                cv = {c["id"]: (c.get("text") or "").strip() for c in it.get("column_values") or []}
                ctype = cv.get(CUST_COL_TYPE, "")
                if customers_only and ctype.lower() == "vendor":
                    continue
                out.append({
                    "item_id": int(it["id"]),
                    "name": it.get("name") or "",
                    "email": cv.get(CUST_COL_EMAIL) or None,
                    "contact_name": cv.get(CUST_COL_CONTACT_NAME) or None,
                    "phone": cv.get(CUST_COL_PHONE) or None,
                    "billing_address": cv.get(CUST_COL_BILLING_ADDRESS) or None,
                    "type": ctype or None,
                })
        return out

    def resolve_customer_item_id(self, customer_name: str) -> Optional[int]:
        """
        Resolve a customer name to a Customers-board item id for the ledger
        board_relation. Exact (case-insensitive) name match wins; otherwise prefer
        a "Billing"/"Invoicing" variant, then Type=Customer, then first result.
        Returns None if nothing matches (caller leaves the relation empty + notes it).
        """
        name = (customer_name or "").strip()
        if not name:
            return None
        matches = self.search_customers(name, limit=25, customers_only=False)
        if not matches:
            return None
        low = name.lower()
        for m in matches:
            if m["name"].strip().lower() == low:
                return m["item_id"]
        for m in matches:
            if any(k in m["name"].lower() for k in ("billing", "invoicing")):
                return m["item_id"]
        for m in matches:
            if (m.get("type") or "").lower() == "customer":
                return m["item_id"]
        return matches[0]["item_id"]

    # ---- Read: Invoices ledger dedupe by Document # ----

    def find_invoice_row_by_document(self, identifier: str,
                                     board_id: Optional[int] = None) -> Optional[dict]:
        """
        Find an existing Invoices-board row whose Document # equals `identifier`
        (the UNIQUE reconciliation key).

        Returns {item_id, item_url, name, stripe_invoice_id} or None.
        ``stripe_invoice_id`` may be empty when the ledger row predates writeback
        or Stripe was skipped — callers must treat it as optional.
        """
        board_id = board_id or INVOICES_SENT_BOARD_ID
        ident = (identifier or "").strip()
        if not ident:
            return None
        query = """
        query ($boardId: [ID!], $val: CompareValue!) {
          boards(ids: $boardId) {
            items_page(limit: 10, query_params: {
              rules: [{column_id: "%s", compare_value: $val, operator: contains_text}]}) {
              items {
                id
                name
                column_values(ids: ["%s", "%s"]) { id text }
              }
            }
          }
        }
        """ % (INV_COL_DOCUMENT, INV_COL_DOCUMENT, INV_COL_STRIPE_INVOICE)
        data = self._query(query, {"boardId": [str(board_id)], "val": ident})
        low = ident.lower()
        for board in data.get("boards") or []:
            for it in (board.get("items_page") or {}).get("items") or []:
                cv = {c["id"]: (c.get("text") or "") for c in it.get("column_values") or []}
                if (cv.get(INV_COL_DOCUMENT) or "").strip().lower() == low:
                    iid = int(it["id"])
                    return {
                        "item_id": iid,
                        "name": it.get("name") or "",
                        "item_url": (
                            f"https://greenvalleycontractors.monday.com/boards/"
                            f"{board_id}/pulses/{iid}"
                        ),
                        "stripe_invoice_id": (
                            (cv.get(INV_COL_STRIPE_INVOICE) or "").strip() or None
                        ),
                    }
        return None

    # ---- Write: create/upsert an Invoices-board ledger row ----

    def upsert_invoice_row(
        self,
        *,
        identifier: str,
        item_name: str,
        amount: Optional[float],
        issue_date: Optional[str],
        due_date: Optional[str],
        job_ref: Optional[str] = None,
        job_type: Optional[str] = None,
        customer_item_id: Optional[int] = None,
        linked_project_id: Optional[int] = None,
        stripe_invoice_id: Optional[str] = None,
        stripe_hosted_url: Optional[str] = None,
        gmail_draft_url: Optional[str] = None,
        drive_folder_url: Optional[str] = None,
        drive_folder_text: Optional[str] = None,
        notes: Optional[str] = None,
        status_label: Optional[str] = None,
        board_id: Optional[int] = None,
    ) -> dict:
        """
        Create (or update, keyed on Document #) one row on the Invoices board.
        Idempotent: a second run with the same identifier updates the existing row
        rather than duplicating. On UPDATE the Status column is NOT touched, so an
        office-advanced status (e.g. "Invoice Sent", "Paid") is never reverted.

        Returns {action: created|updated, item_id, item_url}.
        """
        board_id = board_id or INVOICES_SENT_BOARD_ID

        values: dict[str, Any] = {INV_COL_DOCUMENT: identifier}
        if job_ref:
            values[INV_COL_JOB] = job_ref
        if job_type:
            values[INV_COL_JOB_TYPE] = {"label": job_type}
        if amount is not None:
            values[INV_COL_AMOUNT] = str(round(float(amount), 2))
        if issue_date:
            values[INV_COL_ISSUE_DATE] = {"date": issue_date}
        if due_date:
            values[INV_COL_DUE_DATE] = {"date": due_date}
        if stripe_invoice_id:
            values[INV_COL_STRIPE_INVOICE] = stripe_invoice_id
        if stripe_hosted_url:
            values[INV_COL_STRIPE_URL] = {"url": stripe_hosted_url, "text": "Pay invoice"}
        if gmail_draft_url:
            values[INV_COL_GMAIL] = {"url": gmail_draft_url, "text": "Open Gmail draft"}
        if drive_folder_url:
            values[INV_COL_DRIVE_FOLDER] = {"url": drive_folder_url,
                                            "text": drive_folder_text or "Invoice folder"}
        if notes:
            values[INV_COL_NOTE] = {"text": notes}
        if customer_item_id:
            values[INV_COL_CUSTOMER] = {"item_ids": [int(customer_item_id)]}
        if linked_project_id:
            values[INV_COL_LINKED_PROJECT] = {"item_ids": [int(linked_project_id)]}

        existing = self.find_invoice_row_by_document(identifier, board_id=board_id)
        if existing:
            self._set_invoice_columns(existing["item_id"], values, board_id=board_id)
            return {"action": "updated", "item_id": existing["item_id"],
                    "item_url": existing["item_url"]}

        # Create with the initial Status, then set the rest.
        values_create = dict(values)
        values_create[INV_COL_STATUS] = {"label": status_label or LEDGER_CREATE_STATUS_LABEL}
        item_id = self._create_invoice_item(board_id, item_name, LEDGER_GROUP_ID)
        self._set_invoice_columns(item_id, values_create, board_id=board_id)
        return {"action": "created", "item_id": item_id,
                "item_url": (f"https://greenvalleycontractors.monday.com/boards/"
                             f"{board_id}/pulses/{item_id}")}

    def _create_invoice_item(self, board_id: int, name: str, group_id: Optional[str]) -> int:
        query = """
        mutation ($boardId: ID!, $name: String!, $groupId: String) {
          create_item(board_id: $boardId, item_name: $name, group_id: $groupId) { id }
        }
        """
        data = self._query(query, {"boardId": str(board_id), "name": name,
                                   "groupId": group_id})
        return int(data["create_item"]["id"])

    def _set_invoice_columns(self, item_id: int, values: dict,
                             board_id: Optional[int] = None) -> None:
        if not values:
            return
        board_id = board_id or INVOICES_SENT_BOARD_ID
        query = """
        mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
          change_multiple_column_values(board_id: $boardId, item_id: $itemId,
                                        column_values: $values) { id }
        }
        """
        self._query(query, {"boardId": str(board_id), "itemId": str(item_id),
                            "values": json.dumps(values)})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AUTH_PROBE_CACHE: dict = {"at": 0.0, "result": None}


def _auth_probe_ttl() -> int:
    try:
        return int(os.environ.get("GVC_MONDAY_AUTH_PROBE_TTL", "300"))
    except ValueError:
        return 300


def probe_token(force: bool = False) -> dict:
    """
    Cached `me { name }` probe for /health — mirrors slack_notify.probe_token.

    WHY THIS EXISTS (2026-07-27): health reported `monday_configured: True`
    purely because MONDAY_API_TOKEN was SET, while the token itself had been
    dead for weeks (it belonged to a departing employee). Job Check surfaced
    it only when a crew-facing page finally called Monday. Same class of bug
    as the 2026-07-02 Slack incident, same fix: probe, don't assume.

    Returns a flat dict:
      configured    bool       — MONDAY_API_TOKEN is set
      ok            bool|None  — token actually authenticates (None = no token)
      error         str|None   — API/transport error when not ok
      account_user  str|None   — authenticated Monday user name when ok
    """
    import time

    now = time.time()
    cached = _AUTH_PROBE_CACHE.get("result")
    if not force and cached is not None and (now - _AUTH_PROBE_CACHE["at"]) < _auth_probe_ttl():
        return cached
    try:
        data = MondayClient()._query("query { me { name } }")
        name = ((data or {}).get("me") or {}).get("name")
        result = {"configured": True, "ok": True, "error": None, "account_user": name}
    except MondayNotConfigured:
        result = {"configured": False, "ok": None,
                  "error": "MONDAY_API_TOKEN not set", "account_user": None}
    except Exception as e:  # noqa: BLE001 — health must never raise
        msg = f"{type(e).__name__}: {e}"
        if "401" in msg:
            msg += " (token invalid or revoked — see the MONDAY-TOKEN-ROTATION runbook)"
        result = {"configured": True, "ok": False, "error": msg, "account_user": None}
    _AUTH_PROBE_CACHE.update({"at": now, "result": result})
    return result


def _match_customer_rule(customer_name: Optional[str], builder: Optional[str]) -> Optional[dict]:
    """Match against the customer name first, then the builder text. Case-insensitive substring."""
    candidates = [customer_name, builder]
    for cand in candidates:
        if not cand:
            continue
        low = cand.lower()
        for key, rule in CUSTOMER_RULES.items():
            if key.lower() in low:
                return rule
    return None


def _parse_board_count(raw: Optional[str]) -> Optional[int]:
    """
    Board Count is stored as text on Monday — values include '206', '80 / 80 RC1',
    '-', '80 RC1', etc. Extract the leading integer if any.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw == "-":
        return None
    import re
    m = re.match(r"\s*(\d+)", raw)
    return int(m.group(1)) if m else None


def write_invoice_ledger(
    enriched: dict,
    writeback: dict,
    *,
    linked_project_id: Optional[int] = None,
    drive_folder_id: Optional[str] = None,
    drive_folder_text: Optional[str] = None,
    job_type: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """
    Record a finalized invoice on the Invoices board (1931784889). GRACEFUL:
    returns a report dict and NEVER raises — a Monday outage must not undo a
    Stripe invoice that already exists. Idempotent on the Document # (identifier),
    so re-running a job updates the row instead of duplicating it.

    This is the server-side replacement for the log-gvc-invoice-to-monday skill:
    it fires on every LIVE invoice run regardless of source (portal, /v1 JSON, or
    Monday), so the ledger can no longer be skipped or drift from the board shape.

    `enriched` is the post-enrich() invoice dict; `writeback` is the in-flight
    writeback dict (Stripe ids/urls, gmail_draft_url). Customer is resolved by
    name to the Customers board; `linked_project_id` (the Projects item) sets the
    Linked Project relation; `job_type` overrides the derived Residential/
    Commercial/AIA label (the prefill carries job.monday_job_type).
    """
    report: dict = {"ledger_synced": False}
    try:
        try:
            mc = MondayClient()
        except MondayNotConfigured as e:
            report["ledger_status"] = f"SKIPPED — {e}"
            return report

        inv = enriched.get("invoice") or {}
        job = enriched.get("job") or {}
        client = enriched.get("client") or {}

        identifier = inv.get("identifier")
        if not identifier:
            report["ledger_status"] = "SKIPPED — invoice has no identifier"
            return report

        customer_name = client.get("name") or ""
        customer_item_id = None
        if customer_name:
            try:
                customer_item_id = mc.resolve_customer_item_id(customer_name)
            except Exception:  # noqa: BLE001 — non-fatal; row still records with a note
                customer_item_id = None
        note_text = notes
        if customer_name and not customer_item_id:
            unresolved = f'Customer link: could not resolve "{customer_name}" — link manually.'
            note_text = (note_text + "\n" + unresolved) if note_text else unresolved

        drive_folder_url = (
            f"https://drive.google.com/drive/folders/{drive_folder_id}"
            if drive_folder_id else None
        )

        result = mc.upsert_invoice_row(
            identifier=identifier,
            item_name=job.get("name") or identifier,
            amount=inv.get("total"),
            issue_date=inv.get("issue_date"),
            due_date=inv.get("due_date"),
            job_ref=job.get("site_address") or job.get("name"),
            job_type=job_type or job.get("monday_job_type"),
            customer_item_id=customer_item_id,
            linked_project_id=linked_project_id or job.get("monday_item_id"),
            stripe_invoice_id=writeback.get("stripe_invoice_id"),
            stripe_hosted_url=writeback.get("hosted_invoice_url"),
            gmail_draft_url=writeback.get("gmail_draft_url"),
            drive_folder_url=drive_folder_url,
            drive_folder_text=drive_folder_text,
            notes=note_text,
        )
        report.update({
            "ledger_synced": True,
            "ledger_action": result["action"],
            "ledger_item_id": result["item_id"],
            "ledger_item_url": result["item_url"],
            "ledger_customer_linked": bool(customer_item_id),
        })
        return report
    except Exception as e:  # noqa: BLE001 — never block the flow on a ledger failure
        report["ledger_status"] = f"FAILED — {type(e).__name__}: {e}"
        print(f"[monday-ledger] invoice ledger write failed: {e}", file=sys.stderr)
        return report
