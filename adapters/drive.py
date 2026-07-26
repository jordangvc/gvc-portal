"""
Google Drive uploader for GVC invoices.
=========================================================================
Idempotent folder + file management on a Shared Drive.

Folder hierarchy: <Shared Drive root> / Invoices / <year> / <customer> /
Filename:         <customer>_<job_street_address>_<invoice_number>.pdf

Auth: service account JSON key. The service account must be added as a
member (Content manager or higher) of the Shared Drive identified by
GVC_DRIVE_SHARED_DRIVE_ID. See docs/google-cloud-setup.md.

Designed to be importable without crashing when credentials are absent —
the constructor raises DriveNotConfigured, which callers should treat as
"skip the upload step gracefully".
"""
from __future__ import annotations

from shared import paths
import json
import os
import re
from pathlib import Path
from typing import Optional

ROOT_FOLDER_NAME = "Invoices"
SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveNotConfigured(Exception):
    """Raised when service account or Shared Drive ID is missing."""


def folder_id_from_url(url: str) -> Optional[str]:
    """
    Pure: extract a Google Drive folder ID from a folder URL (or accept a bare
    ID). Returns None if nothing folder-id-shaped is found.

    Handles the common shapes Andrea/Jake paste:
      https://drive.google.com/drive/folders/<ID>
      https://drive.google.com/drive/folders/<ID>?usp=sharing
      https://drive.google.com/drive/u/0/folders/<ID>
      https://drive.google.com/open?id=<ID>
      <ID>            (already just the id)
    """
    if not url:
        return None
    s = url.strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", s)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", s)
    if m:
        return m.group(1)
    # Bare ID (no slashes, no spaces) — Drive IDs are URL-safe base64-ish.
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", s):
        return s
    return None


