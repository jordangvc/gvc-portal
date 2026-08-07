"""Focused tests for the Operations-board bulk title rename."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import backfill_job_rename_ops as ops  # noqa: E402


STANDARD = (
    "9761 Gertrude Lane, Cincinnati, OH 45231 | Jent Construction | "
    "Bryant residence"
)
LOCATION_VALUE = json.dumps({
    "address": "9761 Gertrude Lane, Cincinnati, OH 45231",
})


def _project(
    item_id: int = 11,
    name: str = STANDARD,
    *,
    location: str = "",
    location_value: str = "",
    customer: str = "Bryant",
    project_type: str = "Residential",
) -> dict:
    location_column = {
        "id": ops.JOBSTART_P_COL_LOCATION,
        "text": location,
        "value": location_value,
    }
    return {
        "id": item_id,
        "name": name,
        "location": location,
        "location_value_json": location_value,
        "location_column": location_column,
        "customer": customer,
        "project_type": project_type,
    }


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
        _page([{
            "id": "11",
            "name": STANDARD,
            "column_values": [{
                "id": ops.JOBSTART_P_COL_LOCATION,
                "text": "",
                "value": LOCATION_VALUE,
            }, {
                "id": ops.JOBSTART_P_COL_CUSTOMER,
                "text": "Bryant",
                "display_value": "Bryant",
            }, {
                "id": ops.COL_PROJECT_TYPE_STATUS,
                "text": "Residential",
            }],
        }], cursor="next"),
        _page([{"id": "12", "name": "CO.1 - ignored"}]),
    ])

    projects, by_id = ops.list_project_names(mc)

    assert projects == [_project(location_value=LOCATION_VALUE)]
    assert by_id == {11: STANDARD}
    assert [call["cursor"] for call in mc.calls] == [None, "next"]
    assert mc.calls[0]["cols"] == [
        ops.JOBSTART_P_COL_LOCATION,
        ops.JOBSTART_P_COL_CUSTOMER,
        ops.COL_PROJECT_TYPE_STATUS,
    ]


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
        projects=[_project()],
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
        projects=[_project()],
        project_names={11: STANDARD},
    )

    assert plan["action"] == "rename"
    assert plan["new_name"] == STANDARD
    assert plan["source"] == "matched_project"
    assert plan["match_score"] == 1.0


def test_linked_nonstandard_project_location_enriches_operation():
    old_project_name = "9761 Gertrude | Jent Construction"
    row = {
        "item_id": 22,
        "name": old_project_name,
        "linked_project_ids": [11],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[
            _project(name=old_project_name, location_value=LOCATION_VALUE),
        ],
        project_names={11: old_project_name},
    )

    assert plan["action"] == "rename"
    assert plan["new_name"] == STANDARD
    assert plan["source"] == "linked_project_enriched"
    assert plan["lookup_sources"] == ["monday_location"]


def test_linked_nonstandard_project_prefers_planned_parent_index():
    """Ops mirrors the Projects plan when Monday's Projects title is still short."""
    old_project_name = "9761 Gertrude | Jent Construction"
    row = {
        "item_id": 22,
        "name": "9761 Gertrude | Different Street Spelling",
        "linked_project_ids": [11],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[_project(name=old_project_name, location_value=LOCATION_VALUE)],
        project_names={11: old_project_name},
        parent_index={old_project_name: STANDARD},
        geocode=False,
    )

    assert plan["action"] == "rename"
    assert plan["new_name"] == STANDARD
    assert plan["source"] == "linked_project_planned"


def test_unlinked_operation_geocodes_after_no_safe_project_match():
    """Geocode alone cannot invent Job Title — stay incomplete."""
    row = {
        "item_id": 23,
        "name": "9761 Gertrude | Jent Construction",
        "linked_project_ids": [],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[],
        project_names={},
        geocode_street_fn=lambda street: {
            "street": f"{street} Lane",
            "city": "Cincinnati",
            "state": "OH",
            "zip": "45231",
            "hint": f"{street} Lane, Cincinnati, OH 45231",
        },
        reverse_geocode_fn=lambda *_args: None,
    )

    assert plan["action"] == "skip_incomplete"
    assert plan["source"] == "ops_name_geocoded"
    assert "job title" in (plan.get("note") or "").lower()


def test_unlinked_operation_skips_when_no_safe_standard_match():
    row = {
        "item_id": 23,
        "name": "Misc punch list",
        "linked_project_ids": [],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[_project()],
        project_names={11: STANDARD},
        geocode=False,
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
                "9761 Gertrude Lane, Dayton, OH 45402 | Jent Construction | "
                "Bryant residence"
            ),
        },
    ]

    plan = ops.plan_operation_item(
        row,
        projects=projects,
        project_names={project["id"]: project["name"] for project in projects},
        geocode=False,
    )

    assert plan["action"] == "skip_incomplete"
    assert plan["source"] == "ops_name"


def test_co_rows_cascade_from_linked_project():
    row = {
        "item_id": 24,
        "name": "CO.1 - 9761 Gertrude | Jent Construction",
        "linked_project_ids": [11],
    }

    plan = ops.plan_operation_item(
        row,
        projects=[_project()],
        project_names={11: STANDARD},
    )

    assert plan["action"] == "rename"
    assert plan["new_name"] == f"CO.1 - {STANDARD}"
    assert plan["source"] == "linked_project_parent"


def test_co_rows_resolve_parent_from_enriched_projects_index():
    old_parent = "9761 Gertrude | Jent Construction"
    row = {
        "item_id": 25,
        "name": f"CO.2 - {old_parent}",
        "linked_project_ids": [],
    }
    project = _project(name=old_parent, location_value=LOCATION_VALUE)
    project_plan = ops.rename_enrich.plan_enriched_row(
        name=old_parent,
        location_value_json=LOCATION_VALUE,
        customer=project["customer"],
        geocode=False,
        **ops.rename_enrich.job_title_kwargs_from_monday(
            status=project["project_type"],
            customer=project["customer"],
        ),
    )
    parent_index = ops.rename_enrich.index_parent_titles([project_plan])

    plan = ops.plan_operation_item(
        row,
        projects=[project],
        project_names={11: old_parent},
        parent_index=parent_index,
        geocode=False,
    )

    assert plan["action"] == "rename"
    assert plan["new_name"] == f"CO.2 - {STANDARD}"
    assert plan["source"] == "parent_index"


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
                "100 Main Street, Cincinnati, OH 45202 | Example Builder | "
                "Acme Bank"
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
            "100 Main Street, Cincinnati, OH 45202 | Example Builder | "
            "Acme Bank",
        ),
    ]
    assert sleeps == [0.2]


def test_dry_run_wins_when_both_flags_are_set(monkeypatch):
    seen: list[tuple[bool, int | None]] = []
    monkeypatch.setattr(
        ops,
        "run",
        lambda *, apply, limit, geocode:
            seen.append((apply, limit, geocode)) or 0,
    )

    assert ops.main(["--apply", "--dry-run", "--limit", "7"]) == 0
    assert seen == [(False, 7, True)]


def test_no_geocode_flag(monkeypatch):
    seen: list[tuple[bool, int | None, bool]] = []
    monkeypatch.setattr(
        ops,
        "run",
        lambda *, apply, limit, geocode:
            seen.append((apply, limit, geocode)) or 0,
    )

    assert ops.main(["--no-geocode"]) == 0
    assert seen == [(False, None, False)]
