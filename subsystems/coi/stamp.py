"""
COI stamping — overlay the certificate-holder block onto the blank ACORD 25.
=========================================================================
The company's COI is issued annually by the insurance agent as a single-page
ACORD 25 PDF with an EMPTY "CERTIFICATE HOLDER" box (bottom-left). Issuing a
COI to a builder/GC = writing their name + address into that box and sending
it. This module does exactly that, and nothing else: the policy data on the
form is the agent's and is never touched.

Geometry is calibrated to the real, agent-blessed example
("COI - CM2 - 312 Walnut - expMay_2027.pdf", 2026-07-14): that document is the
blank plus ONE FreeText annotation at page x≈41.9, first baseline y≈109.1,
10pt leading, ~7.6pt Helvetica. We stamp the same spot but draw directly into
the page content (no annotation) so every viewer/printer renders it the same.

Pure helpers (holder_lines / wrap_line / coi_filename / coi_identifier /
pretty_expiry) are unit-tested without any PDF dependency. The single
PDF-touching function is stamp_certificate_holder(); it imports pypdf/reportlab
lazily so the module loads even where those aren't installed.
"""
from __future__ import annotations

import re
from typing import Optional

# --- Certificate-holder box geometry (PDF points, origin bottom-left; page is
# 612x792). Derived from the CM2 example's appearance stream — see module doc.
# The box itself spans roughly x 24..306, y 63..135; four lines at 10pt leading
# from y=109 end at y=79, comfortably inside.
TEXT_X = 42.0
FIRST_BASELINE_Y = 109.0
LEADING = 10.0
FONT_NAME = "Helvetica"
FONT_SIZE = 8.0          # example renders ~7.57pt; 8pt is visually identical
MAX_LINES = 5            # name + up to 4 address lines — hard cap, box-safe
WRAP_AT = 55             # ~box width at 8pt Helvetica; soft-wrap on spaces


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def wrap_line(line: str, width: int = WRAP_AT) -> list[str]:
    """Soft-wrap one logical line on spaces so nothing paints past the box
    edge. A single unbreakable token longer than `width` is left whole
    (clipping beats corrupting an address)."""
    line = " ".join((line or "").split())
    if len(line) <= width:
        return [line] if line else []
    out: list[str] = []
    cur = ""
    for word in line.split(" "):
        candidate = f"{cur} {word}".strip()
        if len(candidate) <= width or not cur:
            cur = candidate
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out


def holder_lines(name: str, address: str) -> list[str]:
    """
    Build the certificate-holder block: the holder/project NAME on line 1,
    then the address exactly as entered (one output line per input line),
    soft-wrapped and capped at MAX_LINES. Mirrors the CM2 example:

        CMsquared LLC
        5777 Kellogg Ave
        Cincinnati, OH 45230
    """
    lines: list[str] = []
    lines += wrap_line(name)
    for raw in (address or "").splitlines():
        lines += wrap_line(raw)
    return lines[:MAX_LINES]


_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\r\n]+')


def _safe_name(name: str) -> str:
    return _UNSAFE_FILENAME.sub("-", " ".join((name or "").split())).strip(" -.")


def coi_filename(name: str, expiry_label: Optional[str] = None) -> str:
    """
    Output filename, following the established convention
    ("COI - CM2 - 312 Walnut - expMay_2027.pdf"):

        COI - {Name/Project Name} - {expiry_label}.pdf

    expiry_label (e.g. "expMay_2027") comes from the stored template's
    metadata; omitted cleanly when the template has none.
    """
    base = f"COI - {_safe_name(name) or 'holder'}"
    if expiry_label:
        base += f" - {_safe_name(expiry_label)}"
    return base + ".pdf"


def coi_identifier(name: str) -> str:
    """Slug used for preview blob paths, Gmail draft dedup, and logs."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return f"COI-{slug or 'holder'}"


def pretty_expiry(expiry_label: Optional[str]) -> Optional[str]:
    """'expMay_2027' -> 'May 2027' for human-facing wording. None-safe."""
    if not expiry_label:
        return None
    s = expiry_label.strip()
    if s.lower().startswith("exp"):
        s = s[3:]
    return s.replace("_", " ").strip() or None


# ---------------------------------------------------------------------------
# The one PDF-touching function
# ---------------------------------------------------------------------------

def stamp_certificate_holder(template_bytes: bytes, lines: list[str]) -> bytes:
    """
    Return a new PDF: the template with `lines` drawn into the certificate-
    holder box of PAGE 1. The template's own content (policy numbers, limits,
    checkbox appearances, signature) is preserved untouched; templates
    encrypted with an empty owner password (the agent's PDFs are) are
    transparently decrypted.
    """
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    if not lines:
        raise ValueError("Nothing to stamp: the certificate holder block is empty.")

    reader = PdfReader(BytesIO(template_bytes))
    if reader.is_encrypted:
        reader.decrypt("")
    page = reader.pages[0]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont(FONT_NAME, FONT_SIZE)
    c.setFillColorRGB(0, 0, 0)
    y = FIRST_BASELINE_Y
    for line in lines[:MAX_LINES]:
        c.drawString(TEXT_X, y, line)
        y -= LEADING
    c.save()
    overlay = PdfReader(BytesIO(buf.getvalue())).pages[0]

    writer = PdfWriter()
    writer.append(reader)          # clones pages + annots + AcroForm intact
    writer.pages[0].merge_page(overlay)  # content-stream append → drawn on top
    out = BytesIO()
    writer.write(out)
    return out.getvalue()
