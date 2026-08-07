"""find_project_by_number — EST-/INV- needles + skip CO. rows + cache."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.monday import cache as monday_cache  # noqa: E402
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


PARENT = _item(100, "937 Madison Ridge | Steele", "PRO-2026-0804-007")
CO_ROW = _item(200, "CO.1 - 937 Madison Ridge | Steele", "PRO-2026-0804-007")
OTHER = _item(300, "Other Job", "PRO-2026-0804-099")


def _install_fake(monkey_catalog: list[dict], probes: list[str]):
    """Patch MondayClient so parallel needle probes hit our catalog."""

    class FakeLocal:
        def _query(self, query, variables=None):  # noqa: ARG002
            val = str((variables or {}).get("val") or "")
            probes.append(val)
            hits = []
            for it in monkey_catalog:
                text = ""
                for cv in it.get("column_values") or []:
                    if cv.get("id") == COL_PROJECT_NUMBER:
                        text = cv.get("text") or ""
                        break
                if val and val.lower() in text.lower():
                    hits.append(it)
            return {"boards": [{"items_page": {"items": hits}}]}

    return FakeLocal


def _run_with_catalog(catalog: list[dict], query: str):
    monday_cache.clear()
    probes: list[str] = []
    FakeLocal = _install_fake(catalog, probes)
    # Minimal host that has find_project_by_number; probes go through patched client.
    host = object.__new__(MondayClient)
    import adapters.monday.client as client_mod
    real = client_mod.MondayClient
    client_mod.MondayClient = lambda **_kw: FakeLocal()  # type: ignore
    try:
        hit = MondayClient.find_project_by_number(host, query)
    finally:
        client_mod.MondayClient = real
    return hit, probes


def test_est_paste_finds_pro_parent():
    hit, probes = _run_with_catalog([CO_ROW, PARENT, OTHER], "EST-2026-0804-007")
    check("EST- paste finds parent", hit == {"item_id": 100, "name": PARENT["name"]})
    check("EST- probes include bare + PRO-",
          "2026-0804-007" in probes and "PRO-2026-0804-007" in probes,
          str(probes))


def test_inv_paste_finds_pro_parent():
    hit, _ = _run_with_catalog([PARENT], "INV-2026-0804-007")
    check("INV- paste finds parent", hit and hit["item_id"] == 100)


def test_pro_exact():
    hit, _ = _run_with_catalog([PARENT, OTHER], "PRO-2026-0804-007")
    check("PRO- exact", hit and hit["item_id"] == 100)


def test_bare_skips_co_row():
    hit, _ = _run_with_catalog([CO_ROW, PARENT], "2026-0804-007")
    check("bare prefers parent over CO.", hit and hit["item_id"] == 100, str(hit))


def test_empty_none():
    hit, _ = _run_with_catalog([PARENT], "  ")
    check("empty → None", hit is None)
    hit2, _ = _run_with_catalog([OTHER], "EST-2026-0804-007")
    check("no match → None", hit2 is None)


def test_legacy_c_series_passthrough():
    legacy = _item(50, "Legacy", "C-005")
    hit, probes = _run_with_catalog([legacy], "C-005")
    check("legacy C-005", hit and hit["item_id"] == 50)
    check("legacy probes once", probes == ["C-005"], str(probes))


def test_cache_hits_second_call():
    monday_cache.clear()
    probes: list[str] = []
    FakeLocal = _install_fake([PARENT], probes)
    import adapters.monday.client as client_mod
    real = client_mod.MondayClient
    client_mod.MondayClient = lambda **_kw: FakeLocal()  # type: ignore
    host = object.__new__(MondayClient)
    try:
        a = MondayClient.find_project_by_number(host, "PRO-2026-0804-007")
        n1 = len(probes)
        b = MondayClient.find_project_by_number(host, "EST-2026-0804-007")
        n2 = len(probes)
    finally:
        client_mod.MondayClient = real
    check("cache returns same item", a == b and a and a["item_id"] == 100)
    check("second call uses cache (no new probes)", n2 == n1, f"n1={n1} n2={n2} probes={probes}")


if __name__ == "__main__":
    print("test_find_project_by_number")
    test_est_paste_finds_pro_parent()
    test_inv_paste_finds_pro_parent()
    test_pro_exact()
    test_bare_skips_co_row()
    test_empty_none()
    test_legacy_c_series_passthrough()
    test_cache_hits_second_call()
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
