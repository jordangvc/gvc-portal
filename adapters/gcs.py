"""
Google Cloud Storage uploader for GVC invoice PDF previews.
=========================================================================
On dry-run, the invoice flow renders a PDF (and any change-order PDFs) to
the container's /tmp directory. Those files are not directly viewable by
Andrea or Claude — the dry-run writeback only reports their in-container
paths. To make dry-run a real visual gate, we upload each rendered PDF to
a Cloud Storage bucket and return a short-lived signed URL that the
caller can open in a browser.

Auth: reuses the same service-account JSON as Drive
(`.google-service-account.json` locally, `/secrets/google-service-account.json`
on Cloud Run). The SA must have `roles/storage.objectAdmin` (or the pair
`objectCreator` + `objectViewer`) on the preview bucket. See
docs/cloud-run-deploy.md.

Designed to be importable without crashing when GCS isn't configured —
`upload_preview_pdf` raises GCSNotConfigured, which callers should treat
as "skip the upload step, dry-run still succeeds".
"""
from __future__ import annotations

from shared import paths
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


class GCSNotConfigured(Exception):
    """Raised when the preview bucket env var or service account JSON is missing."""


def upload_preview_pdf(
    local_path: Path,
    *,
    identifier: str,
    run_timestamp: Optional[str] = None,
    bucket_name: Optional[str] = None,
    creds_path: Optional[Path] = None,
    expiry_seconds: int = 3600,
) -> str:
    """
    Upload `local_path` to the preview bucket and return a signed URL.

    Blob layout: `{identifier}/{run_timestamp}/{local_path.name}`. Grouping
    by identifier + per-run timestamp keeps multiple dry-runs of the same
    invoice from clobbering each other, which matters when Andrea iterates
    on a draft.

    `run_timestamp` is optional so the caller can use a single timestamp
    for the main PDF and its CO PDFs (cleaner UX — all preview URLs from
    one dry-run share a folder prefix). When omitted, defaults to "now".

    Raises GCSNotConfigured if the bucket env var is unset or the service
    account JSON can't be found. Other exceptions (network, permissions)
    propagate so the caller can decide whether to soft-fail.
    """
    bucket = bucket_name or os.environ.get("GVC_GCS_PREVIEW_BUCKET")
    if not bucket:
        raise GCSNotConfigured(
            "GVC_GCS_PREVIEW_BUCKET env var not set; cannot upload PDF preview. "
            "See docs/cloud-run-deploy.md for bucket setup."
        )

    creds_path_resolved = Path(
        creds_path
        or os.environ.get("GVC_DRIVE_CREDENTIALS")
        or paths.DEFAULT_SA_PATH
    )
    if not creds_path_resolved.exists():
        raise GCSNotConfigured(
            f"Google service account JSON not found at {creds_path_resolved}. "
            f"Cannot upload PDF preview for {identifier}."
        )

    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Preview source PDF not found: {local_path}")

    # Imported lazily so the module loads cleanly when the optional dep
    # isn't installed (e.g., during local CLI use without GCS).
    from google.cloud import storage
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        str(creds_path_resolved)
    )

    ts = run_timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    blob_name = f"{identifier}/{ts}/{local_path.name}"

    client = storage.Client(credentials=creds, project=creds.project_id)
    blob = client.bucket(bucket).blob(blob_name)
    blob.upload_from_filename(str(local_path), content_type="application/pdf")

    # The SA JSON contains the private key, so generate_signed_url() can
    # sign locally without an IAM signBlob round-trip.
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expiry_seconds),
        method="GET",
    )


def utc_timestamp() -> str:
    """Stable run-timestamp shared across main + CO uploads in one dry-run."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
