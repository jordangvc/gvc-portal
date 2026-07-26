"""Invoice + change-order PDF rendering (extracted from invoice.py)."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import qrcode
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from shared import paths
from subsystems.change_order.document import render_co_pdf

ROOT = paths.REPO_ROOT
TEMPLATES_DIR = paths.TEMPLATES_DIR
ASSETS_DIR = paths.ASSETS_DIR

def make_qr_data_uri(url: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#111111", back_color="#FFFFFF")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def make_logo_data_uri() -> str:
    logo_path = ASSETS_DIR / "logo.png"
    if not logo_path.exists():
        return ""
    b64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def render_co_pdfs(enriched: dict, output_dir: Path) -> list[Path]:
    """
    Render one Change Order PDF per entry in invoice.change_orders[]. Returns
    the list of generated paths. Empty list if no change_orders.

    Each generated PDF is a standalone informational document referencing the
    parent invoice's identifier in its disclaimer. The CO PDFs end up in
    `extra_pdfs` for the Gmail draft alongside G702/G703.
    """
    inv = enriched["invoice"]
    cos = inv.get("change_orders") or []
    if not cos:
        return []
    identifier = inv["identifier"]
    issue_pretty = inv["issue_date_pretty"]
    paths: list[Path] = []
    for co in cos:
        co_payload = dict(co)
        co_payload["linked_invoice_identifier"] = identifier
        co_payload["issue_date_pretty"] = issue_pretty
        co_number = co.get("co_number") or f"{identifier}-CO-{len(paths) + 1:02d}"
        co_payload["co_number"] = co_number
        out = output_dir / f"{co_number}.pdf"
        render_co_pdf(
            co_payload,
            job=enriched["job"],
            client=enriched["client"],
            output_path=out,
        )
        paths.append(out)
    return paths


def render_pdf(
    enriched: dict,
    hosted_payment_url: str,
    output_path: Path,
) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("invoice.html.j2")
    html_str = tmpl.render(
        client=enriched["client"],
        job=enriched["job"],
        invoice=enriched["invoice"],
        is_past_due=enriched["is_past_due"],
        hosted_payment_url=hosted_payment_url,
        qr_data_uri=make_qr_data_uri(hosted_payment_url),
        logo_data_uri=make_logo_data_uri(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_str, base_url=str(ROOT)).write_pdf(str(output_path))
    return output_path
