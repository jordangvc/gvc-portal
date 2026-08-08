"""Estimate + Change Order drafts must persist revise mode across resume.

Known gap (CLAUDE estimate-revision KNOWN GAPS): draft autosave resume did
not restore the revise checkbox — a resumed revision could finalize as NEW.

Run: .venv/bin/pytest tests/test_revise_draft_persist.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EST = (ROOT / "web" / "estimate.html").read_text(encoding="utf-8")
CO = (ROOT / "web" / "change-order.html").read_text(encoding="utf-8")


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_estimate_revise_persists() -> None:
    save = EST.split("async function doAutosave()")[1].split(
        "async function refreshDrafts()")[0]
    resume = EST.split("function resumeDraft(id)")[1].split(
        "function looksLikeMondayRef")[0]
    check("estimate stores _ui.revise", "_ui: { revise: revise }" in save)
    check("estimate record.revise", "revise: revise" in save)
    check("estimate resume restores checkbox",
          '$("#revise-flag").checked' in resume and "wantRevise" in resume)
    check("estimate strips _ui on resume", "delete p._ui" in resume)
    check("estimate revise badge in list", ">revise</span>" in EST)


def test_co_revise_persists() -> None:
    save = CO.split("async function doAutosave()")[1].split(
        "async function refreshDrafts()")[0]
    resume = CO.split("function resumeDraft(id)")[1].split(
        "async function deleteDraft")[0]
    check("CO stores _ui.revise", "_ui: { revise: revise }" in save)
    check("CO resume restores checkbox",
          '$("#revise-flag").checked' in resume and "wantRevise" in resume)
    check("CO strips _ui on resume", "delete p._ui" in resume)
    check("CO revise badge in list", ">revise</span>" in CO)


if __name__ == "__main__":
    test_estimate_revise_persists()
    test_co_revise_persists()
    print("ALL OK")
