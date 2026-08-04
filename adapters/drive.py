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
import sys
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




def pick_pictures_folder(children: list) -> Optional[dict]:
    """
    PURE. Among Drive child folders named exactly "Pictures" (case-insensitive),
    return the most recently modified one: {id, name, modifiedTime?}.

    Spec (docs/MORNING_BRIEF_BUILD_SPEC.md): if multiple Pictures folders exist,
    automatically use the most recently modified. Returns None when none match.
    """
    pics = []
    for child in children or []:
        if (child.get("mimeType") or "") != "application/vnd.google-apps.folder":
            continue
        if (child.get("name") or "").strip().lower() != "pictures":
            continue
        pics.append(child)
    if not pics:
        return None
    pics.sort(key=lambda c: c.get("modifiedTime") or "", reverse=True)
    return pics[0]

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


# Jake's "01 - Completed Plans" folder — the root of the per-job folder tree the
# Job Start packet reads (Jordan supplied it 2026-07-29). Env-overridable so a
# reorganised Drive is a config change, not a deploy.
DEFAULT_PLANS_FOLDER_ID = "1X1vuutnTuCN0hxTZSANmm3QC6SQ41Gc0"

# Names of subfolders inside a job folder that hold site photos. The nightly
# Monday->Drive sync (and takeoff-app backups) file photos into the job folder;
# these hints let the packet also reach a dedicated photo subfolder if one is
# used. Env-overridable (comma-separated) so a new convention is config, not code.
_PHOTO_SUBFOLDER_HINTS = tuple(
    h.strip().lower() for h in
    (os.environ.get("GVC_JOBSTART_PHOTO_SUBFOLDERS")
     or "photo,picture,image,site photo,job photo").split(",")
    if h.strip()
)

# Words that appear in almost every GVC job name and therefore identify
# nothing. Matching on these is how you prefill one job from another job's
# scope review, so they're stripped before scoring.
_STOPWORDS = frozenset({
    "the", "and", "inc", "llc", "co", "company", "contractors", "construction",
    "scope", "review", "job", "project", "new", "house", "home", "residence",
    "remodel", "addition", "commercial", "residential", "building", "bldg",
    "st", "street", "rd", "road", "ave", "avenue", "dr", "drive", "ln", "lane",
    "ct", "court", "way", "blvd", "suite", "ste", "unit", "lot", "oh", "ohio",
    "in", "indiana", "ky", "kentucky", "usa", "cincinnati", "pdf", "copy",
})


