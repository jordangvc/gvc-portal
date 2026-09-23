"""The money forms' stage-bar Accept waits on a success-only event.

Every form must also listen for a failure/ended event so an error, a 401, or a
cancelled confirm() resets the bar instead of leaving it on "Accepting…" for the
180s timeout (estimate hang reported 2026-09-23).
"""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"

FORMS = {
    "estimate.html": "gvc:estimate-failed",
    "invoice.html": "gvc:invoice-accept-ended",
    "change-order.html": "gvc:co-accept-ended",
}


def test_every_accept_wait_has_a_failure_exit():
    for page, event in FORMS.items():
        html = (WEB / page).read_text(encoding="utf-8")
        assert f'addEventListener("{event}"' in html, f"{page}: onAccept never listens for {event}"
        assert f'new CustomEvent("{event}"' in html, f"{page}: nothing ever dispatches {event}"


def test_hidden_preview_is_linked_not_promised_below():
    # Estimate + invoice hide the doc column, so "Preview ready below" was a lie.
    for page in ("estimate.html", "invoice.html"):
        html = (WEB / page).read_text(encoding="utf-8")
        assert "Preview ready below" not in html, page
        assert "Open the preview PDF" in html, page
