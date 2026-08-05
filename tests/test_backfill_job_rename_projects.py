"""Focused tests for the Projects-board bulk rename script."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday.client import COL_BUILDER  # noqa: E402
from scripts import backfill_job_rename_projects as script  # noqa: E402
from shared.boards import (  # noqa: E402
    JOBSTART_P_COL_CUSTOMER,
    JOBSTART_P_COL_LOCATION,
    PROJECTS_BOARD_ID,
    PROJECTS_GFOLDER_COL,
)


def _column(column_id: str, text: str, **extra) -> dict:
    return {"id": column_id, "text": text, **extra}


def test_plan_project_item_uses_projects_columns_and_keeps_gfolder():
    item = {
        "id": "123",
        "name": "9195 Silva",
        "column_values": [
            _column(
                JOBSTART_P_COL_LOCATION,
                "9195 Silva Drive, Cincinnati, OH 45241",
            ),
            _column(COL_BUILDER, "Willow Creek"),
            _column(JOBSTART_P_COL_CUSTOMER, "Fallback Customer"),
            _column(
                PROJECTS_GFOLDER_COL,
                "GFolder",
                url="https://drive.google.com/drive/folders/folder123",
            ),
        ],
    }

    plan = script.plan_project_item(item)

    assert plan["action"] == "rename"
    assert plan["item_id"] == 123
    assert plan["board"] == "projects"
    assert plan["new_name"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek"
    )
    assert plan["gfolder_url"] == (
        "https://drive.google.com/drive/folders/folder123"
    )


def test_plan_project_item_uses_linked_customer_when_builder_is_empty():
    item = {
        "id": "456",
        "name": "3776 Susanna",
        "column_values": [
            _column(
                JOBSTART_P_COL_LOCATION,
                "3776 Susanna, Lawrenceburg, IN 47025",
            ),
            _column(COL_BUILDER, ""),
            _column(JOBSTART_P_COL_CUSTOMER, "Martin"),
        ],
    }

    plan = script.plan_project_item(item)

    assert plan["action"] == "rename"
    assert plan["new_name"] == (
        "3776 Susanna, Lawrenceburg, IN 47025 | Martin"
    )


def test_plan_project_item_leaves_co_decision_to_shared_planner():
    plan = script.plan_project_item(
        {
            "id": "789",
            "name": "CO.1 - 9195 Silva | Willow Creek",
            "column_values": [],
        }
    )

    assert plan["action"] == "skip_co"


def test_dry_run_wins_when_both_flags_are_present():
    args = argparse.Namespace(apply=True, dry_run=True)
    assert script.should_apply(args) is False


def test_apply_rename_plans_writes_only_rename_actions_and_sleeps_between():
    plans = [
        {
            "action": "rename",
            "ok": True,
            "item_id": 1,
            "old_name": "Old One",
            "new_name": "New One",
        },
        {
            "action": "skip_incomplete",
            "ok": False,
            "item_id": 2,
            "old_name": "Incomplete",
            "new_name": "Incomplete",
        },
        {
            "action": "rename",
            "ok": True,
            "item_id": 3,
            "old_name": "Old Three",
            "new_name": "New Three",
        },
    ]
    calls = []
    sleeps = []

    def fake_rename(mc, board_id, item_id, new_name):
        calls.append((mc, board_id, item_id, new_name))

    written, errors = script.apply_rename_plans(
        "client",
        plans,
        rename_fn=fake_rename,
        sleep_fn=sleeps.append,
    )

    assert written == 2
    assert errors == []
    assert calls == [
        ("client", PROJECTS_BOARD_ID, 1, "New One"),
        ("client", PROJECTS_BOARD_ID, 3, "New Three"),
    ]
    assert sleeps == [script.WRITE_DELAY_SECONDS]
