"""
GFolder Link automation chain — pure / faked-I/O tests.
Self-running:  python tests/test_gfolder_chain.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.drive import drive_folder_url  # noqa: E402
from adapters.monday import jobcheck as mj  # noqa: E402
from shared.boards import PROJECTS_BOARD_ID, PROJECTS_GFOLDER_COL  # noqa: E402


FOLDER_URL = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def test_drive_folder_url():
    assert drive_folder_url("1AbCdEfGhIjKlMnOpQrStUvWxYz012345") == FOLDER_URL
    assert drive_folder_url("abc").endswith("/abc")


def test_photo_ready_status_ready():
    status = mj.photo_ready_status({
        "project_item_id": 111,
        "gfolder_url": FOLDER_URL,
        "folder_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "error": None,
    })
    assert status["photo_ready"] is True
    assert status["has_project_link"] is True
    assert status["has_gfolder"] is True
    assert status["photo_block_reason"] is None
    assert status["folder_id"] == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert status["gfolder_url"] == FOLDER_URL
    assert status["project_item_id"] == 111


def test_photo_ready_status_blocked():
    status = mj.photo_ready_status({
        "project_item_id": None,
        "gfolder_url": None,
        "folder_id": None,
        "error": "No linked Projects item.",
    })
    assert status["photo_ready"] is False
    assert status["has_project_link"] is False
    assert status["has_gfolder"] is False
    assert status["photo_block_reason"] == "No linked Projects item."

    status2 = mj.photo_ready_status({})
    assert status2["photo_ready"] is False
    assert status2["photo_block_reason"] == "Drive folder not linked."


class _FakeMC:
    """Minimal Monday client stand-in for set_projects_gfolder_if_empty."""

    def __init__(self, *, existing_url: str | None = None,
                 missing_item: bool = False):
        self.existing_url = existing_url
        self.missing_item = missing_item
        self.writes: list[dict] = []

    def _query(self, query, variables):
        q = " ".join((query or "").split())
        if "change_multiple_column_values" in q:
            self.writes.append(dict(variables))
            return {"change_multiple_column_values": {
                "id": str(variables.get("itemId"))}}
        # Read path
        if self.missing_item:
            return {"items": []}
        cv = {
            "id": PROJECTS_GFOLDER_COL,
            "text": "GFolder",
            "url": self.existing_url,
            "value": None,
        }
        if self.existing_url:
            cv["value"] = json.dumps({
                "url": self.existing_url, "text": "GFolder"})
        return {"items": [{"id": str(variables["ids"][0]),
                           "column_values": [cv]}]}


def test_set_projects_gfolder_fill_when_empty():
    mc = _FakeMC(existing_url=None)
    result = mj.set_projects_gfolder_if_empty(mc, 42, FOLDER_URL)
    assert result["ok"] is True
    assert result["written"] is True
    assert result["skipped"] is False
    assert result["gfolder_url"] == FOLDER_URL
    assert len(mc.writes) == 1
    written = mc.writes[0]
    assert written["boardId"] == str(PROJECTS_BOARD_ID)
    assert written["itemId"] == "42"
    payload = json.loads(written["values"])
    assert payload[PROJECTS_GFOLDER_COL]["url"] == FOLDER_URL
    assert payload[PROJECTS_GFOLDER_COL]["text"] == "GFolder"


def test_set_projects_gfolder_skip_when_set():
    mc = _FakeMC(existing_url=FOLDER_URL)
    other = "https://drive.google.com/drive/folders/OTHERFOLDERID999"
    result = mj.set_projects_gfolder_if_empty(mc, 42, other)
    assert result["ok"] is True
    assert result["written"] is False
    assert result["skipped"] is True
    assert result["gfolder_url"] == FOLDER_URL
    assert mc.writes == []


def test_set_projects_gfolder_bare_id_wrapped():
    mc = _FakeMC(existing_url=None)
    bare = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    result = mj.set_projects_gfolder_if_empty(mc, 7, bare)
    assert result["ok"] is True
    assert result["written"] is True
    assert result["gfolder_url"] == FOLDER_URL


def test_set_projects_gfolder_missing_item():
    mc = _FakeMC(missing_item=True)
    result = mj.set_projects_gfolder_if_empty(mc, 99, FOLDER_URL)
    assert result["ok"] is False
    assert result["written"] is False
    assert "not found" in (result.get("reason") or "").lower()
    assert mc.writes == []


if __name__ == "__main__":
    tests = [
        test_drive_folder_url,
        test_photo_ready_status_ready,
        test_photo_ready_status_blocked,
        test_set_projects_gfolder_fill_when_empty,
        test_set_projects_gfolder_skip_when_set,
        test_set_projects_gfolder_bare_id_wrapped,
        test_set_projects_gfolder_missing_item,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
