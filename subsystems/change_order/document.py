"""
GVC Change Order Template renderer.
=========================================================================
Produces a GVC-branded PDF that documents a single Change Order:
  - Page 1: typed cover with disclaimer, CO tracking #, project info,
    breakdown lines (labor / materials), totals, and an approval note.
  - Pages 2+: embedded crew approval scans / handwritten field tickets
    (PNG, JPEG, etc.) shown one per page.

The CO Template is NOT an invoice. It is informational documentation for
T&M / change-order work that is billed via a single line on the GVC
invoice. The disclaimer banner on page 1 makes this explicit so a customer
or downstream GC doesn't double-pay or mis-file.

Called from invoice.py when the input JSON declares `invoice.change_orders`.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from shared.paths import REPO_ROOT as ROOT, TEMPLATES_DIR, ASSETS_DIR


def _logo_data_uri() -> str:
    p = ASSETS_DIR / "logo.png"
    if not p.exists():
        return ""
    return f"data:image/png;base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def _image_data_uri(image_path: Path) -> tuple[str, str]:
    """Return (data_uri, display_name) for an image, picking MIME from extension."""
    ext = image_path.suffix.lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        mime = "image/jpeg"
    elif ext == "png":
        mime = "image/png"
    elif ext == "gif":
        mime = "image/gif"
    else:
        # WeasyPrint can usually handle whatever, but be explicit.
        mime = f"image/{ext or 'png'}"
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}", image_path.name


def enrich_change_order(co: dict) -> dict:
    """
    Normalise a change-order dict for templating. Adds *_pretty fields and
    embeds appendix images as data URIs so WeasyPrint doesn't need to fetch
    them from disk at render time.

    Expected input shape:
        {
            "co_number": "GVC-2026-C-005-CO-01",
            "title": "T&M — Drywall touch-up and base molding",
            "description": "Long-form description of the work.",
            "breakdown": [
                {"label": "Labor (73.5 hrs @ $70/hr)", "amount": 5145.00},
                {"label": "Materials",                   "amount": 81.90},
            ],
            "total": 5226.90,
            "approval_note": "Rate approved by Matthew Beaver…",
            "appendix_images": ["inputs/.../TM_Approval.png", ...],
            "linked_invoice_identifier": "GVC-2026-C-005",  # set by caller
            "issue_date_pretty": "May 19, 2026",            # set by caller
        }
    """
    from shared.money import fmt_money

    out = dict(co)
    total = 0.0
    for row in out.get("breakdown", []):
        amt = float(row["amount"])
        row["amount"] = round(amt, 2)
        row["amount_pretty"] = fmt_money(amt)
        total += amt
    # Trust the caller's stated total if it's there; otherwise use the sum.
    if out.get("total") is None:
        out["total"] = round(total, 2)
    else:
        out["total"] = round(float(out["total"]), 2)
    out["total_pretty"] = fmt_money(out["total"])  # noqa: F821 — fmt_money imported above

    # Appendix images may be either local paths (relative to ROOT, or
    # absolute) or `drive:FILE_ID` references. The cloud commercial flow
    # uses the latter — Claude reads the source folder via the Drive MCP
    # and passes file IDs in the JSON; the service downloads them to /tmp.
    from adapters.drive import resolve_local_or_drive_path

    tmp_dir = Path(os.environ.get("GVC_OUTPUT_DIR") or "/tmp") / "co_appendix"
    appendix: list[dict] = []
    for img_raw in out.get("appendix_images", []) or []:
        try:
            p = resolve_local_or_drive_path(
                str(img_raw), tmp_dir=tmp_dir, project_root=ROOT,
            )
            if not p.exists():
                raise FileNotFoundError(f"CO appendix image not found: {p}")
            uri, name = _image_data_uri(p)
            appendix.append({"data_uri": uri, "filename": name})
        except Exception as e:
            # Missing-or-unreadable appendix image is non-fatal: keep
            # building the CO PDF without this image rather than failing
            # the whole invoice. The other CO content (breakdown, totals,
            # approval text) is still useful. The admin sees the warning on
            # stderr; Andrea sees the CO PDF without this page and can
            # attach the original to the Gmail draft if she needs to.
            print(
                f"[change_order] skipping appendix image {img_raw!r}: "
                f"{type(e).__name__}: {e}", file=sys.stderr,
            )
    out["appendix"] = appendix
    return out


def render_co_pdf(
    co: dict,
    *,
    job: dict,
    client: dict,
    output_path: Path,
    standalone: bool = False,
) -> Path:
    """
    Render a single Change Order PDF.

    Two modes share one template:
      - standalone=False (default): the CO is supporting documentation for a
        change-order LINE on a parent invoice. The amber "not an invoice"
        disclaimer references co["linked_invoice_identifier"]. This is the
        path invoice.py uses — unchanged.
      - standalone=True: the CO is its own approvable document in the Change
        Order program (billed later as its own invoice CO.{n}-{estimate#}).
        The disclaimer is replaced with an approval-by-reply note and the meta
        block shows the project / estimate number instead of a linked invoice.

    Caller must set: co["co_number"], co["issue_date_pretty"]. For the
    non-standalone path also set co["linked_invoice_identifier"]; for the
    standalone path set co["base_number"] (the linked estimate / project #).
    """
    enriched = enrich_change_order(co)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("change_order.html.j2")
    html_str = tmpl.render(
        co=enriched,
        job=job,
        client=client,
        standalone=standalone,
        logo_data_uri=_logo_data_uri(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(ROOT)).write_pdf(str(output_path))
    return output_path
