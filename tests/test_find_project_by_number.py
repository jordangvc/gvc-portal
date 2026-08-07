"""find_project_by_number — EST-/INV- needles + skip CO. rows."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.monday.client import COL_PROJECT_NUMBER, MondayClient  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f" FAIL {label}" + (f" — {detail}" if detail else ""))


def _item(item_id: int, name: str, project_number: str) -> dict:
    return {
        "id": str(item_id),
        "name": name,
        "column_values": [{"id": COL_PROJECT_NUMBER, "text": project_number}],
    }


class FakeMC(MondayClient):
    """Stub _query: maps compare_value → items that contain that substring."""

    def __init__(self, catalog: list[dict]):
        # Skip real session/token setup.
        self._catalog = catalog
        self.probes: list[str] = []

    def _query(self, query: str, variables: dict | None = None):  # noqa: ARG002
        val = str((variables or {}).get("val") or "")
        self.probes.append(val)
        hits = []
        for it in self._catalog:
            text = ""
            for cv in it.get("column_values") or []:
                if cv.get("id") == COL_PROJECT_NUMBER:
                    text = cv.get("text") or ""
                    break
            if val and val.lower() in text.lower():
                hits.append(it)
        return {"boards": [{"items_page": {"items": hits}}]}


PARENT = _item(100, "937 Madison Ridge | Steele", "PRO-2026-0804-007")
CO_ROW = _item(200, "CO.1 - 937 Madison Ridge | Steele", "PRO-2026-0804-007")
OTHER = _item(300, "Other Job", "PRO-2026-0804-099")


def test_est_paste_finds_pro_parent():
    mc = FakeMC([CO_ROW, PARENT, OTHER])  # CO first — must not win
    hit = mc.find_project_by_number("EST-2026-0804-007")
    check("EST- paste finds parent", hit == {"item_id": 100, "name": PARENT["name"]})
    check("EST- probes include bare + PRO-",
          "2026-0804-007" in mc.probes and "PRO-2026-0804-007" in mc.probes,
          str(mc.probes))


def test_inv_paste_finds_pro_parent():
    mc = FakeMC([PARENT])
    hit = mc.find_project_by_number("INV-2026-0804-007")
    check("INV- paste finds parent", hit and hit["item_id"] == 100)


def test_pro_exact():
    mc = FakeMC([PARENT, OTHER])
    hit = mc.find_project_by_number("PRO-2026-0804-007")
    check("PRO- exact", hit and hit["item_id"] == 100)


def test_bare_skips_co_row():
    mc = FakeMC([CO_ROW, PARENT])
    hit = mc.find_project_by_number("2026-0804-007")
    check("bare prefers parent over CO.", hit and hit["item_id"] == 100,
          str(hit))


def test_empty_none():
    mc = FakeMC([PARENT])
    check("empty → None", mc.find_project_by_number("  ") is None)
    check("no match → None",
          FakeMC([OTHER]).find_project_by_number("EST-2026-0804-007") is None)


def test_legacy_c_series_passthrough():
    legacy = _item(50, "Legacy", "C-005")
    mc = FakeMC([legacy])
    hit = mc.find_project_by_number("C-005")
    check("legacy C-005", hit and hit["item_id"] == 50)
    check("legacy probes once", mc.probes == ["C-005"], str(mc.probes))


if __name__ == "__main__":
    print("test_find_project_by_number")
    test_est_paste_finds_pro_parent()
    test_inv_paste_finds_pro_parent()
    test_pro_exact()
    test_bare_skips_co_row()
    test_empty_none()
    test_legacy_c_series_passthrough()
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
