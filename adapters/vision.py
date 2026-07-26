"""
Google Cloud Vision OCR for check images.
=========================================================================
Thin wrapper around Vision `DOCUMENT_TEXT_DETECTION`, which handles the dense,
mixed-font layout of a check (printed fields + MICR line) better than plain
text detection. Reuses the same service-account JSON as Drive/GCS
(`.google-service-account.json` locally, `/secrets/...` on Cloud Run).

Loads lazily so the module imports cleanly where the dep/creds aren't present
(e.g. unit tests of the pure parser). Enable the API once per project:
    gcloud services enable vision.googleapis.com --project gvc-invoice-system

CLI harness — dump the raw OCR text for a local check image so the parser can
be tuned against real output:
    python vision.py /path/to/check.jpg
"""
from __future__ import annotations

from shared import paths
import os
from pathlib import Path
from typing import Optional, Union


class VisionNotConfigured(Exception):
    """Raised when the Vision dep or service-account JSON is unavailable."""


def _explicit_creds_path() -> Optional[Path]:
    """Explicit service-account JSON, if configured and present. Else None."""
    raw = os.environ.get("GVC_DRIVE_CREDENTIALS")
    p = Path(raw) if raw else paths.DEFAULT_SA_PATH
    return p if p.exists() else None


def ocr_text(image: Union[str, Path, bytes]) -> str:
    """
    Return the full OCR text of a check image (path or raw bytes).

    Credentials, in order: explicit service-account JSON (GVC_DRIVE_CREDENTIALS or
    .google-service-account.json) → otherwise Application Default Credentials
    (GOOGLE_APPLICATION_CREDENTIALS, `gcloud auth application-default login`, or
    the Cloud Run runtime service account). Raises VisionNotConfigured if the dep
    is missing; lets Vision API / auth errors propagate so the caller sees them.
    """
    try:
        from google.cloud import vision
    except ImportError as e:  # dep not installed in this environment
        raise VisionNotConfigured(f"google-cloud-vision not available: {e}")

    content = image if isinstance(image, bytes) else Path(image).read_bytes()
    creds_path = _explicit_creds_path()
    if creds_path:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(str(creds_path))
        client = vision.ImageAnnotatorClient(credentials=creds)
    else:
        client = vision.ImageAnnotatorClient()  # Application Default Credentials

    response = client.document_text_detection(image=vision.Image(content=content))
    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")
    return response.full_text_annotation.text or ""


if __name__ == "__main__":  # pragma: no cover — local tuning harness
    import sys

    if len(sys.argv) < 2:
        print("usage: python vision.py /path/to/check.jpg", file=sys.stderr)
        raise SystemExit(2)
    # Take the first arg only (ignore a stray trailing shell comment) and expand
    # a leading ~ even if it arrived quoted.
    print(ocr_text(os.path.expanduser(sys.argv[1])))