def _match_tokens(*parts) -> set:
    """
    PURE. Text → the set of distinctive lowercase tokens used to match a job to
    its scope review. Street numbers and builder names survive; boilerplate and
    geography do not. Flattens strings and lists of strings.
    """
    words: list = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple, set)):
            words.extend(str(p) for p in part if p)
        else:
            words.append(str(part))
    text = " ".join(words).lower()
    raw = re.findall(r"[a-z0-9]+", text)
    return {t for t in raw
            if t not in _STOPWORDS and (len(t) > 2 or t.isdigit())}


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


    def resolve_project_pictures_folder(self, project_folder_id: str) -> dict:
        """
        Resolve (or create) the Pictures subfolder under a project Drive folder.

        Convention: GFolder Link → exact project folder → Pictures
        (docs/MORNING_BRIEF_BUILD_SPEC.md). Never creates employee/date-named
        media folders. If multiple Pictures children exist, uses the most
        recently modified. If none exist, creates one.

        Returns {folder_id, created, web_view_link?}.
        """
        if not project_folder_id:
            raise ValueError("project_folder_id is required")
        children = self._list_children(project_folder_id, folders_only=True)
        existing = pick_pictures_folder(children)
        if existing:
            return {
                "folder_id": existing["id"],
                "created": False,
                "web_view_link": existing.get("webViewLink"),
            }
        folder_id = self.ensure_folder("Pictures", project_folder_id)
        return {"folder_id": folder_id, "created": True, "web_view_link": None}

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

    def find_numbered_child_folder(self, parent_id: str, number: str) -> dict:
        """
        Find the child folder of `parent_id` whose name starts with `number` —
        Jake's plan folders are named "341 - Obara Office Renovation - Sent",
        so the leading number is the key the portal matches on.

        Returns one of:
          {"ok": True,  "folder_id", "name"}
          {"ok": False, "reason": "no_access" | "not_found" | "ambiguous",
           "detail": <human sentence>, "candidates": [names…]}

        ⚠ Why `no_access` is checked FIRST and separately: Drive answers a
        child-listing query on a folder the caller can't see with an EMPTY LIST,
        not an error — indistinguishable from "that number isn't in there".
        Verified live 2026-07-29: the service account got 0 children for Jake's
        folder and a 404 on the folder itself. Reporting "folder 341 not found"
        when the truth is "we were never allowed to look" would send someone
        hunting a filing mistake that doesn't exist.
        """
        num = (number or "").strip()
        if not num:
            return {"ok": False, "reason": "not_found",
                    "detail": "No plan-folder number was supplied."}

        # 1) Prove we can see the parent at all.
        try:
            self.service.files().get(
                fileId=parent_id, fields="id, name", supportsAllDrives=True,
            ).execute()
        except Exception as e:  # noqa: BLE001 — 404/403 both mean "not visible"
            return {"ok": False, "reason": "no_access",
                    "detail": (f"The plan folder isn't visible to "
                               f"{self._sa_email() or 'the portal service account'} "
                               f"({type(e).__name__}). Share it as Editor/Content "
                               f"manager, then re-run."),
                    }

        # 2) List children and match on the leading number. Compared as ints so
        #    "097" and "97" are the same job, and "34" never matches "341".
        try:
            target = int(num)
        except ValueError:
            return {"ok": False, "reason": "not_found",
                    "detail": f"{num!r} isn't a plan-folder number."}

        matches, page_token = [], None
        while True:
            resp = self.service.files().list(
                q=(f"'{parent_id}' in parents and trashed = false and "
                   f"mimeType = 'application/vnd.google-apps.folder'"),
                fields="nextPageToken, files(id, name)", pageSize=200,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
                pageToken=page_token,
            ).execute()
            for f in resp.get("files") or []:
                lead = re.match(r"\s*(\d{1,5})", f.get("name") or "")
                if lead and int(lead.group(1)) == target:
                    matches.append(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        if not matches:
            return {"ok": False, "reason": "not_found",
                    "detail": f"No plan folder starting with {target} was found."}
        if len(matches) > 1:
            return {"ok": False, "reason": "ambiguous",
                    "detail": (f"{len(matches)} plan folders start with {target} — "
                               f"the portal won't guess which one."),
                    "candidates": [m["name"] for m in matches]}
        return {"ok": True, "folder_id": matches[0]["id"], "name": matches[0]["name"]}

    def _sa_email(self) -> Optional[str]:
        """The service-account address, read from the key file, so a sharing
        error can name exactly who to grant access to. None if unreadable."""
        try:
            return json.loads(Path(self.creds_path).read_text()).get("client_email")
        except Exception:  # noqa: BLE001 — an error message must not itself fail
            return None

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

    def _list_children(self, parent_id: str, *, folders_only: bool = False,
                       page_size: int = 200) -> list:
        """Children of a Drive folder. Read-only."""
        q = f"'{parent_id}' in parents and trashed = false"
        if folders_only:
            q += " and mimeType = 'application/vnd.google-apps.folder'"
        out: list = []
        token = None
        while True:
            result = (
                self.service.files()
                .list(q=q,
                      fields="nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)",
                      orderBy="modifiedTime desc",
                      corpora="allDrives", includeItemsFromAllDrives=True,
                      supportsAllDrives=True, pageSize=page_size,
                      pageToken=token)
                .execute()
            )
            out.extend(result.get("files", []))
            token = result.get("nextPageToken")
            if not token or len(out) >= 600:
                break
        return out

    def find_job_documents(self, *, job_hint: str,
                           extra_hints: Optional[list] = None) -> dict:
        """
        Find a job's folder inside Jake's "01 - Completed Plans" tree and pull the
        documents the Job Start packet needs out of it.

        Structure verified live 2026-07-29 against folder 1X1vuutn… —
        `01 - Completed Plans/{seq} - {GC} - {project} - {status}/` containing e.g.
            Jent-Bryant Res - Scope Review.pdf        ← the packet's primary source
            Jent - Bryant Res - Takeoff Totals.xlsx   ← the takeoff link, free
            Jent - Bryant Res - Planswift Takeoffs.pdf
            Plan - 2026-01 PLAN REVISIONS.pdf

        FOLDER-FIRST, not a global filename search: scoping to the known tree is
        both faster and far safer, because a global "Scope Review" search can
        match some other job's document. Folder matching uses
        naming.best_folder(), which is built for these `### - GC - project -
        status` names — the leading number is a job counter, not a street number,
        and containment beats Jaccard because folder names legitimately omit the
        address. An ambiguous match returns nothing rather than the wrong job.

        Returns {folder, scope_review, takeoff, detail} — any of which may be None.
        """
        from subsystems.jobstart import naming

        root = (os.environ.get("GVC_JAKE_PLANS_FOLDER_ID")
                or DEFAULT_PLANS_FOLDER_ID)
        out: dict = {"folder": None, "scope_review": None, "takeoff": None,
                     "detail": None}
        if not root:
            out["detail"] = "No completed-plans folder configured."
            return out

        folders = self._list_children(root, folders_only=True)
        if not folders:
            out["detail"] = ("Couldn't read the completed-plans folder — the "
                             "service account may not have access to it.")
            return out

        hint = " ".join(str(h) for h in
                        [job_hint, *(extra_hints or [])] if h)
        hit = naming.best_folder(hint, [{"id": f["id"], "name": f["name"]}
                                        for f in folders])
        if not hit:
            out["detail"] = (f"No job folder in Completed Plans matched this job "
                             f"({len(folders)} folders checked).")
            return out
        out["folder"] = hit

        files = [f for f in self._list_children(hit["id"])
                 if f.get("mimeType") != "application/vnd.google-apps.folder"]

        def _pick(*needles) -> Optional[dict]:
            for f in files:
                low = (f.get("name") or "").lower()
                if all(n in low for n in needles):
                    return f
            return None

        out["scope_review"] = _pick("scope", "review")
        # Prefer the totals workbook; fall back to the PlanSwift export.
        out["takeoff"] = (_pick("takeoff", "total") or _pick("takeoff")
                          or _pick("planswift"))
        if not out["scope_review"]:
            out["detail"] = (f"Found the job folder \"{hit['name']}\" but no "
                             f"Scope Review file in it.")
        return out

    def find_scope_review(self, *, job_hint: str,
                          extra_hints: Optional[list] = None) -> Optional[dict]:
        """Back-compat shim: just the scope-review file from find_job_documents."""
        found = self.find_job_documents(job_hint=job_hint, extra_hints=extra_hints)
        sr = found.get("scope_review")
        return {**sr, "score": (found.get("folder") or {}).get("score", 0)} if sr else None

    def read_document_text(self, file_id: str, mime_type: str) -> str:
        """
        Plain text of a Drive document, for parsing. Google Docs export as
        text/plain; PDFs come back as bytes and are handed to the PDF text
        extractor already in the image (pypdf, shipped for the COI stamper).
        Returns "" on anything unreadable — a scope review we can't read must
        degrade to Bid Board prefill, never raise into the handoff page.
        """
        import io as _io

        try:
            if mime_type == "application/vnd.google-apps.document":
                data = (self.service.files()
                        .export(fileId=file_id, mimeType="text/plain")
                        .execute())
                return data.decode("utf-8") if isinstance(data, bytes) else str(data)

            from googleapiclient.http import MediaIoBaseDownload

            request = self.service.files().get_media(fileId=file_id,
                                                     supportsAllDrives=True)
            buf = _io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            raw = buf.getvalue()

            if mime_type == "application/pdf":
                from pypdf import PdfReader

                reader = PdfReader(_io.BytesIO(raw))
                return "\n".join((p.extract_text() or "") for p in reader.pages)
            return raw.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 — unreadable ⇒ fall back to Monday
            print(f"[drive] scope review unreadable ({file_id}): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            return ""

    def download_file_bytes(self, file_id: str) -> bytes:
        """Raw bytes of a Drive file by ID (read-only, works across shared drives).
        Used to pull site photos for the Job Start packet."""
        import io as _io

        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(fileId=file_id,
                                                 supportsAllDrives=True)
        buf = _io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    def find_job_photo_files(self, *, job_hint: str,
                             extra_hints: Optional[list] = None,
                             folder_id: Optional[str] = None) -> dict:
        """
        List the site-photo files for a job. Photos land in the job's Drive
        folder via the nightly Monday->Drive sync (and, soon, takeoff-app
        backups), so this reuses the same folder discovery as the scope review:
        it locates the job folder, then collects image files sitting directly in
        it PLUS any inside a photo-named subfolder (Photos / Pictures / Images /
        Site Photos …). Pass `folder_id` to skip discovery and read one folder.

        Returns {files, folder, detail}. `files` is the raw Drive metadata
        (id, name, mimeType, webViewLink); selection/ordering/capping is the pure
        subsystems.jobstart.photos layer's job. Read-only; never raises — a Drive
        problem yields an empty list so the packet still renders.
        """
        from subsystems.jobstart import photos as _photos

        out: dict = {"files": [], "folder": None, "detail": None}
        try:
            if folder_id:
                folder = {"id": folder_id, "name": None}
            else:
                docs = self.find_job_documents(job_hint=job_hint,
                                               extra_hints=extra_hints)
                folder = docs.get("folder")
                if not folder:
                    out["detail"] = docs.get("detail") or "No job folder matched."
                    return out
            out["folder"] = folder

            children = self._list_children(folder["id"])
            images = [c for c in children if _photos.is_image(c)]

            hints = _PHOTO_SUBFOLDER_HINTS
            for sub in children:
                if sub.get("mimeType") != "application/vnd.google-apps.folder":
                    continue
                if any(h in (sub.get("name") or "").lower() for h in hints):
                    images.extend(c for c in self._list_children(sub["id"])
                                  if _photos.is_image(c))
            out["files"] = images
            if not images:
                out["detail"] = ("Found the job folder but no photos in it "
                                 "(or a Photos subfolder).")
            return out
        except Exception as e:  # noqa: BLE001 — photos never block the packet
            print(f"[drive] job photo lookup failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            out["detail"] = f"Couldn't read photos from Drive ({type(e).__name__})."
            return out

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

    def ensure_handoff_folder(
        self,
        *,
        customer: str,
        project_label: str,
        project_type: str,
        year: int,
    ) -> dict:
        """
        Find-or-create the Sales → Operations handoff folder chain (mirrors
        ensure_estimate_folder / ensure_invoice_folder, added 2026-07-29):

            <Projects root>/<year>/<Residential|Commercial>/<customer>/
                <project_label>/Handoff/

        Derived from the SAME customer / project_label / project_type / year the
        estimate and invoice flows use, so the accepted handoff packet lands in
        the same project folder as the estimate that won the job — one folder per
        job, no pasted link required.

        Returns {folder_id, folder_path}. Idempotent at every level.
        """
        root_name = (os.environ.get("GVC_PROJECTS_ROOT_FOLDER") or "Projects").strip()
        type_name = "Commercial" if (project_type or "").lower().startswith("c") else "Residential"
        customer_slug = slug_for_path(customer)
        project_slug = slug_for_path(project_label)

        folder_id = self.ensure_folder(root_name, self.shared_drive_id)
        path_parts = [root_name]
        for name in (str(year), type_name, customer_slug, project_slug, "Handoff"):
            folder_id = self.ensure_folder(name, folder_id)
            path_parts.append(name)
        return {"folder_id": folder_id, "folder_path": "/".join(path_parts)}

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
