"""Focused tests for the Operations-board bulk title rename."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import backfill_job_rename_ops as ops  # noqa: E402


STANDARD = "9761 Gertrude Lane, Cincinnati, OH 45231 | Jent Construction"


class FakeMonday:
    def __init__(self, replies: list[dict]):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def _query(self, _query: str, variables: dict) -> dict:
        self.calls.append(variables)
        return self.replies.pop(0)


def _page(items: list[dict], cursor=None) -> dict:
    return {"boards": [{"items_page": {"cursor": cursor, "items": items}}]}


def test_list_project_names_pages_once_and_builds_id_index():
    mc = FakeMonday([
        _page([{"id": "11", "name": STANDARD}], cursor="next"),
        _page([{"id": "12", "name": "CO.1 - ignored"}]),
    ])

    projects, by_id = ops.list_project_names(mc)

    assert projects == [{"id": 11, "name": STANDARD}]
    assert by_id == {11: STANDARD}
    assert [call["cursor"] for call in mc.calls] == [None, "next"]


def test_list_operations_items_reads_project_relation_and_honors_limit():
    mc = FakeMonday([_page([
        {
            "id": "21",
            "name": "9761 Gertrude | Jent Construction",
            "column_values": [{
                "id": ops.OPS_PROJECT_LINK_COL,
                "linked_item_ids": ["11"],
                "linked_items": [],
            }],
        },
        {
            "id": "22",
            "name": "Second item",
            "column_values": [],
        },
    ])])

    rows = ops.list_operations_items(mc, limit=1)

    assert rows == [{
        "item_id": 21,
        "name": "9761 Gertrude | Jent Construction",
        "linked_project_ids": [11],
    }]
    assert mc.calls[0]["cols"] == [ops.OPS_PROJECT_LINK_COL]


def test_linked_operation_mirrors_current_standard_project_name():
    row = {
        "item_id": 21,
        "name": "9761 Gertrude | Jent Construction",
        "linked_project_ids": [11],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[{"id": 11, "name": STANDARD}],
        project_names={11: STANDARD},
    )

    assert plan["action"] == "rename"
    assert plan["new_name"] == STANDARD
    assert plan["source"] == "linked_project"


def test_unlinked_incomplete_operation_can_mirror_unique_standard_match():
    row = {
        "item_id": 22,
        "name": "9761 Gertrude | Jent Construction",
        "linked_project_ids": [],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[{"id": 11, "name": STANDARD}],
        project_names={11: STANDARD},
    )

    assert plan["action"] == "rename"
    assert plan["new_name"] == STANDARD
    assert plan["source"] == "matched_project"
    assert plan["match_score"] == 1.0


def test_unlinked_operation_skips_when_no_safe_standard_match():
    row = {
        "item_id": 23,
        "name": "Misc punch list",
        "linked_project_ids": [],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[{"id": 11, "name": STANDARD}],
        project_names={11: STANDARD},
    )

    assert plan["action"] == "skip_incomplete"
    assert plan["source"] == "ops_name"


def test_unlinked_operation_skips_ambiguous_project_matches():
    row = {
        "item_id": 23,
        "name": "9761 Gertrude | Jent Construction",
        "linked_project_ids": [],
    }
    projects = [
        {"id": 11, "name": STANDARD},
        {
            "id": 12,
            "name": (
                "9761 Gertrude Lane, Dayton, OH 45402 | Jent Construction"
            ),
        },
    ]

    plan = ops.plan_operation_item(
        row,
        projects=projects,
        project_names={project["id"]: project["name"] for project in projects},
    )

    assert plan["action"] == "skip_incomplete"
    assert plan["source"] == "ops_name"


def test_co_rows_are_skipped_even_when_linked():
    row = {
        "item_id": 24,
        "name": "CO.1 - 9761 Gertrude | Jent Construction",
        "linked_project_ids": [11],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[{"id": 11, "name": STANDARD}],
        project_names={11: STANDARD},
    )

    assert plan["action"] == "skip_co"


def test_apply_writes_only_rename_plans_and_sleeps(monkeypatch):
    writes: list[tuple[int, int, str]] = []
    sleeps: list[float] = []
    monkeypatch.setattr(
        ops,
        "rename_item_name",
        lambda _mc, board_id, item_id, new_name:
            writes.append((board_id, item_id, new_name)),
    )
    monkeypatch.setattr(ops.time, "sleep", sleeps.append)
    plans = [
        {
            "action": "rename",
            "ok": True,
            "item_id": 21,
            "new_name": STANDARD,
        },
        {
            "action": "rename",
            "ok": True,
            "item_id": 23,
            "new_name": (
                "100 Main Street, Cincinnati, OH 45202 | Example Builder"
            ),
        },
        {
            "action": "skip_incomplete",
            "ok": False,
            "item_id": 22,
            "new_name": "unchanged",
        },
    ]

    errors = ops.apply_plans(object(), plans)

    assert errors == []
    assert writes == [
        (ops.OPERATIONS_BOARD_ID, 21, STANDARD),
        (
            ops.OPERATIONS_BOARD_ID,
            23,
            "100 Main Street, Cincinnati, OH 45202 | Example Builder",
        ),
    ]
    assert sleeps == [0.2]


def test_dry_run_wins_when_both_flags_are_set(monkeypatch):
    seen: list[tuple[bool, int | None]] = []
    monkeypatch.setattr(
        ops,
        "run",
        lambda *, apply, limit: seen.append((apply, limit)) or 0,
    )

    assert ops.main(["--apply", "--dry-run", "--limit", "7"]) == 0
    assert seen == [(False, 7)]
