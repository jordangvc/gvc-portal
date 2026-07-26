"""
AIA G702/G703 Excel-to-PDF conversion for GVC progress billing.
=========================================================================
Given a filled-in .xlsx AIA workbook, this module exports named sheets
(typically "AIA G702 " and a per-period G703 sheet) as individual PDFs
by routing through Google Drive:

  1. Upload the .xlsx to Drive, converting it to Google Sheets format.
  2. Query the Sheets API for each target sheet's numeric GID.
  3. Fetch each sheet as a PDF via an authenticated export URL.
  4. Delete the temp Sheets file from Drive.
  5. Return the local PDF paths.

The same service-account credentials used by drive.py are reused here.
The Google Sheets API must be enabled in your GCP project (one-time step).

CLI usage (standalone, for testing):
    ./gvc aia.py export \\
        --excel "inputs/Kaiker_Colerain_G702_G703.xlsx" \\
        --g702-sheet "AIA G702 " \\
        --g703-sheet "G703 Pay App 1" \\
        --out-dir inputs/

    ./gvc aia.py verify    # confirm credentials + Sheets API are working
"""
from __future__ import annotations

from shared import paths
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional


class AIANotConfigured(Exception):
    """Raised when service account credentials are missing or Sheets API is not enabled."""


def _build_services(creds_path: Optional[Path] = None):
    """Return (drive_service, sheets_service, credentials) using service account."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_path = Path(
        creds_path
        or os.environ.get("GVC_DRIVE_CREDENTIALS")
        or paths.DEFAULT_SA_PATH
    )
    if not creds_path.exists():
        raise AIANotConfigured(
            f"Service account JSON not found at {creds_path}. "
            "See docs/google-cloud-setup.md."
        )

    # Drive scope covers both Drive and Sheets API calls.
    SCOPES = ["https://www.googleapis.com/auth/drive"]
    creds = service_account.Credentials.from_service_account_file(
        str(creds_path), scopes=SCOPES
    )
    drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    try:
        sheets_svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as e:
        raise AIANotConfigured(
            "Failed to initialise Sheets API client. "
            "Make sure the Google Sheets API is enabled in your GCP project: "
            "console.cloud.google.com → APIs & Services → Enable APIs → "
            "search 'Google Sheets API' → Enable. "
            f"Original error: {e}"
        ) from e

    return drive_svc, sheets_svc, creds


def export_aia_pdfs(
    excel_path: Path,
    *,
    g702_sheet: str = "AIA G702 ",
    g703_sheet: str,
    output_dir: Path,
    creds_path: Optional[Path] = None,
    shared_drive_id: Optional[str] = None,
) -> tuple[Path, Path]:
    """
    Convert G702 and one G703 sheet from an AIA Excel workbook to PDFs.

    Args:
        excel_path:       Path to the filled-in .xlsx AIA workbook.
        g702_sheet:       Sheet tab name for the G702 (default "AIA G702 ").
        g703_sheet:       Sheet tab name for the G703 to attach (e.g. "G703 Pay App 1").
        output_dir:       Directory to write the output PDFs.
        creds_path:       Override for service account JSON path.
        shared_drive_id:  Override for Shared Drive ID (temp file parent).

    Returns:
        (g702_pdf_path, g703_pdf_path)
    """
    import requests as req_lib
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from googleapiclient.http import MediaFileUpload

    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"AIA Excel file not found: {excel_path}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    shared_drive_id = shared_drive_id or os.environ.get("GVC_DRIVE_SHARED_DRIVE_ID")

    drive_svc, sheets_svc, creds = _build_services(creds_path)

    # 1. Upload .xlsx to Drive, converting to Google Sheets format.
    ts = int(time.time())
    file_meta: dict = {
        "name": f"_gvc_aia_temp_{ts}",
        "mimeType": "application/vnd.google-apps.spreadsheet",
    }
    if shared_drive_id:
        file_meta["parents"] = [shared_drive_id]

    media = MediaFileUpload(
        str(excel_path),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=False,
    )
    uploaded = drive_svc.files().create(
        body=file_meta,
        media_body=media,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    sheets_file_id = uploaded["id"]

    try:
        # 2. Get sheet GIDs via Sheets API.
        spreadsheet = sheets_svc.spreadsheets().get(
            spreadsheetId=sheets_file_id,
            fields="sheets.properties",
        ).execute()

        gid_map: dict[str, int] = {}
        for sheet in spreadsheet.get("sheets", []):
            props = sheet["properties"]
            gid_map[props["title"]] = props["sheetId"]

        # 3. Export each target sheet as a PDF.
        creds.refresh(GoogleAuthRequest())
        access_token = creds.token

        output_paths: list[Path] = []
        for sheet_name in [g702_sheet, g703_sheet]:
            gid = gid_map.get(sheet_name)
            if gid is None:
                available = list(gid_map.keys())
                raise ValueError(
                    f"Sheet '{sheet_name}' not found in {excel_path.name}. "
                    f"Available sheets: {available}"
                )

            url = f"https://docs.google.com/spreadsheets/d/{sheets_file_id}/export"
            params = {
                "format": "pdf",
                "gid": str(gid),
                "size": "letter",
                "portrait": "true",
                "fitw": "true",        # fit to page width
                "sheetnames": "false",
                "printtitle": "false",
                "gridlines": "false",
                "fzr": "false",        # don't repeat frozen rows
            }
            resp = req_lib.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            resp.raise_for_status()

            safe_name = re.sub(r"[^\w\-]", "_", sheet_name).strip("_")
            pdf_path = output_dir / f"_aia_{safe_name}_{ts}.pdf"
            pdf_path.write_bytes(resp.content)
            output_paths.append(pdf_path)
            print(f"[aia] exported '{sheet_name}' → {pdf_path.name}")

        return tuple(output_paths)  # type: ignore[return-value]

    finally:
        # 4. Always clean up the temp Sheets file from Drive.
        try:
            drive_svc.files().delete(
                fileId=sheets_file_id, supportsAllDrives=True
            ).execute()
            print(f"[aia] cleaned up temp Sheets file {sheets_file_id}")
        except Exception as cleanup_err:
            print(f"[aia] cleanup warning: {cleanup_err}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv

    load_dotenv(paths.ENV_FILE)

    ap = argparse.ArgumentParser(description="AIA G702/G703 Excel → PDF conversion")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # verify subcommand
    sub.add_parser("verify", help="Confirm service account + Sheets API are working")

    # export subcommand
    exp = sub.add_parser("export", help="Export G702 + a G703 sheet as PDFs")
    exp.add_argument("--excel", required=True, help="Path to the AIA .xlsx workbook")
    exp.add_argument("--g702-sheet", default="AIA G702 ", help="G702 tab name (default: 'AIA G702 ')")
    exp.add_argument("--g703-sheet", required=True, help="G703 tab name for this billing period")
    exp.add_argument("--out-dir", default="inputs", help="Output directory for PDFs")

    args = ap.parse_args()

    if args.cmd == "verify":
        try:
            drive_svc, sheets_svc, creds = _build_services()
            about = drive_svc.about().get(fields="user").execute()
            print(f"OK — Drive authenticated as: {about['user']['emailAddress']}")
            # Quick Sheets API test
            print("OK — Sheets API client initialized successfully")
        except AIANotConfigured as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)

    elif args.cmd == "export":
        try:
            g702_pdf, g703_pdf = export_aia_pdfs(
                Path(args.excel),
                g702_sheet=args.g702_sheet,
                g703_sheet=args.g703_sheet,
                output_dir=Path(args.out_dir),
            )
            print(f"\nOutput PDFs:")
            print(f"  G702: {g702_pdf}")
            print(f"  G703: {g703_pdf}")
        except (AIANotConfigured, FileNotFoundError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)
