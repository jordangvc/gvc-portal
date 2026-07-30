"""
The handoff packet document — context building + PDF render.
=========================================================================
Jordan, 2026-07-29: "There should be no handwriting, any motherfucking thing
anywhere, because people's handwriting sucks. Everything should be done online
and be able to be sent out as a PDF or Google Drive link."

So the packet is a generated document, not a form someone fills with a pen.
Every value on it comes from Monday (the bid), Drive (the job folder), or what
Sales typed into the portal. It renders through the same WeasyPrint path as the
estimate / invoice / CO PDFs and files into the job's Drive folder.

Layering: PURE context building here (unit-testable with no I/O), the render
call wraps WeasyPrint, and Drive/Monday/Slack orchestration stays in
orchestrators/jobstart_flow.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from shared.paths import REPO_ROOT as ROOT, TEMPLATES_DIR

TEMPLATE_NAME = "job_handoff.html.j2"


def packet_filename(job_name: str, *, accepted: bool = False) -> str:
    """
    Stable, human-readable filename. One name per job so a re-render REPLACES
    the packet in Drive rather than littering the folder with versions — the
    accepted packet is the record, and there is only ever one current one.
    """
    safe = "".join(c for c in (job_name or "Job")
                   if c.isalnum() or c in " -_.,&").strip()
    safe = " ".join(safe.split())[:90] or "Job"
    suffix = "Handoff Packet" if not accepted else "Handoff Packet - Accepted"
    return f"{safe} - {suffix}.pdf"


def _pretty_dt(iso: Optional[str]) -> Optional[str]:
    """
    ISO timestamp → 'Jul 29, 2026 2:14 PM'. Returns None on anything
    unparseable rather than showing a raw timestamp on a shared document.

    Built without %-d / %-I: those strip-leading-zero codes are glibc-only and
    raise ValueError on Windows, where this code also gets run and tested.
    """
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    hour = dt.hour % 12 or 12
    return (f"{dt.strftime('%b')} {dt.day}, {dt.year} "
            f"{hour}:{dt.minute:02d} {dt.strftime('%p')}")


def _person(email: Optional[str]) -> Optional[str]:
    """jake@greenvalleycontractors.com → 'Jake'. The packet is an internal
    document; full addresses just make it noisier."""
    if not email:
        return None
    name = str(email).split("@")[0].replace(".", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in name.split()) or None


def build_context(*, job_name: str, values: dict, bid_context: dict,
                  record: dict, bid_url: Optional[str] = None,
                  drive_folder_path: Optional[str] = None) -> dict:
    """
    PURE. Everything the template needs, from the packet values + bid context +
    the stored handoff record. Missing values stay missing — the template renders
    them as an explicit dash rather than a blank the reader has to interpret.
    """
    v = values or {}
    ctx = bid_context or {}
    rec = record or {}

    def val(key: str) -> Optional[str]:
        raw = v.get(key)
        text = str(raw).strip() if raw is not None else ""
        return text or None

    return {
        "job_name": job_name or "Untitled job",
        "customer": ctx.get("customer"),
        "estimate_number": ctx.get("estimate_number"),
        "estimate_total": ctx.get("estimate_total"),
        "location": ctx.get("location"),
        "bid_url": bid_url,
        "drive_folder_path": drive_folder_path,

        # Packet fields (keys match shared/boards.JOBSTART_FIELDS)
        "scope": val("scope"),
        "exclusions": val("exclusions"),
        "takeoff_link": val("takeoff_link"),
        "board_count": val("board_count"),
        "ceiling_finish": val("ceiling_finish"),
        "garage_finish": val("garage_finish"),
        "window_returns": val("window_returns"),
        "window_type": val("window_type"),
        "supervisor": val("supervisor"),
        "builder": val("builder"),
        "project_type": val("project_type"),
        "start_date": val("start_date"),
        "expected_finish": val("expected_finish"),
        "lock_box": val("lock_box"),
        "scaffold": val("scaffold"),
        "heater_cans": val("heater_cans"),
        "shower": val("shower"),
        "lot": val("lot"),
        "gc_confirmed_on": val("gc_confirmed_on"),
        "open_questions": val("open_questions"),
        "allowances": val("allowances"),

        # Acceptance state — what makes this a handoff and not a form
        "sent_by": _person(rec.get("sent_by")),
        "sent_at": _pretty_dt(rec.get("sent_at")),
        "accepted_by": _person(rec.get("accepted_by")),
        "accepted_at": _pretty_dt(rec.get("accepted_at")),
        "sent_back_note": rec.get("sent_back_note") or None,

        "generated_at": datetime.now(timezone.utc).strftime("%b %d, %Y"),
    }


def render_packet_pdf(context: dict, output_path: Path) -> Path:
    """Render the packet to PDF. Same WeasyPrint path the estimate/CO/invoice
    PDFs use, so it picks up the same fonts and base_url handling."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    from weasyprint import HTML

    html_str = env.get_template(TEMPLATE_NAME).render(**context)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(ROOT)).write_pdf(str(output_path))
    return output_path
