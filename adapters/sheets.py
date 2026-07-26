"""
Google Sheets adapter — read a spreadsheet + write cell values back.
=========================================================================
Built for the bulk COI run ("Annual COI List"): read the whole sheet, then
write YES/NO into the Sent/Status column per processed row. Deliberately
tiny — two operations, nothing clever.

Auth: the SAME service-account JSON as Drive/GCS (GVC_DRIVE_CREDENTIALS /
.google-service-account.json), scope spreadsheets. The target spreadsheet
must be SHARED with the service account
(gvc-invoice-bot@gvc-invoice-system.iam.gserviceaccount.com) — Editor for
writeback, or the run fails with a clean 403 message.

Importable without the Google libs (lazy imports); constructor raises
SheetsNotConfigured when creds are absent, mirroring DriveNotConfigured.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from shared import paths

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsNotConfigured(Exception):
    """Raised when the service-account JSON is missing."""


def spreadsheet_id_from_url(url: str) -> Optional[str]:
    """
    Pure: extract a spreadsheet ID from a Google Sheets URL (or accept a
    bare ID). Handles the shapes people paste:
      https://docs.google.com/spreadsheets/d/<ID>/edit?usp=sharing
      https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0
      <ID>
    """
    if not url:
        return None
    s = url.strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{10,})", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", s):
        return s
    return None


def column_letter(index: int) -> str:
    """Pure: 0-based column index -> A1 letter(s). 0->A, 25->Z, 26->AA."""
    if index < 0:
        raise ValueError("column index must be >= 0")
    letters = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


class SheetsClient:
    def __init__(self, creds_path: Optional[Path] = None) -> None:
        creds_path = Path(
            creds_path
            or os.environ.get("GVC_DRIVE_CREDENTIALS")
            or paths.DEFAULT_SA_PATH
        )
        if not creds_path.exists():
            raise SheetsNotConfigured(
                f"Google service account JSON not found at {creds_path}."
            )
        self.creds_path = creds_path
        self._service = None

    @property
    def service(self):
        if self._service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                str(self.creds_path), scopes=SCOPES
            )
            self._service = build("sheets", "v4", credentials=creds,
                                  cache_discovery=False)
        return self._service

    def first_sheet_title(self, spreadsheet_id: str) -> str:
        """Title of the first (leftmost) tab — where the Annual COI List lives."""
        meta = (
            self.service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id,
                 fields="sheets(properties(title,index))")
            .execute()
        )
        sheets = meta.get("sheets") or []
        if not sheets:
            raise RuntimeError("The spreadsheet has no sheets.")
        first = min(sheets, key=lambda s: s["properties"].get("index", 0))
        return first["properties"]["title"]

    def read_rows(self, spreadsheet_id: str, *, tab_title: Optional[str] = None,
                  max_rows: int = 1000) -> tuple[str, list[list[str]]]:
        """
        Return (tab_title, rows) — every row as a list of cell strings
        (trailing empty cells may be absent, per the Sheets API). Reads
        columns A..Z which comfortably covers the COI list.
        """
        tab = tab_title or self.first_sheet_title(spreadsheet_id)
        out = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{tab}'!A1:Z{max_rows}")
            .execute()
        )
        return tab, out.get("values") or []

    def write_cell(self, spreadsheet_id: str, *, tab_title: str,
                   row_number: int, col_index: int, value: str) -> None:
        """Write one cell (1-based row_number, 0-based col_index). RAW input —
        the value lands exactly as given."""
        rng = f"'{tab_title}'!{column_letter(col_index)}{row_number}"
        (
            self.service.spreadsheets()
            .values()
            .update(spreadsheetId=spreadsheet_id, range=rng,
                    valueInputOption="RAW", body={"values": [[value]]})
            .execute()
        )