def slug_for_path(s: str, *, max_len: int = 80) -> str:
    """
    Sanitize a string for use in a Drive folder/file name.

    Drive permits most characters, but we strip / \\ : * ? " < > | and
    collapse whitespace so the result is portable across filesystems too
    (in case a user downloads the PDF to a local machine).
    """
    s = re.sub(r"[\\/:*?\"<>|]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def download_drive_file(
    file_id: str,
    dest_path: Path,
    *,
    creds_path: Optional[Path] = None,
) -> Path:
    """
    Download a Drive file by ID to a local path. Returns dest_path.

    Used by the cloud commercial flow: Claude sends the JSON with a Drive
    file ID (instead of a local path) for the AIA xlsx and any CO appendix
    images, and the service downloads each file to /tmp before running the
    existing local-path code path. Avoids having to teach aia.py /
    change_order.py to read from Drive directly.

    Service account must have at least Viewer access to the file.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    creds_path_resolved = Path(
        creds_path
        or os.environ.get("GVC_DRIVE_CREDENTIALS")
        or paths.DEFAULT_SA_PATH
    )
    if not creds_path_resolved.exists():
        raise DriveNotConfigured(
            f"Google service account JSON not found at {creds_path_resolved}. "
            f"Cannot download Drive file {file_id}."
        )

    creds = service_account.Credentials.from_service_account_file(
        str(creds_path_resolved), scopes=SCOPES
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with dest_path.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path


def resolve_local_or_drive_path(
    path_or_drive_ref: str,
    *,
    tmp_dir: Path,
    project_root: Path,
) -> Path:
    """
    Resolve a JSON field that may be either:
      - a `drive:FILE_ID` reference (download to tmp_dir, return that path)
      - a local path (absolute or relative to project_root)

    The `drive:` prefix lets a single field accept either form without
    breaking existing JSON files that use local paths.
    """
    if path_or_drive_ref.startswith("drive:"):
        file_id = path_or_drive_ref[len("drive:"):].strip()
        if not file_id:
            raise ValueError(
                f"Empty Drive file ID in 'drive:' reference: {path_or_drive_ref!r}"
            )
        # Use the file ID as the filename suffix so multiple downloads don't
        # collide. The extension is reconstructed from Drive metadata.
        meta = _get_drive_file_metadata(file_id)
        ext = _ext_from_drive_metadata(meta)
        dest = tmp_dir / f"gvc_drive_{file_id}{ext}"
        if not dest.exists():
            download_drive_file(file_id, dest)
        return dest

    p = Path(path_or_drive_ref)
    if not p.is_absolute():
        p = project_root / p
    return p


def _get_drive_file_metadata(file_id: str, *, creds_path: Optional[Path] = None) -> dict:
    """Fetch name + mimeType for a Drive file. Used to pick the right extension."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_path_resolved = Path(
        creds_path
        or os.environ.get("GVC_DRIVE_CREDENTIALS")
        or paths.DEFAULT_SA_PATH
    )
    creds = service_account.Credentials.from_service_account_file(
        str(creds_path_resolved), scopes=SCOPES
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return service.files().get(
        fileId=file_id, fields="name, mimeType", supportsAllDrives=True,
    ).execute()


def _ext_from_drive_metadata(meta: dict) -> str:
    """Best-effort extension from Drive metadata. Empty string if unknown."""
    name = meta.get("name", "")
    if "." in name:
        return "." + name.rsplit(".", 1)[1].lower()
    mime = meta.get("mimeType", "")
    return {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "application/pdf": ".pdf",
    }.get(mime, "")


class DriveUploader:
    def __init__(
        self,
        creds_path: Optional[Path] = None,
        shared_drive_id: Optional[str] = None,
    ) -> None:
        creds_path = Path(
            creds_path
            or os.environ.get("GVC_DRIVE_CREDENTIALS")
            or paths.DEFAULT_SA_PATH
        )
        shared_drive_id = shared_drive_id or os.environ.get("GVC_DRIVE_SHARED_DRIVE_ID")

        if not creds_path.exists():
            raise DriveNotConfigured(
                f"Google service account JSON not found at {creds_path}. "
                f"See docs/google-cloud-setup.md."
            )
        if not shared_drive_id:
            raise DriveNotConfigured(
                "GVC_DRIVE_SHARED_DRIVE_ID env var not set. "
                "See docs/google-cloud-setup.md."
            )

        self.creds_path = creds_path
        self.shared_drive_id = shared_drive_id
        self._service = None

    @property
    def service(self):
        if self._service is None:
            # Lazy-import the Google libs so importing this module doesn't
            # require them when the script runs in --dry-run mode.
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                str(self.creds_path), scopes=SCOPES
            )
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    # ---- Shared Drive API helpers ----

    def _list_kwargs(self) -> dict:
        return {
            "corpora": "drive",
            "driveId": self.shared_drive_id,
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
        }

    def _mutate_kwargs(self) -> dict:
        return {"supportsAllDrives": True}

    # ---- Folder + file primitives ----

    def _find_child(
        self, parent_id: str, name: str, *, is_folder: bool = False
    ) -> Optional[dict]:
        safe = name.replace("'", "\\'")
        mime_clause = (
            "mimeType = 'application/vnd.google-apps.folder'"
            if is_folder
            else "mimeType != 'application/vnd.google-apps.folder'"
        )
        q = (
            f"name = '{safe}' and {mime_clause} and "
            f"'{parent_id}' in parents and trashed = false"
        )
        result = (
            self.service.files()
            .list(q=q, fields="files(id, name)", **self._list_kwargs())
            .execute()
        )
        files = result.get("files", [])
        return files[0] if files else None

    def ensure_folder(self, name: str, parent_id: str) -> str:
        """Return folder ID, creating it under parent_id if it doesn't exist."""
        existing = self._find_child(parent_id, name, is_folder=True)
        if existing:
            return existing["id"]
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        created = (
            self.service.files()
            .create(body=body, fields="id", **self._mutate_kwargs())
            .execute()
        )
        return created["id"]

    def list_child_names(self, parent_id: str, *, folders: bool = False) -> list[str]:
        """
        Return the names of children under parent_id (non-folders by default).
        Used by the Change Order flow to discover existing CO PDFs in a job
        folder so the next CO number can be computed even without Monday.
        Paginates so large folders aren't truncated.
        """
        mime_clause = (
            "mimeType = 'application/vnd.google-apps.folder'"
            if folders
            else "mimeType != 'application/vnd.google-apps.folder'"
        )
        q = f"'{parent_id}' in parents and {mime_clause} and trashed = false"
        names: list[str] = []
        page_token: Optional[str] = None
        while True:
            result = (
                self.service.files()
                .list(
                    q=q,
                    fields="nextPageToken, files(name)",
                    pageToken=page_token,
                    pageSize=200,
                    **self._list_kwargs(),
                )
                .execute()
            )
            names.extend(f["name"] for f in result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return names

    def find_child_file(self, parent_id: str, name: str) -> Optional[dict]:
        """Public wrapper: find a non-folder child by exact name under
        parent_id. Returns {id, name} or None. Used by the estimate revision
        flow to locate the current PDF/sidecar before archiving them."""
        return self._find_child(parent_id, name, is_folder=False)

    def rename_file(self, file_id: str, new_name: str) -> dict:
        """
        Rename a Drive file in place (file ID — and therefore every existing
        link — is preserved). Used by the estimate revision flow to archive a
        superseded PDF/sidecar under an `e{n}-` prefix instead of overwriting
        it: a sent document is never destroyed, only relabeled.
        """
        info = (
            self.service.files()
            .update(fileId=file_id, body={"name": new_name},
                    fields="id, name, webViewLink", **self._mutate_kwargs())
            .execute()
        )
        return {
            "file_id": info["id"],
            "filename": info.get("name") or new_name,
            "web_view_link": info.get("webViewLink"),
        }

    def upload_invoice_pdf(
        self,
        pdf_path: Path,
        *,
        customer: str,
        year: int,
        job_street_address: str,
        invoice_number: str,
    ) -> dict:
        """
        Upload (or replace) a PDF at:
          <Shared Drive>/Invoices/<year>/<customer>/<customer>_<street>_<inv#>.pdf

        Returns: {file_id, web_view_link, folder_path, filename}.
        Idempotent: re-uploading the same filename replaces the file contents.
        """
        from googleapiclient.http import MediaFileUpload

        customer_slug = slug_for_path(customer)
        street_slug = slug_for_path(job_street_address)
        filename = f"{customer_slug}_{street_slug}_{invoice_number}.pdf"

        # Build the folder path top-down
        invoices_root = self.ensure_folder(ROOT_FOLDER_NAME, self.shared_drive_id)
        year_folder = self.ensure_folder(str(year), invoices_root)
        customer_folder = self.ensure_folder(customer_slug, year_folder)

        media = MediaFileUpload(str(pdf_path), mimetype="application/pdf", resumable=False)
        existing = self._find_child(customer_folder, filename, is_folder=False)

        if existing:
            file_id = existing["id"]
            self.service.files().update(
                fileId=file_id, media_body=media, **self._mutate_kwargs()
            ).execute()
        else:
            body = {"name": filename, "parents": [customer_folder]}
            created = (
                self.service.files()
                .create(
                    body=body,
                    media_body=media,
                    fields="id",
                    **self._mutate_kwargs(),
                )
                .execute()
            )
            file_id = created["id"]

        info = (
            self.service.files()
            .get(fileId=file_id, fields="id, webViewLink", **self._mutate_kwargs())
            .execute()
        )
        return {
            "file_id": file_id,
            "web_view_link": info.get("webViewLink"),
            "folder_path": f"{ROOT_FOLDER_NAME}/{year}/{customer_slug}",
            "filename": filename,
        }

    def upload_invoice_pdf_to_folder(
        self,
        pdf_path: Path,
        *,
        folder_id: str,
        customer: str,
        job_street_address: str,
        invoice_number: str,
    ) -> dict:
        """
        Upload the GVC invoice PDF directly to a known target folder, using
        the standardized filename `<customer>_<street>_<invoice#>.pdf`.

        Used by the new directory structure (2026-05-20+) where Andrea +
        an admin pre-create the Drive tree as:
            Projects/<year>/<Residential|Commercial>/<customer>/
                <[Number Street] | [Builder/Client]>/Invoice/
        and pass the Invoice/ folder ID through the JSON as
        `drive_invoice_folder_id` (or its alias `drive_source_folder_id`).

        Distinct from upload_invoice_pdf() which walks the old
        Invoices/<year>/<customer>/ tree and creates folders as needed.
        This method does NO folder creation — caller is responsible for
        the folder already existing.

        Idempotent: a file with the same name in the target folder is
        replaced in-place (preserves file ID so existing links keep working).
        """
        from googleapiclient.http import MediaFileUpload

        customer_slug = slug_for_path(customer)
        street_slug = slug_for_path(job_street_address)
        filename = f"{customer_slug}_{street_slug}_{invoice_number}.pdf"

        media = MediaFileUpload(str(pdf_path), mimetype="application/pdf", resumable=False)
        existing = self._find_child(folder_id, filename, is_folder=False)

        if existing:
            file_id = existing["id"]
            self.service.files().update(
                fileId=file_id, media_body=media, **self._mutate_kwargs()
            ).execute()
        else:
            body = {"name": filename, "parents": [folder_id]}
            created = (
                self.service.files()
                .create(
                    body=body,
                    media_body=media,
                    fields="id",
                    **self._mutate_kwargs(),
                )
                .execute()
            )
            file_id = created["id"]

        info = (
            self.service.files()
            .get(fileId=file_id, fields="id, webViewLink", **self._mutate_kwargs())
            .execute()
        )
        return {
            "file_id": file_id,
            "web_view_link": info.get("webViewLink"),
            "folder_id": folder_id,
            "filename": filename,
        }

    def upload_pdf_to_folder(
        self,
        pdf_path: Path,
        *,
        folder_id: str,
        filename: Optional[str] = None,
    ) -> dict:
        """
        Upload a PDF into a specific Drive folder. Idempotent: an existing
        file with the same name in that folder is replaced in-place (preserves
        its file ID so existing links keep working).

        Used to write fresh G702/G703 PDFs back to the project source folder
        on each progress-invoice run so the folder reflects current state.

        Returns: {file_id, web_view_link, filename}.
        """
        from googleapiclient.http import MediaFileUpload

        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        name = filename or pdf_path.name

        media = MediaFileUpload(str(pdf_path), mimetype="application/pdf", resumable=False)
        existing = self._find_child(folder_id, name, is_folder=False)
        if existing:
            file_id = existing["id"]
            self.service.files().update(
                fileId=file_id, media_body=media, **self._mutate_kwargs()
            ).execute()
        else:
            body = {"name": name, "parents": [folder_id]}
            created = (
                self.service.files()
                .create(
                    body=body,
                    media_body=media,
                    fields="id",
                    **self._mutate_kwargs(),
                )
                .execute()
            )
            file_id = created["id"]

        info = (
            self.service.files()
            .get(fileId=file_id, fields="id, webViewLink", **self._mutate_kwargs())
            .execute()
        )
        return {
            "file_id": file_id,
            "web_view_link": info.get("webViewLink"),
            "filename": name,
        }

    def _find_child_any_drive(self, parent_id: str, name: str) -> Optional[dict]:
        """
        Like _find_child, but searches across ALL shared drives the service
        account can access (corpora="allDrives") instead of pinning to
        self.shared_drive_id. Lets callers target a folder that lives in a
        different shared drive than GVC_DRIVE_SHARED_DRIVE_ID (e.g. the activity
        backup folder under the Office shared drive).
        """
        safe = name.replace("'", "\\'")
        q = (
            f"name = '{safe}' and "
            f"mimeType != 'application/vnd.google-apps.folder' and "
            f"'{parent_id}' in parents and trashed = false"
        )
        result = (
            self.service.files()
            .list(
                q=q,
                fields="files(id, name)",
                corpora="allDrives",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        files = result.get("files", [])
        return files[0] if files else None

    def upload_or_replace_file(
        self,
        *,
        folder_id: str,
        filename: str,
        data: bytes,
        mimetype: str = "application/octet-stream",
    ) -> dict:
        """
        Upload arbitrary in-memory bytes into a Drive folder. Idempotent: an
        existing file with the same name in that folder is replaced in place
        (file ID + links preserved), so re-running a month's backup overwrites
        rather than duplicates.

        Drive-agnostic existence check (corpora="allDrives"), so folder_id may
        live in any shared drive the service account is a member of -- not only
        GVC_DRIVE_SHARED_DRIVE_ID.

        Returns: {file_id, web_view_link, filename, action}.
        """
        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(data, mimetype=mimetype, resumable=False)
        existing = self._find_child_any_drive(folder_id, filename)
        if existing:
            file_id = existing["id"]
            self.service.files().update(
                fileId=file_id, media_body=media, **self._mutate_kwargs()
            ).execute()
            action = "updated"
        else:
            body = {"name": filename, "parents": [folder_id]}
            created = (
                self.service.files()
                .create(
                    body=body,
                    media_body=media,
                    fields="id",
                    **self._mutate_kwargs(),
                )
                .execute()
            )
            file_id = created["id"]
            action = "created"

        info = (
            self.service.files()
            .get(fileId=file_id, fields="id, webViewLink", **self._mutate_kwargs())
            .execute()
        )
        return {
            "file_id": file_id,
            "web_view_link": info.get("webViewLink"),
            "filename": filename,
            "action": action,
        }

    def find_file_anywhere(self, name: str) -> Optional[dict]:
        """
        Find a non-folder file by exact name across ALL shared drives the service
        account can access. Returns the most recently modified match
        {id, name, webViewLink} or None. Used to locate an invoice's persisted
        JSON sidecar (e.g. "GVC-2026-MV-007.gvc.json") for the correction flow,
        without needing to know which folder it lives in.
        """
        safe = name.replace("'", "\\'")
        q = (
            f"name = '{safe}' and "
            f"mimeType != 'application/vnd.google-apps.folder' and trashed = false"
        )
        result = (
            self.service.files()
            .list(
                q=q,
                fields="files(id, name, webViewLink, modifiedTime)",
                orderBy="modifiedTime desc",
                corpora="allDrives",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=5,
            )
            .execute()
        )
        files = result.get("files", [])
        return files[0] if files else None

    def download_json(self, file_id: str) -> dict:
        """Download a small Drive file by ID and parse it as JSON."""
        import io as _io

        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = _io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return json.loads(buf.getvalue().decode("utf-8"))

    def ensure_completed_invoices_subfolder(
        self, parent_folder_id: str, *, date_str: str
    ) -> str:
        """
        Create-or-find a "Completed Invoices <date_str>" subfolder under the
        given parent (typically a job's Invoice/ folder). Returns the
        subfolder ID.

        Convention added 2026-05-20 so that each live invoice run lands its
        generated artifacts (invoice PDF, G702/G703, CO Templates, sentinel)
        in their own dated subfolder, leaving the parent folder's source
        materials untouched. Two practical benefits:

          - Multiple progress bills on a commercial job are visually
            separated by date, instead of newer PDFs overwriting (or sitting
            next to) older ones at the folder root.
          - Anyone opening the Invoice/ folder sees billing instructions /
            AIA xlsx / photos at the root, and a clear timeline of completed
            runs in subfolders below.

        Idempotent on (parent_folder_id, date_str): re-running on the same
        day reuses the same subfolder, so file replacements happen in-place.

        Caller decides date_str (this method does not impose a timezone).
        The canonical caller uses UTC today's date in YYYY-MM-DD format.
        """
        return self.ensure_folder(f"Completed Invoices {date_str}", parent_folder_id)

    def write_completion_sentinel(
        self,
        folder_id: str,
        *,
        invoice_identifier: str,
        body: str = "Invoice made and sent.",
    ) -> dict:
        """
        Drop a small plain-text marker into the project's billing-instructions
        folder so anyone looking at that folder can see the invoice was issued.

        Idempotent: re-running for the same invoice replaces the file contents.
        Filename: "{invoice_identifier} - made and sent.txt".

        Use this when the source-of-truth folder for a job's billing
        instructions is NOT the same as the archive folder (Invoices/<year>/
        <customer>/). Pass the folder ID via job.drive_source_folder_id in the
        invoice JSON.

        Returns: {file_id, name}.
        """
        from googleapiclient.http import MediaInMemoryUpload

        filename = f"{invoice_identifier} - made and sent.txt"
        media = MediaInMemoryUpload(body.encode("utf-8"), mimetype="text/plain")
        existing = self._find_child(folder_id, filename, is_folder=False)

        if existing:
            file_id = existing["id"]
            self.service.files().update(
                fileId=file_id, media_body=media, **self._mutate_kwargs()
            ).execute()
        else:
            created = (
                self.service.files()
                .create(
                    body={"name": filename, "parents": [folder_id]},
                    media_body=media,
                    fields="id",
                    **self._mutate_kwargs(),
                )
                .execute()
            )
            file_id = created["id"]

        return {"file_id": file_id, "name": filename}

    def ensure_estimate_folder(
        self,
        *,
        customer: str,
        project_label: str,
        project_type: str,
        year: int,
    ) -> dict:
        """
        Find-or-create the estimate folder chain (confirmed design,
        2026-06-10; mirrors the 2026-05-20 invoice directory convention):

            <Projects root>/<year>/<Residential|Commercial>/<customer>/
                <project_label>/Estimate/

        `project_label` is the naming-convention folder name, e.g.
        "9195 Silva | Willow Creek" (residential) or "Cintas | 3368 Turfway"
        (commercial) — built by the caller, slugged here. `project_type` is
        "residential" or "commercial". The root folder name defaults to
        "Projects" (override: GVC_PROJECTS_ROOT_FOLDER).

        Returns {folder_id, folder_path}. Idempotent at every level.
        """
        root_name = (os.environ.get("GVC_PROJECTS_ROOT_FOLDER") or "Projects").strip()
        type_name = "Commercial" if (project_type or "").lower().startswith("c") else "Residential"
        customer_slug = slug_for_path(customer)
        project_slug = slug_for_path(project_label)

        folder_id = self.ensure_folder(root_name, self.shared_drive_id)
        path_parts = [root_name]
        for name in (str(year), type_name, customer_slug, project_slug, "Estimate"):
            folder_id = self.ensure_folder(name, folder_id)
            path_parts.append(name)
        return {"folder_id": folder_id, "folder_path": "/".join(path_parts)}

    def ensure_invoice_folder(
        self,
        *,
        customer: str,
        project_label: str,
        project_type: str,
        year: int,
    ) -> dict:
        """
        Find-or-create the invoice folder chain (mirrors ensure_estimate_folder):

            <Projects root>/<year>/<Residential|Commercial>/<customer>/
                <project_label>/Invoice/

        Used by the live invoice flow when the JSON did NOT carry an explicit
        drive_invoice_folder_id (e.g. portal / Monday-sourced runs). Keeps invoice
        PDFs inside the project's own folder tree instead of the legacy
        Invoices/<year>/<customer>/ archive. `project_type` is "residential" or
        "commercial"; the root folder defaults to "Projects"
        (override: GVC_PROJECTS_ROOT_FOLDER).

        Returns {folder_id, folder_path}. Idempotent at every level.
        """
        root_name = (os.environ.get("GVC_PROJECTS_ROOT_FOLDER") or "Projects").strip()
        type_name = "Commercial" if (project_type or "").lower().startswith("c") else "Residential"
        customer_slug = slug_for_path(customer)
        project_slug = slug_for_path(project_label)

        folder_id = self.ensure_folder(root_name, self.shared_drive_id)
        path_parts = [root_name]
        for name in (str(year), type_name, customer_slug, project_slug, "Invoice"):
            folder_id = self.ensure_folder(name, folder_id)
            path_parts.append(name)
        return {"folder_id": folder_id, "folder_path": "/".join(path_parts)}

    def ensure_coi_folder(self, *, year: int) -> dict:
        """
        Find-or-create the outbound-COI record folder:

            <COIs root>/<year>/

        COIs are builder-level, not project-level — many go out before any
        project folder exists — so they live in their own top-level tree on
        the shared drive (a browsable record of every certificate sent; the
        bulk annual run files here too). Root folder name defaults to
        "COIs Sent" (override: GVC_COI_ROOT_FOLDER).

        Returns {folder_id, folder_path}. Idempotent at every level.
        """
        root_name = (os.environ.get("GVC_COI_ROOT_FOLDER") or "COIs Sent").strip()
        folder_id = self.ensure_folder(root_name, self.shared_drive_id)
        folder_id = self.ensure_folder(str(year), folder_id)
        return {"folder_id": folder_id, "folder_path": f"{root_name}/{year}"}

    def ensure_change_order_folder(
        self,
        *,
        customer: str,
        project_label: str,
        project_type: str,
        year: int,
    ) -> dict:
        """
        Find-or-create the change-order folder chain (mirrors
        ensure_estimate_folder / ensure_invoice_folder):

            <Projects root>/<year>/<Residential|Commercial>/<customer>/
                <project_label>/Change Orders/

        Used by the CO flow when the JSON did NOT carry an explicit Drive folder
        URL (portal / Monday-sourced runs). Because it derives the chain from the
        SAME customer / project_label / project_type / year the estimate + invoice
        flows use, a CO lands in the SAME project folder the estimate created —
        just under its "Change Orders" leaf instead of "Estimate"/"Invoice". No
        pasted link required. `project_type` is "residential" or "commercial";
        the root folder defaults to "Projects" (override: GVC_PROJECTS_ROOT_FOLDER).

        Returns {folder_id, folder_path}. Idempotent at every level.
        """
        root_name = (os.environ.get("GVC_PROJECTS_ROOT_FOLDER") or "Projects").strip()
        type_name = "Commercial" if (project_type or "").lower().startswith("c") else "Residential"
        customer_slug = slug_for_path(customer)
        project_slug = slug_for_path(project_label)

        folder_id = self.ensure_folder(root_name, self.shared_drive_id)
        path_parts = [root_name]
        for name in (str(year), type_name, customer_slug, project_slug, "Change Orders"):
            folder_id = self.ensure_folder(name, folder_id)
            path_parts.append(name)
        return {"folder_id": folder_id, "folder_path": "/".join(path_parts)}
