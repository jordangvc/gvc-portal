"""
Tests for shared/activity_detail.py + the CSV report columns.

Self-running (no pytest on the Windows box — same pattern as
test_lien_watch.py / test_jobcheck.py):  python tests/test_activity_detail.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.activity_detail import result_for, summarize  # noqa: E402
from shared.activity_read import REPORT_FIELDS, to_csv  # noqa: E402

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}\n    got:  {got!r}\n    want: {want!r}")


def check_true(name, cond):
    if not cond:
        FAILURES.append(name)


# --- fixtures shaped like the REAL writebacks -------------------------------

INVOICE_DATA = {
    "client": {"name": "Ken Roell Custom Homes", "email": "roell21@yahoo.com"},
    "job": {"name": "6845 Hager Rd"},
    "invoice": {"identifier": "2026-0723-01"},
    "cc_email": "office@example.com",
}
INVOICE_WB = {
    "identifier": "2026-0723-01", "customer": "Ken Roell Custom Homes",
    "job_name": "6845 Hager Rd | Ken Roell", "recipient": "roell21@yahoo.com",
    "cc": "office@example.com", "amount_pretty": "$27,500.00",
    "due_date_pretty": "Aug 22, 2026", "gmail_draft_url": "https://mail.google.com/x",
    "drive_status": "saved", "ledger": {"ledger_synced": True},
    "stripe_invoice_id": "in_1TwN0N",
}
# The estimate writeback IS the enriched dict (nested client/job).
ESTIMATE_WB = {
    "identifier": "2026-0727-003",
    "client": {"name": "CORE Resources, Inc.", "email": "pm@core.example"},
    "job": {"name": "1346 Jamike Ave — Obara Office Renovation"},
    "main_total_pretty": "$155,150.00", "gmail_draft_url": "https://mail.google.com/y",
    "drive_status": "saved", "revision_version": 2,
}


def test_invoice_success():
    f = summarize("invoice", INVOICE_DATA, INVOICE_WB, mode="live")
    check("target = document number", f["target"], "2026-0723-01")
    check("customer", f["customer"], "Ken Roell Custom Homes")
    # The enriched job label (what Drive/Monday filed under) wins over raw input.
    check("job prefers writeback label", f["job"], "6845 Hager Rd | Ken Roell")
    check("amount", f["amount"], "$27,500.00")
    check("sent_to", f["sent_to"], "roell21@yahoo.com")
    check("cc", f["cc"], "office@example.com")
    check("gmail step", f["gmail"], "draft created")
    check("drive step", f["drive"], "saved")
    check("monday step", f["monday"], "row synced")
    check("stripe id", f["stripe_invoice_id"], "in_1TwN0N")
    check("result ok", result_for(INVOICE_WB), "ok")


def test_partial_when_a_step_failed():
    wb = dict(INVOICE_WB)
    wb.pop("gmail_draft_url")
    wb["gmail_status"] = "FAILED — no Gmail draft was created."
    f = summarize("invoice", INVOICE_DATA, wb, mode="live")
    check_true("failed gmail surfaced", f["gmail"].startswith("FAILED"))
    check("result partial", result_for(wb), "partial")

    wb2 = dict(INVOICE_WB)
    wb2["ledger"] = {"ledger_synced": False, "ledger_status": "not synced — API error"}
    check("unsynced ledger -> partial", result_for(wb2), "partial")


def test_error_before_writeback_still_identifies_the_job():
    f = summarize("invoice", INVOICE_DATA, None, mode="live",
                  error=ValueError("bad recipient email"))
    check("target from input", f["target"], "2026-0723-01")
    check("customer from input", f["customer"], "Ken Roell Custom Homes")
    check("error recorded", f["error"], "ValueError: bad recipient email")
    check_true("no step chips invented", "gmail" not in f and "drive" not in f)


def test_estimate_nested_shape():
    f = summarize("estimate", {}, ESTIMATE_WB, mode="finalize")
    check("estimate target", f["target"], "2026-0727-003")
    check("estimate customer", f["customer"], "CORE Resources, Inc.")
    check("estimate amount", f["amount"], "$155,150.00")
    check("estimate recipient", f["sent_to"], "pm@core.example")
    check("revision noted", f["revision"], "2")


def test_missing_step_is_not_a_failed_step():
    """A step that never ran must NOT render as success or failure."""
    f = summarize("invoice", INVOICE_DATA, {"identifier": "X"}, mode="dry-run")
    check_true("no gmail key", "gmail" not in f)
    check_true("no drive key", "drive" not in f)
    check("dry-run mode kept", f["mode"], "dry-run")


def test_never_raises():
    for bad in (None, "string", 42, [], {"client": "not-a-dict"}):
        got = summarize("invoice", bad, bad)
        check_true(f"garbage {bad!r} -> dict with target", isinstance(got, dict) and "target" in got)
    check("result_for(garbage)", result_for("nope"), "ok")


def test_csv_promotes_report_columns():
    events = [{
        "ts": "2026-07-27T18:02:00Z", "action": "invoice.run", "actor": "andrea@gvc.com",
        "target": "2026-0723-01", "result": "ok", "severity": "INFO",
        "extra": {"customer": "Ken Roell Custom Homes", "amount": "$27,500.00",
                  "sent_to": "roell21@yahoo.com", "gmail": "draft created"},
    }]
    csv_text = to_csv(events)
    header, row = csv_text.splitlines()[0], csv_text.splitlines()[1]
    for field in REPORT_FIELDS:
        check_true(f"header has {field}", field in header)
    check_true("customer value in row", "Ken Roell Custom Homes" in row)
    check_true("amount value in row", "$27,500.00" in row)
    check_true("raw extra still present", "draft created" in row)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s):")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print(f"OK — {len(tests)} tests, all checks passed.")
