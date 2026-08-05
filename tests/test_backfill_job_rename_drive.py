"""Drive job-folder bulk rename script — pure planning and paged reads."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import backfill_job_rename_drive as script  # noqa: E402


FOLDER_ID = "1AbCdEfGhIjKlMnOp"


def _item(
    *,
    item_id: str = "101",
    name: str = "9195 Silva | Willow Creek",
    location: str = "9195 Silva Drive, Cincinnati, OH 45241",
    builder: str = "Willow Creek",
    customer: str = "",
    gfolder: str = f"https://drive.google.com/drive/folders/{FOLDER_ID}",
    location_value: str = "",
    linked_bid_id: int | None = None,
) -> dict:
    columns = [
        {
            "id": script.PROJECT_LOCATION_COL,
            "text": location,
            "value": location_value,
        },
        {"id": script.PROJECT_BUILDER_COL, "text": builder},
        {"id": script.PROJECT_CUSTOMER_COL, "text": customer,
         "display_value": customer},
        {"id": script.PROJECTS_GFOLDER_COL, "text": "GFolder",
         "url": gfolder},
    ]
    if linked_bid_id is not None:
        columns.append({
            "id": script.PROJECT_OPPORTUNITY_COL,
            "text": "Linked Bid",
            "value": json.dumps({
                "linkedPulseIds": [{"linkedPulseId": linked_bid_id}],
            }),
        })
    return {
        "id": item_id,
        "name": name,
        "group": {"id": "active", "title": "Active"},
        "column_values": columns,
    }


def test_shape_project_row_uses_location_builder_customer_and_gfolder():
    row = script.shape_project_row(
        _item(builder="", customer="Willow Creek Homes"))
    assert row == {
        "item_id": 101,
        "name": "9195 Silva | Willow Creek",
        "group_title": "Active",
        "location": "9195 Silva Drive, Cincinnati, OH 45241",
        "location_value_json": "",
        "location_column": {
            "id": script.PROJECT_LOCATION_COL,
            "text": "9195 Silva Drive, Cincinnati, OH 45241",
            "value": "",
        },
        "builder": "",
        "customer": "Willow Creek Homes",
        "gfolder_url": (
            f"https://drive.google.com/drive/folders/{FOLDER_ID}"),
        "linked_bid_ids": [],
    }


def test_shape_project_row_keeps_location_json_and_linked_bid_ids():
    location_value = json.dumps({
        "address": "9195 Silva Drive, Cincinnati, OH 45241",
    })

    row = script.shape_project_row(_item(
        location="",
        location_value=location_value,
        linked_bid_id=77,
    ))

    assert row["location_value_json"] == location_value
    assert row["location_column"]["value"] == location_value
    assert row["linked_bid_ids"] == [77]


def test_list_projects_pages_and_requires_nonempty_gfolder():
    class FakeMonday:
        def __init__(self):
            self.calls = []

        def _query(self, _query, variables):
            self.calls.append(variables)
            if variables["cursor"] is None:
                return {"boards": [{"items_page": {
                    "cursor": "page-2",
                    "items": [
                        _item(item_id="101"),
                        _item(item_id="102", gfolder=""),
                    ],
                }}]}
            return {"boards": [{"items_page": {
                "cursor": None,
                "items": [_item(item_id="103")],
            }}]}

    mc = FakeMonday()
    rows = script.list_projects_with_gfolder(mc)
    assert [row["item_id"] for row in rows] == [101, 103]
    assert [call["cursor"] for call in mc.calls] == [None, "page-2"]


def test_plan_drive_rename_uses_drive_safe_slug_and_folder_id():
    plan = script.plan_drive_rename(
        shape_project_row_for_plan(
            name="9195 Silva | Willow Creek",
            location="9195 Silva Drive, Cincinnati, OH 45241"))
    assert plan["action"] == "rename"
    assert plan["folder_id"] == FOLDER_ID
    assert plan["new_name"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert plan["new_slug"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 Willow Creek")
    assert "|" not in plan["new_slug"]


def test_plan_drive_rename_uses_location_json():
    row = shape_project_row_for_plan(
        location="",
        location_value_json=json.dumps({
            "address": "9195 Silva Drive, Cincinnati, OH 45241",
        }),
    )
    row["location_column"] = {
        "id": script.PROJECT_LOCATION_COL,
        "text": "",
        "value": row["location_value_json"],
    }

    plan = script.plan_drive_rename(row)

    assert plan["action"] == "rename"
    assert plan["new_name"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert plan["lookup_sources"] == ["monday_location"]


def test_plan_drive_rename_geocodes_incomplete_location():
    plan = script.plan_drive_rename(
        shape_project_row_for_plan(location="", location_value_json=""),
        geocode_street_fn=lambda street: {
            "street": f"{street} Drive",
            "city": "Cincinnati",
            "state": "OH",
            "zip": "45241",
            "hint": f"{street} Drive, Cincinnati, OH 45241",
        },
        reverse_geocode_fn=lambda *_args: None,
    )

    assert plan["action"] == "rename"
    assert "nominatim_tri_state" in plan["lookup_sources"]


def test_plan_drive_rename_uses_linked_bid_hint_before_geocoding():
    row = shape_project_row_for_plan(
        location="",
        location_value_json="",
        linked_bid_ids=[77],
    )

    plan = script.plan_drive_rename(
        row,
        bid_hints={77: "9195 Silva Drive, Cincinnati, OH 45241"},
        geocode=False,
    )

    assert plan["action"] == "rename"
    assert plan["new_name"] == (
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek")
    assert "linked_or_drive" in plan["lookup_sources"]


def test_plan_drive_rename_skips_unresolvable_folder_url():
    row = shape_project_row_for_plan(gfolder_url="not a drive folder")
    plan = script.plan_drive_rename(row)
    assert plan["action"] == "skip_folder"
    assert plan["folder_id"] is None


def test_drive_names_match_after_pipe_is_sanitized():
    assert script.drive_names_match(
        "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek",
        "9195 Silva Drive, Cincinnati, OH 45241 Willow Creek",
    )


def test_plan_drive_rename_skips_equal_sanitized_names(monkeypatch):
    monkeypatch.setattr(
        script.rename_enrich,
        "plan_enriched_row",
        lambda **_kwargs: {
        "action": "rename",
        "ok": True,
        "item_id": 101,
        "old_name": "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek",
        "new_name": "9195 Silva Drive, Cincinnati, OH 45241 Willow Creek",
        "note": "",
        },
    )
    plan = script.plan_drive_rename(shape_project_row_for_plan())
    assert plan["action"] == "skip_drive_standard"
    assert plan["old_slug"] == plan["new_slug"]


def test_plan_drive_rename_preserves_planner_skips():
    plan = script.plan_drive_rename(
        shape_project_row_for_plan(
            name="CO.1 - 9195 Silva | Willow Creek"))
    assert plan["action"] == "skip_co"


def test_dry_run_wins_over_apply():
    assert script.should_apply(apply=True, dry_run=True) is False
    assert script.should_apply(apply=True, dry_run=False) is True
    assert script.should_apply(apply=False, dry_run=False) is False


def test_apply_renames_only_eligible_rows(monkeypatch):
    rows = [
        shape_project_row_for_plan(),
        shape_project_row_for_plan(
            item_id=102,
            name="CO.1 - 9195 Silva | Willow Creek",
        ),
    ]
    calls = []

    class FakeDrive:
        def rename_file(self, folder_id, new_name):
            calls.append((folder_id, new_name))
            return {"file_id": folder_id, "filename": new_name}

    monkeypatch.setattr(script, "MondayClient", lambda: object())
    monkeypatch.setattr(
        script, "list_projects_with_gfolder",
        lambda _mc, limit=None: rows[:limit],
    )
    monkeypatch.setattr(script, "DriveUploader", FakeDrive)
    monkeypatch.setattr(script.time, "sleep", lambda _seconds: None)

    assert script.run(apply=True, limit=20, geocode=True) == 0
    assert calls == [(
        FOLDER_ID,
        "9195 Silva Drive, Cincinnati, OH 45241 Willow Creek",
    )]


def test_dry_run_does_not_initialize_drive(monkeypatch):
    monkeypatch.setattr(script, "MondayClient", lambda: object())
    monkeypatch.setattr(
        script, "list_projects_with_gfolder",
        lambda _mc, limit=None: [shape_project_row_for_plan()],
    )
    monkeypatch.setattr(
        script, "DriveUploader",
        lambda: (_ for _ in ()).throw(AssertionError("Drive initialized")),
    )
    assert script.run(apply=False, limit=None, geocode=True) == 0


def test_no_geocode_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(
        script,
        "run",
        lambda *, apply, limit, geocode:
            seen.append((apply, limit, geocode)) or 0,
    )

    assert script.main(["--no-geocode"]) == 0
    assert seen == [(False, None, False)]


def shape_project_row_for_plan(**overrides) -> dict:
    row = {
        "item_id": 101,
        "name": "9195 Silva | Willow Creek",
        "group_title": "Active",
        "location": "9195 Silva Drive, Cincinnati, OH 45241",
        "location_value_json": "",
        "location_column": {
            "id": script.PROJECT_LOCATION_COL,
            "text": "9195 Silva Drive, Cincinnati, OH 45241",
            "value": "",
        },
        "builder": "Willow Creek",
        "customer": "",
        "gfolder_url": (
            f"https://drive.google.com/drive/folders/{FOLDER_ID}"),
        "linked_bid_ids": [],
    }
    row.update(overrides)
    if (
        "location_column" not in overrides
        and (
            "location" in overrides
            or "location_value_json" in overrides
        )
    ):
        row["location_column"] = {
            "id": script.PROJECT_LOCATION_COL,
            "text": row["location"],
            "value": row["location_value_json"],
        }
    return row
