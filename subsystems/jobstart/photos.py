"""
Job Start packet photos — PURE image selection + processing.
=========================================================================
The field crew asked for site photos on the handoff packet (Jordan, 2026-08-03).
Photos live in the job's Google Drive folder: they are uploaded to Monday, a
nightly job downloads them, fixes the naming, and files them to Drive (and,
soon, the takeoff app backs its own photos up to the same place). So the packet
PULLS them from Drive rather than anyone re-uploading — same principle as the
scope review and takeoff.

This module is the PURE half: it decides which files are photos, in what order,
and how each one becomes an embeddable image. It does NO Drive I/O — the caller
(adapters/drive.py) lists and downloads; this turns raw bytes into a
`data:` URI that WeasyPrint can render straight into the PDF (the production
pages run under a content policy that blocks remote images, and a Drive file
isn't publicly fetchable anyway, so embedding is the only path that works).

In-house packets carry every photo on the job. Subcontractor packets will carry
only the photos that sub needs — a later, slightly different handoff (Jordan);
the seam for it is `select_photos(..., only=...)`, unused for now.
"""
from __future__ import annotations

import base64
import io
import os
import sys
from typing import Any, Optional

# Max images on one packet. A job with 60 photos should not make a 60-page PDF;
# the cap keeps the in-house packet skimmable and the file small. Env-overridable.
DEFAULT_MAX_PHOTOS = int(os.environ.get("GVC_JOBSTART_PHOTO_MAX") or 24)
# Longest edge each photo is scaled down to before embedding. 1400px prints
# crisply at packet size while keeping the base64 payload small.
DEFAULT_MAX_PX = int(os.environ.get("GVC_JOBSTART_PHOTO_MAX_PX") or 1400)
DEFAULT_JPEG_QUALITY = int(os.environ.get("GVC_JOBSTART_PHOTO_QUALITY") or 72)

# Extensions we treat as photos even when a mimeType is missing/generic.
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif",
               ".tiff", ".heic", ".heif")


def is_image(meta: dict) -> bool:
    """True if a Drive file entry looks like a photo (by mime, then extension)."""
    mime = str(meta.get("mimeType") or "").lower()
    if mime.startswith("image/"):
        return True
    name = str(meta.get("name") or "").lower()
    return name.endswith(_IMAGE_EXTS)


def caption_from_name(name: Optional[str]) -> str:
    """
    'IMG_4417 - Lock box front door.HEIC' → 'Lock box front door'.
    The nightly job fixes the naming, so the filename is the caption source;
    we just drop the extension and any leading camera token, and tidy separators.
    """
    base = str(name or "").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    # Drop a leading camera-style token like 'IMG_4417 - ' / 'DSC0001_'.
    parts = base.replace("_", " ").split(" - ", 1)
    if len(parts) == 2 and _looks_like_camera_token(parts[0]):
        base = parts[1]
    else:
        base = base.replace("_", " ")
    return " ".join(base.split()).strip()


def _looks_like_camera_token(token: str) -> bool:
    t = token.strip().upper()
    return (t.startswith(("IMG", "DSC", "DCIM", "PXL", "PHOTO"))
            or (t.replace(" ", "").isdigit() and len(t.replace(" ", "")) >= 4))


def select_photos(files: list, *, limit: Optional[int] = None,
                  only: Optional[list] = None) -> list:
    """
    PURE. Filter Drive file entries to photos, order them by (fixed) filename so
    the crew sees them in a sensible sequence, and cap the count.

    `only` (a list of file ids) is the seam for the subcontractor variant — when
    given, keep only those ids. Unused by the in-house packet, which takes all.
    """
    if limit is None:
        limit = DEFAULT_MAX_PHOTOS
    imgs = [f for f in (files or []) if is_image(f)]
    if only is not None:
        keep = {str(i) for i in only}
        imgs = [f for f in imgs if str(f.get("id")) in keep]
    imgs.sort(key=lambda f: str(f.get("name") or "").lower())
    return imgs[: max(0, limit)]


def to_data_uri(raw: bytes, *, max_px: int = DEFAULT_MAX_PX,
                quality: int = DEFAULT_JPEG_QUALITY) -> Optional[str]:
    """
    Raw image bytes → a downscaled JPEG `data:` URI, or None if the bytes can't
    be decoded (an unreadable photo is skipped, never fatal to the packet).

    EXIF orientation is applied first — phone photos carry rotation in metadata,
    and WeasyPrint would otherwise print them sideways. HEIC/HEIF (the iPhone
    default) decodes only if pillow-heif is installed; without it those files are
    skipped and the caller logs it.
    """
    if not raw:
        return None
    try:
        from PIL import Image, ImageOps
        _maybe_register_heif()

        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)          # honor phone rotation
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((max_px, max_px))            # downscale, keep aspect
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:  # noqa: BLE001 — a bad photo is skipped, not fatal
        print(f"[jobstart.photos] could not process image "
              f"({type(e).__name__}: {e})", file=sys.stderr)
        return None


_HEIF_REGISTERED = False


def _maybe_register_heif() -> None:
    """Register pillow-heif with Pillow if it's installed, so HEIC/HEIF decode.
    Best-effort and cached — absence just means HEICs are skipped."""
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return
    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 — no HEIC support available; that's fine
        pass
    _HEIF_REGISTERED = True


def build_entries(items: list, *, max_px: int = DEFAULT_MAX_PX,
                  quality: int = DEFAULT_JPEG_QUALITY) -> list[dict]:
    """
    PURE. [{name, bytes}] → [{caption, data_uri}] for the template, dropping any
    photo whose bytes won't decode. Order is preserved (caller has already
    selected + sorted).
    """
    out: list[dict] = []
    for item in items or []:
        data_uri = to_data_uri(item.get("bytes") or b"",
                               max_px=max_px, quality=quality)
        if not data_uri:
            continue
        out.append({"caption": caption_from_name(item.get("name")),
                    "data_uri": data_uri})
    return out
