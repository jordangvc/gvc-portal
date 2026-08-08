"""
Pure shaping for the Billing Hub queue items.
=========================================================================
No Monday I/O — adapters/monday/billing.py fetches; this module turns raw
rows into the hub payload shape (deep links, status labels, display fields).
Unit-tested in tests/test_billing_hub.py.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from adapters.monday import billing as monday_billing


def invoice_href(*, project_number: Optional[str] = None,
                 monday_item_id: Optional[Any] = None,
                 q: Optional[str] = None,
                 ops_item_id: Optional[Any] = None) -> str:
    # Forward q / ops_item_id — Ready-to-Invoice shaping passes the job name
    # when Project # is missing so /ui/invoice can still search/prefill, and
    # ops_ready so a staged P5 worksheet can load into line items.
    return monday_billing.invoice_href(
        project_number=project_number, monday_item_id=monday_item_id, q=q,
        ops_item_id=ops_item_id)


def estimate_href(*, estimate_number: Optional[str] = None,
                  q: Optional[str] = None) -> str:
    return monday_billing.estimate_href(estimate_number=estimate_number, q=q)


def jobstart_href(*, bid_id: Optional[Any] = None) -> str:
    return monday_billing.jobstart_href(bid_id=bid_id)


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def shape_ready_to_invoice(row: dict) -> dict:
    """
    Ops Ready-to-Invoice row → hub card.

    Primary CTA opens the invoice form via Project # and/or the linked
    Projects monday_item_id. Never passes the Ops item id as monday_item_id
    — invoice lookup builds prefills from the Projects board only.
    """
    project_number = _clean(row.get("project_number"))
    project_item_id = row.get("project_item_id")
    ops_item_id = row.get("item_id")
    job_q = _clean(row.get("name")) or _clean(row.get("project_name"))
    # Projects board id only — Ops pulses 404 / empty-prefill on invoice lookup.
    monday_for_invoice = project_item_id or None
    href = invoice_href(
        project_number=project_number,
        monday_item_id=monday_for_invoice,
        q=None if (project_number or monday_for_invoice) else job_q,
        ops_item_id=ops_item_id,
    )
    status_labels = [s for s in (
        _clean(row.get("billable")) and f"Billable: {row.get('billable')}",
        _clean(row.get("stage")),
        _clean(row.get("project_status")),
        _clean(row.get("ready_date")) and f"Ready {row.get('ready_date')}",
    ) if s]
    # Optional P5 staged worksheet fields (caller may attach; never required).
    proposed_total = row.get("proposed_total")
    model = _clean(row.get("model"))
    price_label = _clean(row.get("price_label"))
    if proposed_total is not None:
        try:
            proposed_total = round(float(proposed_total), 2)
        except (TypeError, ValueError):
            proposed_total = None
    if project_number:
        note = "Project # known — invoice form will look it up."
    elif monday_for_invoice:
        note = "Opens invoice from the linked Projects item."
    else:
        note = (
            "No Projects link yet — opens invoice search with the job name. "
            "Link the Ops task to Projects so one-tap invoice works."
        )
    if proposed_total is not None and price_label:
        note = f"Proposed ${proposed_total:,.2f} ({price_label}). {note}"
    elif proposed_total is not None:
        note = f"Proposed ${proposed_total:,.2f} from staged worksheet. {note}"
    return {
        "kind": "ready_to_invoice",
        "name": _clean(row.get("name")) or "(unnamed)",
        "item_id": ops_item_id,
        "ops_item_id": ops_item_id,
        "project_item_id": project_item_id,
        "project_number": project_number,
        "project_name": _clean(row.get("project_name")),
        "builder": _clean(row.get("builder")),
        "supervisor": _clean(row.get("supervisor")),
        "location": _clean(row.get("location")),
        "status_labels": status_labels,
        "ready_date": _clean(row.get("ready_date")),
        "billable": _clean(row.get("billable")),
        "stage": _clean(row.get("stage")),
        "monday_url": _clean(row.get("url")),
        "proposed_total": proposed_total,
        "model": model,
        "price_label": price_label,
        "invoice_href": href,
        "estimate_href": None,
        "jobstart_href": None,
        "primary_href": href,
        "primary_label": "Open invoice",
        "note": note,
    }


def shape_accepted_bid(row: dict) -> dict:
    """
    Accepted bid → hub card.

    Needs-handoff bids primary-CTA to Job Start; handed-off bids with an
    estimate # go to the estimate form; otherwise Job Start still helps.
    """
    estimate_number = _clean(row.get("estimate_number"))
    project_number = _clean(row.get("project_number"))
    bid_id = row.get("item_id")
    handed_off = bool(row.get("handed_off"))
    needs_handoff = not handed_off

    est_href = estimate_href(estimate_number=estimate_number) if estimate_number else None
    js_href = jobstart_href(bid_id=bid_id)
    inv_href = invoice_href(project_number=project_number) if project_number else None

    if needs_handoff:
        primary_href = js_href
        primary_label = "Open Job Start"
        note = ("Accepted bid still needs a Sales→Ops handoff before "
                "there's a Projects item to invoice.")
    elif inv_href:
        primary_href = inv_href
        primary_label = "Open invoice"
        note = "Handed off — Project # available for invoicing."
    elif est_href:
        primary_href = est_href
        primary_label = "Open estimate"
        note = "Handed off — open the estimate to confirm scope before billing."
    else:
        primary_href = js_href
        primary_label = "Open Job Start"
        note = "Accepted bid — open Job Start to confirm the handoff state."

    status_labels = [s for s in (
        _clean(row.get("stage")),
        "Needs handoff" if needs_handoff else "Handed off",
        _clean(row.get("accepted_date")) and f"Accepted {row.get('accepted_date')}",
        _clean(row.get("estimate_total")) and f"Est {row.get('estimate_total')}",
        "Group drift" if row.get("group_drift") else None,
    ) if s]

    return {
        "kind": "accepted_bid",
        "name": _clean(row.get("name")) or "(unnamed)",
        "item_id": bid_id,
        "bid_item_id": bid_id,
        "project_number": project_number,
        "estimate_number": estimate_number,
        "estimate_total": _clean(row.get("estimate_total")),
        "builder": _clean(row.get("builder")),
        "supervisor": _clean(row.get("supervisor")),
        "location": _clean(row.get("location")),
        "status_labels": status_labels,
        "handed_off": handed_off,
        "needs_handoff": needs_handoff,
        "has_project": bool(row.get("has_project")),
        "has_ops": bool(row.get("has_ops")),
        "accepted_date": _clean(row.get("accepted_date")),
        "monday_url": _clean(row.get("url")),
        "invoice_href": inv_href,
        "estimate_href": est_href,
        "jobstart_href": js_href,
        "primary_href": primary_href,
        "primary_label": primary_label,
        "note": note,
    }


def shape_project_billing(row: dict) -> dict:
    """Projects board row with an invoice-oriented status → hub card."""
    project_number = _clean(row.get("project_number"))
    item_id = row.get("item_id")
    href = invoice_href(project_number=project_number, monday_item_id=item_id)
    status_labels = [s for s in (
        _clean(row.get("invoice_status")),
        _clean(row.get("deal_stage")),
        _clean(row.get("group_title")),
    ) if s]
    return {
        "kind": "project_billing",
        "name": _clean(row.get("name")) or "(unnamed)",
        "item_id": item_id,
        "project_item_id": item_id,
        "project_number": project_number,
        "builder": _clean(row.get("builder")),
        "supervisor": _clean(row.get("supervisor")),
        "location": _clean(row.get("location")),
        "invoice_status": _clean(row.get("invoice_status")),
        "status_labels": status_labels,
        "monday_url": _clean(row.get("url")),
        "invoice_href": href,
        "estimate_href": (
            estimate_href(estimate_number=project_number)
            if project_number else None
        ),
        "jobstart_href": None,
        "primary_href": href,
        "primary_label": "Open invoice",
        "note": (
            "Projects Invoice Status is set — confirm before drafting."
        ),
    }


def shape_search_project(row: dict) -> dict:
    """Normalize a search hit (rich or co.search_projects) for the hub UI."""
    project_number = _clean(
        row.get("project_number") or row.get("estimate_number"))
    item_id = row.get("item_id") or row.get("monday_item_id")
    name = _clean(row.get("name")) or "(unnamed)"
    href = invoice_href(project_number=project_number, monday_item_id=item_id)
    return {
        "kind": "search_project",
        "name": name,
        "item_id": item_id,
        "project_number": project_number,
        "builder": _clean(row.get("builder")),
        "supervisor": _clean(row.get("supervisor")),
        "location": _clean(row.get("location") or row.get("site_address")),
        "group": _clean(row.get("group") or row.get("group_title")),
        "status_labels": [s for s in (
            _clean(row.get("invoice_status")),
            _clean(row.get("deal_stage") or row.get("stage")),
            _clean(row.get("group") or row.get("group_title")),
        ) if s],
        "monday_url": _clean(row.get("url") or row.get("monday_url")),
        "invoice_href": href,
        "estimate_href": (
            estimate_href(q=project_number or name)
        ),
        "jobstart_href": None,
        "primary_href": href,
        "primary_label": "Open invoice",
    }


def shape_search_bid(row: dict) -> dict:
    """Normalize a bid search hit for the hub UI."""
    estimate_number = _clean(row.get("estimate_number"))
    item_id = row.get("item_id")
    name = _clean(row.get("name")) or "(unnamed)"
    stage = _clean(row.get("stage"))
    est_href = estimate_href(estimate_number=estimate_number, q=name)
    js_href = jobstart_href(bid_id=item_id)
    accepted = (stage or "").strip().lower() == "accepted"
    primary_href = js_href if accepted else est_href
    primary_label = "Open Job Start" if accepted else "Open estimate"
    return {
        "kind": "search_bid",
        "name": name,
        "item_id": item_id,
        "estimate_number": estimate_number,
        "stage": stage,
        "builder": _clean(row.get("builder") or row.get("customer")),
        "location": _clean(row.get("location")),
        "status_labels": [s for s in (stage, estimate_number and f"Est {estimate_number}") if s],
        "monday_url": _clean(row.get("url") or row.get("monday_url")),
        "invoice_href": None,
        "estimate_href": est_href,
        "jobstart_href": js_href,
        "primary_href": primary_href,
        "primary_label": primary_label,
    }


def encode_query(q: str) -> str:
    """URL-encode a search string (test helper / thin wrapper)."""
    return quote((q or "").strip())
