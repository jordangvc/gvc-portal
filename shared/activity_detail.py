"""
Business detail for activity events.
=========================================================================
The activity log used to record only WHO clicked WHAT mode ("invoice.run …
target=live"), which made /ui/activity unanswerable for the questions the
office actually asks: which customer, which document, how much, where did
it go, and did every downstream step land?

`summarize()` turns a flow's input payload + its writeback into the flat
scalar fields log_event() attaches to the event. Pure and total: it never
raises and never reaches the network — a reporting nicety must not be able
to break an invoice run (same contract as activity.log_event itself).

Field names are deliberately short and stable: they become CSV column
headers in the activity export and `key=value` chips in the UI.

Sources, in order of trust:
  1. the WRITEBACK — what actually happened (identifier as issued, totals as
     rendered on the PDF, per-step outcomes). Preferred for money.
  2. the INPUT payload — still available when a run throws before producing
     a writeback, so a failed attempt is logged with the customer + document
     it was attempting rather than a bare "error".
"""
from __future__ import annotations

from typing import Any, Optional

# Kinds the portal logs. Each maps to the nested key holding its document
# fields in the input payload ("invoice" / "estimate" / "change_order" …).
_DOC_KEYS = {
    "invoice": "invoice",
    "estimate": "estimate",
    "change_order": "change_order",
    "coi": "coi",
}


def _first(*values: Any) -> Optional[str]:
    """First non-empty value as a trimmed string, else None."""
    for v in values:
        if v is None:
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
        s = str(v).strip()
        if s:
            return s
    return None


def _nested(source: Any, *path: str) -> Any:
    """Walk dicts safely: _nested(d, "client", "name") -> value or None."""
    cur = source
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _step(value: Any, *, ok_label: str) -> Optional[str]:
    """
    Normalize a per-step status into one of ok_label / "FAILED — …" / None.

    The flows report steps inconsistently (a URL means success for Gmail; a
    status string that may start with "FAILED" for Drive; a dict for the
    Monday ledger). Anything falsy is reported as None = "step didn't run",
    which is different from "ran and failed" — that distinction is the whole
    point of putting these in the log.
    """
    if value in (None, "", False):
        return None
    text = str(value).strip()
    if text.upper().startswith("FAILED") or "FAILED" in text.upper()[:20]:
        return text[:180]
    return ok_label


def _outcome_fields(writeback: dict) -> dict:
    """Per-step results, shared shape across invoice / estimate / CO / COI."""
    out: dict = {}

    gmail = _step(writeback.get("gmail_draft_url"), ok_label="draft created")
    if gmail is None:
        # gmail_status carries the failure/skip reason when no URL was produced.
        gmail = _step(writeback.get("gmail_status"), ok_label="draft created")
    if gmail:
        out["gmail"] = gmail

    drive = _step(
        writeback.get("drive_status") or writeback.get("drive_pdf_url")
        or writeback.get("drive_web_view_link") or writeback.get("drive_file_id"),
        ok_label="saved",
    )
    if drive:
        out["drive"] = drive

    ledger = writeback.get("ledger")
    if isinstance(ledger, dict):
        out["monday"] = ("row synced" if ledger.get("ledger_synced")
                         else _first(ledger.get("ledger_status"), "not synced"))
    else:
        monday = _step(writeback.get("monday_status"), ok_label="updated")
        if monday:
            out["monday"] = monday

    stripe_id = _first(writeback.get("stripe_invoice_id"))
    if stripe_id:
        out["stripe_invoice_id"] = stripe_id

    if writeback.get("already_existed"):
        out["reused"] = "existing invoice reused (no duplicate created)"
    if writeback.get("revised") or writeback.get("revision_version"):
        out["revision"] = _first(writeback.get("revision_version"), "revised")

    return out


def summarize(
    kind: str,
    data: Optional[dict] = None,
    writeback: Optional[dict] = None,
    *,
    mode: Optional[str] = None,
    error: Optional[BaseException] = None,
    **extra: Any,
) -> dict:
    """
    Build the kwargs for activity.log_event: `target` (the document number, per
    log_event's own contract that target is "what it acted on") plus flat
    business + outcome fields.

    Returns at minimum {"target": …} — callers can splat it directly:
        activity.log_event("invoice.run", actor=email,
                           **summarize("invoice", req.data, wb, mode=mode))
    """
    try:
        data = data if isinstance(data, dict) else {}
        wb = writeback if isinstance(writeback, dict) else {}
        doc_key = _DOC_KEYS.get(kind, kind)

        fields: dict = {}

        # --- identity -------------------------------------------------------
        # Writeback first: on an estimate the service assigns the number at
        # finalize, so the input's may be blank.
        fields["target"] = _first(
            wb.get("identifier"),
            _nested(wb, doc_key, "identifier"),
            _nested(data, doc_key, "identifier"),
            wb.get("co_number"), _nested(data, doc_key, "co_number"),
            kind,
        )

        customer = _first(
            _nested(wb, "client", "name"), _nested(data, "client", "name"),
            wb.get("customer"), data.get("customer"),
            _nested(data, "holder", "name"),          # COI: certificate holder
        )
        if customer:
            fields["customer"] = customer

        job = _first(
            # wb first (both shapes): the enriched job label — e.g.
            # "6845 Hager Rd | Ken Roell" — is what Drive/Monday actually filed under.
            _nested(wb, "job", "name"), wb.get("job_name"),
            _nested(data, "job", "name"), _nested(data, "project", "name"),
        )
        if job:
            fields["job"] = job

        amount = _first(
            wb.get("amount_pretty"),
            _nested(wb, doc_key, "total_pretty"),
            wb.get("main_total_pretty"), wb.get("total_with_options_pretty"),
            _nested(wb, doc_key, "total"),
        )
        if amount:
            fields["amount"] = amount

        recipient = _first(
            wb.get("recipient"),
            _nested(wb, "client", "email"), _nested(data, "client", "email"),
            _nested(data, "contact", "email"),        # COI: contact block
        )
        if recipient:
            fields["sent_to"] = recipient

        cc = _first(wb.get("cc"), data.get("cc_email"),
                    _nested(data, "_monday", "cc_email"))
        if cc:
            fields["cc"] = cc

        due = _first(wb.get("due_date_pretty"), _nested(wb, doc_key, "due_date_pretty"))
        if due:
            fields["due"] = due

        if mode:
            fields["mode"] = mode

        # --- what actually happened ----------------------------------------
        fields.update(_outcome_fields(wb))

        if error is not None:
            fields["error"] = f"{type(error).__name__}: {error}"[:300]

        for key, value in extra.items():
            if value not in (None, ""):
                fields[key] = value

        return fields
    except Exception:  # noqa: BLE001 — reporting must never break a run
        return {"target": kind}


def result_for(writeback: Optional[dict]) -> str:
    """
    "ok" when every step that ran succeeded, "partial" when the run returned
    200 but a downstream step failed (the silent-failure class that hid the
    Slack outage for days — worth being able to filter for).
    """
    try:
        outcome = _outcome_fields(writeback if isinstance(writeback, dict) else {})
        for key in ("gmail", "drive", "monday"):
            value = str(outcome.get(key) or "")
            if value.upper().startswith("FAILED") or "not synced" in value:
                return "partial"
        return "ok"
    except Exception:  # noqa: BLE001
        return "ok"
