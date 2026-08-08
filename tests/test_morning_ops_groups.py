"""Morning Ops fetch scopes to active groups (skips Completed Tasks walk).

Run: .venv/bin/pytest tests/test_morning_ops_groups.py -q
  or: python tests/test_morning_ops_groups.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import boards  # noqa: E402
from adapters.monday import morning as mm  # noqa: E402


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


class _FakeMC:
    """Records queries; serves groups + per-group items_page."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.group_pages = {
            "topics": {"cursor": None, "items": [
                {"id": "1", "name": "A | B", "updated_at": "2026-08-08T12:00:00Z",
                 "group": {"id": "topics", "title": "In-Progress"},
                 "column_values": []},
            ]},
            "group_mm3khfvc": {"cursor": None, "items": [
                {"id": "2", "name": "C | D", "updated_at": "2026-08-08T12:00:00Z",
                 "group": {"id": "group_mm3khfvc", "title": "Upcoming"},
                 "column_values": []},
            ]},
        }

    def _query(self, query: str, variables=None):
        variables = variables or {}
        q = " ".join(query.split())
        self.calls.append(q)
        if "groups { id }" in q and "items_page" not in q:
            return {"boards": [{"groups": [
                {"id": "topics"},
                {"id": "group_mm3khfvc"},
                {"id": "new_group"},  # Completed — must be skipped
                {"id": "group_mm3zq4q2"},  # Ready to Invoice — skipped
            ]}]}
        if "groups(ids:" in q:
            gids = variables.get("groupIds") or []
            gid = gids[0] if gids else None
            page = self.group_pages.get(gid) or {"cursor": None, "items": []}
            return {"boards": [{"groups": [{"id": gid, "items_page": page}]}]}
        if "items_page(limit: 200, cursor: $cursor)" in q and "groups(ids" not in q:
            raise AssertionError("full-board items_page should not run on happy path")
        raise AssertionError(f"unexpected query: {q[:160]}")


def test_active_group_ids_skip_completed() -> None:
    mc = _FakeMC()
    ids = mm._list_active_ops_group_ids(mc)
    check("keeps topics", "topics" in ids)
    check("keeps upcoming", "group_mm3khfvc" in ids)
    check("drops completed", "new_group" not in ids)
    check("drops ready", "group_mm3zq4q2" not in ids)
    check("skip set honored", set(ids).isdisjoint(boards.MORNING_SKIP_GROUP_IDS))


def test_fetch_ops_uses_group_scope_not_full_board() -> None:
    """_fetch_ops_items_uncached pages active groups only."""
    fake = _FakeMC()

    class _StubClient:
        def _query(self, query, variables=None):
            return fake._query(query, variables)

    import adapters.monday.morning as morning_mod
    orig = morning_mod.MondayClient
    morning_mod.MondayClient = _StubClient
    try:
        rows = mm._fetch_ops_items_uncached(fake)
    finally:
        morning_mod.MondayClient = orig

    ids = {str(r["item_id"]) for r in rows}
    check("got both active items", ids == {"1", "2"})
    check("listed groups", any("groups { id }" in c for c in fake.calls))
    check("used groups(ids", any("groups(ids:" in c for c in fake.calls))
    check("no accidental full-board page",
          not any(
              "items_page(limit: 200, cursor: $cursor)" in c and "groups(ids:" not in c
              for c in fake.calls
          ))


def test_skip_constants_match_jobcheck() -> None:
    check("morning skip == jobcheck skip",
          boards.MORNING_SKIP_GROUP_IDS == boards.JOBCHECK_SKIP_GROUP_IDS)
    check("completed id present", "new_group" in boards.MORNING_SKIP_GROUP_IDS)


def main() -> None:
    print("test_morning_ops_groups")
    test_active_group_ids_skip_completed()
    test_fetch_ops_uses_group_scope_not_full_board()
    test_skip_constants_match_jobcheck()
    print("ALL OK")


if __name__ == "__main__":
    main()
