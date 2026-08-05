"""Estimate → Jake plan-folder PDF slice.

Pure tests for normalize + soft-fail upload helper (no Drive / no Monday).
Runs under pytest OR directly: `python tests/test_estimate_plan_folder.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subsystems.estimate.plan_folder import (  # noqa: E402
    normalize_plan_folder_number,
    upload_pdf_to_jake_plan_folder,
)
from adapters.monday.estimate import (  # noqa: E402
    build_prefill,
    COL_PLAN_FOLDER,
    COL_ESTIMATE_DATE,
    COL_EXPIRY_DATE,
)


# ---------------------------------------------------------------- normalize
def test_normalize_plan_folder_number():
    assert normalize_plan_folder_number("341") == "341"
    assert normalize_plan_folder_number("  097 - Foo ") == "097"
    assert normalize_plan_folder_number("") is None
    assert normalize_plan_folder_number(None) is None
    assert normalize_plan_folder_number("Obara Office") is None


# ---------------------------------------------------------------- prefill
def test_build_prefill_includes_plan_folder_number():
    cols = {
        COL_PLAN_FOLDER: {"text": "341 - Obara", "display": "", "linked": []},
        "status": {"text": "Commercial", "display": "", "linked": []},
    }
    # project type label map needs Residential/Commercial-ish; commercial via status
    pf = build_prefill(99, "123 Main | Acme", cols)
    assert pf["job"]["monday_item_id"] == 99
    assert pf["job"]["plan_folder_number"] == "341"


def test_build_prefill_skips_blank_plan_folder():
    cols = {COL_PLAN_FOLDER: {"text": "", "display": "", "linked": []}}
    pf = build_prefill(1, "Job", cols)
    assert "plan_folder_number" not in pf["job"]


def test_build_prefill_includes_estimate_and_expiry_dates():
    cols = {
        COL_ESTIMATE_DATE: {"text": "2026-07-15", "display": "", "linked": []},
        COL_EXPIRY_DATE: {"text": "2026-08-14T00:00:00Z", "display": "", "linked": []},
    }
    pf = build_prefill(42, "9761 Gertrude | Acme", cols)
    assert pf["estimate"]["date"] == "2026-07-15"
    assert pf["estimate"]["expiry_date"] == "2026-08-14"


def test_build_prefill_omits_blank_dates():
    cols = {
        COL_ESTIMATE_DATE: {"text": "", "display": "", "linked": []},
        COL_EXPIRY_DATE: {"text": "  ", "display": "", "linked": []},
    }
    pf = build_prefill(1, "Job", cols)
    assert pf["estimate"] == {}
    assert "date" not in pf["estimate"]
    assert "expiry_date" not in pf["estimate"]


# ---------------------------------------------------------------- upload helper
class _FakeUploader:
    def __init__(self, found, upload_result=None, upload_exc=None):
        self.found = found
        self.upload_result = upload_result or {
            "file_id": "f1",
            "web_view_link": "https://drive.example/f1",
            "filename": "est.pdf",
        }
        self.upload_exc = upload_exc
        self.upload_calls = []

    def find_numbered_child_folder(self, parent_id, number):
        self.last_find = (parent_id, number)
        return self.found

    def upload_pdf_to_folder(self, pdf_path, *, folder_id, filename=None):
        self.upload_calls.append(
            {"pdf_path": Path(pdf_path), "folder_id": folder_id, "filename": filename}
        )
        if self.upload_exc:
            raise self.upload_exc
        return self.upload_result


def test_upload_happy_path():
    up = _FakeUploader(
        {"ok": True, "folder_id": "folder-341", "name": "341 - Obara - Sent"}
    )
    pdf = Path("/tmp/estimate.pdf")
    report = upload_pdf_to_jake_plan_folder(
        up,
        pdf_path=pdf,
        filename="2026-0804-001 - Obara.pdf",
        plan_number="341",
        root_id="root-jake",
    )
    assert report["plan_folder_ok"] is True
    assert report["plan_folder_id"] == "folder-341"
    assert report["plan_folder_name"] == "341 - Obara - Sent"
    assert report["plan_folder_pdf_url"] == "https://drive.example/f1"
    assert up.last_find == ("root-jake", "341")
    assert up.upload_calls[0]["folder_id"] == "folder-341"
    assert up.upload_calls[0]["filename"] == "2026-0804-001 - Obara.pdf"


def test_upload_soft_fail_missing_number():
    up = _FakeUploader({"ok": True, "folder_id": "x", "name": "x"})
    report = upload_pdf_to_jake_plan_folder(
        up,
        pdf_path=Path("/tmp/e.pdf"),
        filename="e.pdf",
        plan_number="",
        root_id="root",
    )
    assert report["plan_folder_ok"] is False
    assert report["plan_folder_reason"] == "missing"
    assert up.upload_calls == []


def test_upload_soft_fail_not_found():
    up = _FakeUploader(
        {"ok": False, "reason": "not_found", "detail": "No plan folder starting with 999 was found."}
    )
    report = upload_pdf_to_jake_plan_folder(
        up,
        pdf_path=Path("/tmp/e.pdf"),
        filename="e.pdf",
        plan_number="999",
        root_id="root",
    )
    assert report["plan_folder_ok"] is False
    assert report["plan_folder_reason"] == "not_found"
    assert "999" in report["plan_folder_status"]
    assert up.upload_calls == []


def test_upload_soft_fail_ambiguous():
    up = _FakeUploader(
        {
            "ok": False,
            "reason": "ambiguous",
            "detail": "2 plan folders start with 12 — the portal won't guess which one.",
            "candidates": ["12 - A", "12 - B"],
        }
    )
    report = upload_pdf_to_jake_plan_folder(
        up,
        pdf_path=Path("/tmp/e.pdf"),
        filename="e.pdf",
        plan_number="12",
        root_id="root",
    )
    assert report["plan_folder_ok"] is False
    assert report["plan_folder_reason"] == "ambiguous"
    assert report["plan_folder_candidates"] == ["12 - A", "12 - B"]
    assert up.upload_calls == []


def test_upload_soft_fail_no_access():
    up = _FakeUploader(
        {
            "ok": False,
            "reason": "no_access",
            "detail": "The plan folder isn't visible to portal@….gserviceaccount.com.",
        }
    )
    report = upload_pdf_to_jake_plan_folder(
        up,
        pdf_path=Path("/tmp/e.pdf"),
        filename="e.pdf",
        plan_number="341",
        root_id="root",
    )
    assert report["plan_folder_ok"] is False
    assert report["plan_folder_reason"] == "no_access"
    assert up.upload_calls == []


def test_upload_soft_fail_upload_error():
    up = _FakeUploader(
        {"ok": True, "folder_id": "folder-341", "name": "341 - Obara"},
        upload_exc=RuntimeError("boom"),
    )
    report = upload_pdf_to_jake_plan_folder(
        up,
        pdf_path=Path("/tmp/e.pdf"),
        filename="e.pdf",
        plan_number="341",
        root_id="root",
    )
    assert report["plan_folder_ok"] is False
    assert report["plan_folder_reason"] == "upload_error"
    assert "boom" in report["plan_folder_status"]


if __name__ == "__main__":
    # Lightweight runner (no pytest required in the image).
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK  {name}")
    print("all passed")
