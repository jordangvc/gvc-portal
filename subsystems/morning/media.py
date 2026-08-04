"""
Morning Brief media — Pictures-folder resolution (docs/MORNING_BRIEF_BUILD_SPEC.md
"Google Drive").
=========================================================================
Pure selection logic only. Drive I/O (listing a project folder's children,
downloading/uploading, and creating a Pictures folder when none exists)
lives in adapters/drive.py, mirroring the split subsystems/jobstart/photos.py
already uses for the same convention on the Job Start packet.

Spec: "Existing media convention is `GVC Job Site Media -> exact project
folder -> Pictures`. ... If multiple child folders are named `Pictures`,
automatically use the most recently modified one. ... Never create
employee- or date-named media folders."
"""
from __future__ import annotations

from typing import Optional

_PICTURES_NAME = "pictures"


def pick_pictures_folder(folders: list) -> Optional[dict]:
    """
    PURE. `folders` is a list of Drive folder metadata dicts (siblings
    inside the exact linked project folder), each with at least `name`;
    `modifiedTime` should be an RFC 3339 / ISO-8601 string when present (Drive's
    own format, which sorts correctly as plain text).

    Returns the entry named "Pictures" (case-insensitive, exact match — not
    "Site Pictures" or "Pics") with the most recent `modifiedTime`. None
    when no folder is named Pictures — in that case the caller creates one
    inside the exact linked project folder; this function never invents a
    name (no employee- or date-named folder, per spec).
    """
    candidates = [
        f for f in (folders or [])
        if str((f or {}).get("name") or "").strip().lower() == _PICTURES_NAME
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: str(f.get("modifiedTime") or ""))
