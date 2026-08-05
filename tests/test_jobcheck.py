"""
Job Check pure-logic tests — allowlist gating, value shaping, validation,
status-label parsing, job-row normalization, change descriptions. Everything
here is pure — no Monday, no network (the write path is exercised with a
stub client). Runs under pytest OR directly: `python tests/test_jobcheck.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday import jobcheck as mj  # noqa: E402
from orchestrators import jobcheck_flow as jf  # noqa: E402
from shared import boards  # noqa: E402


# ------------------------------------------------------------ config sanity

def test_config_entries_are_well_formed():
    assert boards.JOBCHECK_COLUMNS, "shipped allowlist is empty"
    for entry in boards.JOBCHECK_COLUMNS:
        assert entry["id"].strip(), entry
        assert entry["label"].strip(), entry
        assert entry["type"] in boards.JOBCHECK_RENDER_TYPES, entry


def test_shipped_config_survives_the_hard_exclusion_gate():
    # Every shipped entry must come through allowlisted_columns() unchanged —
    # if this fails, the default config itself contains an excluded column.
    effective = jf.allowlisted_columns()
    assert [c["id"] for c in effective] == [c["id"] for c in boards.JOBCHECK_COLUMNS]
    assert all(c["board"] == "ops" for c in effective)
    # Display order is config order.
    assert effective[0]["label"] == "Stage"
    assert effective[-1]["label"] == "Open questions for Ops"
    assert any(c["id"] == "date" for c in effective)
    assert any(c["id"] == "long_text_mkpzf3je" for c in effective)
    assert any(c["id"] == "color_mm02xmc0" for c in effective)
    # Trade cols stay out of the Ops allowlist (Projects board only).
    trade_ids = {c["id"] for c in boards.JOBCHECK_PROJECTS_TRADE_COLUMNS}
    assert not trade_ids & {c["id"] for c in boards.JOBCHECK_COLUMNS} - {"status_19"}
    # status_19 is the intentional id collision — Ops Scheduled Day vs Projects Hanging.
    assert any(c["id"] == "status_19" for c in boards.JOBCHECK_COLUMNS)
    assert any(c["id"] == "status_19" for c in boards.JOBCHECK_PROJECTS_TRADE_COLUMNS)


def test_hard_exclusions_beat_config_edits():
    # Simulate someone adding money/link/relation columns to the config:
    # they must never reach the effective allowlist.
    bad = (
        {"id": "board_counts", "label": "Board Count", "type": "text"},          # money by ID
        {"id": "numeric_mm5ahj91", "label": "CO Amount", "type": "number"},      # money by ID
        {"id": "lookup_mm40txvs", "label": "Contract Value", "type": "mirror"},  # excluded type
        {"id": "board_relation_mkvp7t1x", "label": "Invoicing Link", "type": "board_relation"},
        {"id": "link_mkwr6ef9", "label": "GFolder Link", "type": "link"},
        {"id": "files", "label": "Files", "type": "file"},
        {"id": "weird", "label": "Weird", "type": "rating"},                     # unsupported render type
    )
    saved = boards.JOBCHECK_COLUMNS
    boards.JOBCHECK_COLUMNS = saved + bad
    try:
        ids = {c["id"] for c in jf.allowlisted_columns()}
        for entry in bad:
            assert entry["id"] not in ids, f"{entry['id']} leaked through the gate"
        # …and the validator rejects them as write targets too.
        shaped, errors, _ = jf.validate_values({"board_counts": "999",
                                                "lookup_mm40txvs": "x"})
        assert shaped == {}
        assert set(errors) == {"board_counts", "lookup_mm40txvs"}
    finally:
        boards.JOBCHECK_COLUMNS = saved


# ------------------------------------------------------------- value shaping

def test_shape_status_date_number_text():
    assert jf.shape_value("status", "100% Hanging Completed") == \
        {"label": "100% Hanging Completed"}
    assert jf.shape_value("status", "") is None                 # clear
    assert jf.shape_value("date", "2026-07-27") == {"date": "2026-07-27"}
    assert jf.shape_value("date", None) is None                 # clear
    assert jf.shape_value("number", "42") == "42"
    assert jf.shape_value("number", "3.5") == "3.5"
    assert jf.shape_value("number", "1,250") == "1250"
    assert jf.shape_value("number", "") == ""                   # clear
    assert jf.shape_value("text", "  lot 4 ") == "lot 4"
    assert jf.shape_value("text", None) == ""                   # clear
    assert jf.shape_value("long_text", "crew note") == {"text": "crew note"}
    assert jf.shape_value("checkbox", "true") == {"checked": True}
    assert jf.shape_value("checkbox", "false") is None
    assert jf.shape_value("checkbox", True) == {"checked": True}


def test_shape_rejects_garbage():
    for rtype, raw in (("date", "tomorrow"), ("date", "07/27/2026"),
                       ("number", "forty"), ("checkbox", "maybe"),
                       ("nonsense", "x")):
        try:
            jf.shape_value(rtype, raw)
        except ValueError:
            continue
        raise AssertionError(f"shape_value({rtype!r}, {raw!r}) did not raise")


def test_shape_text_length_cap():
    jf.shape_value("long_text", "x" * jf.MAX_TEXT_LEN)  # at the cap: fine
    try:
        jf.shape_value("long_text", "x" * (jf.MAX_TEXT_LEN + 1))
    except ValueError as e:
        assert "too long" in str(e)
    else:
        raise AssertionError("over-cap text did not raise")


# --------------------------------------------------------------- validation

def test_validate_values_allowlist_and_labels():
    labels = {"status_19": ["Hanging Not Started", "100% Hanging Completed"]}
    shaped, errors, accepted = jf.validate_values(
        {"status_19": "100% Hanging Completed",     # ok
         "date_mm1kwzf9": "2026-07-27",                     # ok
         "text_mm14mhpm": "back bedroom needs a skim",     # ok
         "status_19x": "boom",                      # not allowlisted
         "color_mm2xd40t": "For Invoicing"},            # not in the config
        status_labels=labels)
    assert shaped == {"status_19": {"label": "100% Hanging Completed"},
                      "date_mm1kwzf9": {"date": "2026-07-27"},
                      "text_mm14mhpm": "back bedroom needs a skim"}
    assert set(errors) == {"status_19x", "color_mm2xd40t"}
    assert set(accepted) == set(shaped)


def test_validate_values_rejects_unknown_status_label():
    labels = {"status_19": ["Hanging Not Started"]}
    shaped, errors, _ = jf.validate_values({"status_19": "Totally Done"},
                                           status_labels=labels)
    assert shaped == {}
    assert "not a label" in errors["status_19"]
    # Without a label set to check against, the label passes through
    # (Monday itself becomes the validator and reports per-column).
    shaped, errors, _ = jf.validate_values({"status_19": "Totally Done"})
    assert shaped == {"status_19": {"label": "Totally Done"}} and not errors


def test_validate_values_bad_value_is_per_column():
    shaped, errors, _ = jf.validate_values(
        {"date_mm1kwzf9": "not-a-date", "text_mm14mhpm": "fine"}, status_labels={})
    assert list(shaped) == ["text_mm14mhpm"]
    assert "date_mm1kwzf9" in errors and "YYYY-MM-DD" in errors["date_mm1kwzf9"]


# ------------------------------------------------- status settings parsing

def test_parse_status_labels_classic_dict_shape():
    settings = json.dumps({
        "labels": {"5": "Not Started", "1": "Working", "2": "Done", "9": ""},
        "labels_colors": {"5": {"color": "#c4c4c4"}, "1": {"color": "#00c875"},
                          "2": {"color": "#037f4c"}},
        "labels_positions_v2": {"5": 0, "1": 1, "2": 2},
    })
    out = mj.parse_status_labels(settings)
    assert [l["label"] for l in out] == ["Not Started", "Working", "Done"]
    assert out[1]["hex"] == "#00c875"


def test_parse_status_labels_list_shape_and_deactivated():
    settings = json.dumps({"labels": [
        {"id": 1, "label": "Done", "index": 2, "hex": "#037f4c",
         "is_deactivated": False},
        {"id": 0, "label": "Old", "index": 1, "hex": "#000",
         "is_deactivated": True},
        {"id": 5, "label": "Not Started", "index": 0, "hex": "#c4c4c4",
         "is_deactivated": False},
    ]})
    out = mj.parse_status_labels(settings)
    assert [l["label"] for l in out] == ["Not Started", "Done"]


def test_parse_status_labels_garbage_is_empty():
    assert mj.parse_status_labels(None) == []
    assert mj.parse_status_labels("") == []
    assert mj.parse_status_labels("not json") == []
    assert mj.parse_status_labels(json.dumps({"labels": 7})) == []


# ------------------------------------------------- job-row normalization

def _raw_item(name="123 Main St - Builder - New House", group="topics",
              title="Activities/Tasks (In-Progress)", **cols):
    # Operations-board context ids (link to Projects / Job Location mirror /
    # Project Status mirror).
    defaults = {"link_to_projects": None, "lookup_mknf1rdw": None, "mirror3": None}
    defaults.update(cols)
    return {"id": "101", "name": name,
            "group": {"id": group, "title": title},
            "column_values": [{"id": k, "text": v} for k, v in defaults.items()]}


def test_normalize_job_keeps_active_and_maps_columns():
    row = mj._normalize_job(_raw_item(link_to_projects="123 Main St - Builder",
                                      lookup_mknf1rdw="Brookville, IN",
                                      mirror3="Work-in-Progress"))
    assert row["item_id"] == 101
    assert row["project_number"] == "123 Main St - Builder"
    assert row["location"] == "Brookville, IN"
    assert row["deal_stage"] == "Work-in-Progress"
    assert str(mj.JOBCHECK_BOARD_ID) in row["url"]


def test_normalize_job_skips_finished_and_lost():
    # Completed Tasks and the office's invoicing queue are hidden from the crew.
    assert mj._normalize_job(_raw_item(group="new_group",
                                       title="Completed Tasks")) is None
    assert mj._normalize_job(_raw_item(group="group_mm3zq4q2",
                                       title="Ready to Invoice")) is None
    assert mj._normalize_job(_raw_item(mirror3="Project Lost/canceled")) is None
    # Upcoming work stays in — the crew may start it today.
    assert mj._normalize_job(_raw_item(group="group_mm3khfvc",
                                       title="Upcoming Projects (Not Started)")) is not None


# ----------------------------------------------------- write-path fallback

class _StubMC:
    """Stub Monday client: batch mutation fails, per-column succeeds except
    for one poisoned column — proves the per-column error reporting."""
    def __init__(self):
        self.calls = []

    def _query(self, query, variables):
        values = json.loads(variables["values"])
        self.calls.append(sorted(values))
        if len(values) > 1:
            raise RuntimeError("Monday API error: batch boom")
        if "date_mm1kwzf9" in values:
            raise RuntimeError("Monday API error: bad date payload")
        return {"change_multiple_column_values": {"id": variables["itemId"]}}


def test_set_item_columns_batch_then_per_column_errors():
    mc = _StubMC()
    out = mj.set_item_columns(mc, 101, {
        "status_19": {"label": "Done"},
        "text_mm14mhpm": {"text": "hi"},
        "date_mm1kwzf9": {"date": "2026-07-27"},
    })
    assert out["written"] == ["status_19", "text_mm14mhpm"]
    assert list(out["failed"]) == ["date_mm1kwzf9"]
    assert "bad date payload" in out["failed"]["date_mm1kwzf9"]
    # first call was the batch (3 cols), then one call per column
    assert sorted(mc.calls[0]) == ["date_mm1kwzf9", "status_19", "text_mm14mhpm"]
    assert all(len(c) == 1 for c in mc.calls[1:])


def test_set_item_columns_happy_batch_and_guards():
    class _OkMC:
        def _query(self, query, variables):
            assert "change_multiple_column_values" in query
            assert "create_item" not in query and "delete" not in query.lower()
            return {"change_multiple_column_values": {"id": variables["itemId"]}}
    out = mj.set_item_columns(_OkMC(), 101, {"text_mm14mhpm": {"text": "hi"}})
    assert out == {"written": ["text_mm14mhpm"], "failed": {}}
    assert mj.set_item_columns(_OkMC(), 101, {}) == {"written": [], "failed": {}}
    try:
        mj.set_item_columns(_OkMC(), 0, {"text_mm14mhpm": {"text": "hi"}})
    except ValueError:
        pass
    else:
        raise AssertionError("empty item_id did not raise")


# --------------------------------------------------------- change describe

def test_describe_changes_reads_like_the_audit_trail():
    cols = {"status_19": {"label": "Hanging Status"},
            "text_mm14mhpm": {"label": "Notes"}}
    text = jf.describe_changes(
        {"status_19": "Hanging Not Started", "text_mm14mhpm": None},
        {"status_19": "100% Hanging Completed", "text_mm14mhpm": "swept garage"},
        cols)
    assert "Hanging Status: Hanging Not Started → 100% Hanging Completed" in text
    assert "Notes: (empty) → swept garage" in text
    assert jf.describe_changes({}, {}, {}) == ""




def test_new_writable_columns_are_allowlisted_and_shapeable():
    ids = {c["id"] for c in jf.allowlisted_columns()}
    assert {"date", "long_text_mkpzf3je", "color_mm02xmc0"} <= ids
    # Progress is context-only — never a write target even if someone adds it.
    assert "lookup_mkpeqd8w" not in ids
    shaped, errors, _ = jf.validate_values({
        "date": "2026-08-04",
        "long_text_mkpzf3je": "GC still owes paint schedule",
        "color_mm02xmc0": "Drywall",
        "lookup_mkpeqd8w": "50%",   # hard-excluded type / not allowlisted
    })
    assert shaped["date"] == {"date": "2026-08-04"}
    assert shaped["long_text_mkpzf3je"] == {"text": "GC still owes paint schedule"}
    assert shaped["color_mm02xmc0"] == {"label": "Drywall"}
    assert "lookup_mkpeqd8w" in errors


def test_hard_exclusions_still_block_money_and_gfolder_link():
    # GFolder Link / Progress are excluded by TYPE (link / lookup); Board Count by ID.
    assert "link" in boards.JOBCHECK_HARD_EXCLUDED_TYPES
    assert "lookup" in boards.JOBCHECK_HARD_EXCLUDED_TYPES
    assert "board_counts" in boards.JOBCHECK_HARD_EXCLUDED_IDS
    saved = boards.JOBCHECK_COLUMNS
    boards.JOBCHECK_COLUMNS = saved + (
        {"id": "link_mkwr6ef9", "label": "GFolder Link", "type": "link"},
        {"id": "lookup_mkpeqd8w", "label": "Progress", "type": "lookup"},
        {"id": "board_counts", "label": "Board Count", "type": "text"},
    )
    try:
        ids = {c["id"] for c in jf.allowlisted_columns()}
        assert "link_mkwr6ef9" not in ids
        assert "lookup_mkpeqd8w" not in ids
        assert "board_counts" not in ids
    finally:
        boards.JOBCHECK_COLUMNS = saved


def test_pick_pictures_folder_prefers_most_recently_modified():
    # Callers pass folder-only lists (DriveUploader.list_child_folders).
    from subsystems.morning.media import pick_pictures_folder
    kids = [
        {"id": "1", "name": "Photos", "modifiedTime": "2026-08-01T00:00:00.000Z"},
        {"id": "2", "name": "Pictures", "modifiedTime": "2026-07-01T00:00:00.000Z"},
        {"id": "3", "name": "pictures", "modifiedTime": "2026-08-03T12:00:00.000Z"},
        {"id": "4", "name": "Site Photos", "modifiedTime": "2026-08-04T00:00:00.000Z"},
    ]
    picked = pick_pictures_folder(kids)
    assert picked["id"] == "3"   # most recent folder named Pictures
    assert pick_pictures_folder([]) is None
    assert pick_pictures_folder([{"id": "x", "name": "Other"}]) is None


def test_post_update_validation_without_network():
    # Empty / whitespace rejected before any Monday call.
    out = jf.post_update(101, "   ", "mark@greenvalleycontractors.com")
    assert out["ok"] is False and out["error"] == "EMPTY_UPDATE"
    out = jf.post_update(101, "", "mark@greenvalleycontractors.com")
    assert out["ok"] is False and out["error"] == "EMPTY_UPDATE"
    # Over-cap rejected before network.
    out = jf.post_update(101, "x" * (jf.MAX_UPDATE_LEN + 1), "mark@x.com")
    assert out["ok"] is False and out["error"] == "UPDATE_TOO_LONG"


def test_upload_photos_validation_without_network():
    out = jf.upload_photos(101, [], "mark@x.com", note="hi")
    assert out["ok"] is False and out["error"] == "NO_FILES"


def test_create_item_update_requires_body():
    class _MC:
        def _query(self, *a, **k):
            raise AssertionError("should not call Monday on empty body")
    try:
        mj.create_item_update(_MC(), 101, "  ")
    except ValueError as e:
        assert "required" in str(e).lower()
    else:
        raise AssertionError("empty update body did not raise")



# ------------------------------------ Projects trade status (phase-2 slice 1)

def test_projects_trade_config_well_formed_and_gated():
    assert boards.JOBCHECK_PROJECTS_TRADE_COLUMNS, "trade allowlist is empty"
    expected = {
        "color_mkza9z7c": "Framing Status",
        "status_19": "Hanging Status",
        "dup__of_hung_status1": "Scrapping Status",
        "dup__of_scrapped_status": "Taped Status",
        "dup__of_taped_status": "2nd Bed Coat",
        "dup__of_2nd_bed_coat": "3rd Coat",
        "dup__of_3rd_coat": "Sanded",
        "dup__of_sanded": "Text/Skim",
        "color_mkza855s": "Finishing Stage",
    }
    got = {c["id"]: c["label"] for c in boards.JOBCHECK_PROJECTS_TRADE_COLUMNS}
    assert got == expected
    for entry in boards.JOBCHECK_PROJECTS_TRADE_COLUMNS:
        assert entry["type"] == "status", entry
    effective = jf.allowlisted_projects_trade_columns()
    assert [c["id"] for c in effective] == list(expected)
    assert all(c["board"] == "projects" for c in effective)
    # Display order walks the trade sequence the crew actually walks:
    # Framing -> Hang -> Scrap -> Tape -> 2nd coat -> 3rd coat -> Sand -> Skim
    # -> Finishing. Finishing Stage stays last (unchanged from slice 1).
    assert [c["id"] for c in effective][-1] == "color_mkza855s"
    # Hard exclusions still apply if someone tries to sneak a mirror in.
    saved = boards.JOBCHECK_PROJECTS_TRADE_COLUMNS
    boards.JOBCHECK_PROJECTS_TRADE_COLUMNS = saved + (
        {"id": "mirror3", "label": "Project Status", "type": "mirror"},
        {"id": "link_to_projects", "label": "Link", "type": "board_relation"},
    )
    try:
        ids = {c["id"] for c in jf.allowlisted_projects_trade_columns()}
        assert "mirror3" not in ids
        assert "link_to_projects" not in ids
    finally:
        boards.JOBCHECK_PROJECTS_TRADE_COLUMNS = saved


def test_coat_sand_skim_slice_is_writable_and_board_scoped():
    """Slice 2 (2026-08-05): Taped/2nd Bed Coat/3rd Coat/Sanded/Text-Skim
    write to the linked Projects item exactly like the slice-1 four did."""
    new_ids = {"dup__of_scrapped_status", "dup__of_taped_status",
               "dup__of_2nd_bed_coat", "dup__of_3rd_coat", "dup__of_sanded"}
    effective = {c["id"]: c for c in jf.allowlisted_projects_trade_columns()}
    assert new_ids <= set(effective)
    for cid in new_ids:
        assert effective[cid]["board"] == "projects"
        assert effective[cid]["type"] == "status"
    # None of the new ids leak into the Ops allowlist.
    assert not new_ids & {c["id"] for c in jf.allowlisted_columns()}

    labels = {jf.field_key("projects", "dup__of_taped_status"):
              ["Coat Not Started", "2nd Coat Complete"]}
    shaped, errors, accepted = jf.validate_values(
        {"projects:dup__of_taped_status": "2nd Coat Complete"},
        status_labels=labels)
    assert shaped == {"projects:dup__of_taped_status":
                      {"label": "2nd Coat Complete"}}
    assert not errors
    assert accepted["projects:dup__of_taped_status"]["label"] == "2nd Bed Coat"


def test_status_19_collision_is_board_scoped():
    # Ops status_19 = Scheduled Day; Projects status_19 = Hanging Status.
    ops = next(c for c in jf.allowlisted_columns() if c["id"] == "status_19")
    proj = next(c for c in jf.allowlisted_projects_trade_columns()
                if c["id"] == "status_19")
    assert ops["label"] == "Scheduled Day" and ops["board"] == "ops"
    assert proj["label"] == "Hanging Status" and proj["board"] == "projects"
    assert jf.field_key(ops) == "ops:status_19"
    assert jf.field_key(proj) == "projects:status_19"
    assert jf.parse_value_key("status_19") == ("ops", "status_19")
    assert jf.parse_value_key("projects:status_19") == ("projects", "status_19")

    labels = {
        "status_19": ["Today", "Tomorrow"],                       # Ops Scheduled Day
        "projects:status_19": ["Hanging Not Started", "100% Hanging Completed"],
    }
    shaped, errors, accepted = jf.validate_values({
        "status_19": "Today",
        "projects:status_19": "100% Hanging Completed",
    }, status_labels=labels)
    assert shaped["status_19"] == {"label": "Today"}
    assert shaped["projects:status_19"] == {"label": "100% Hanging Completed"}
    assert accepted["status_19"]["board"] == "ops"
    assert accepted["projects:status_19"]["board"] == "projects"
    assert not errors
    # Cross-board label misuse is rejected.
    shaped, errors, _ = jf.validate_values(
        {"projects:status_19": "Today"}, status_labels=labels)
    assert shaped == {} and "not a label" in errors["projects:status_19"]


def test_set_item_columns_passes_projects_board_id():
    seen = {}

    class _OkMC:
        def _query(self, query, variables):
            seen["boardId"] = variables["boardId"]
            seen["itemId"] = variables["itemId"]
            assert "change_multiple_column_values" in query
            return {"change_multiple_column_values": {"id": variables["itemId"]}}

    out = mj.set_item_columns(
        _OkMC(), 555, {"status_19": {"label": "100% Hanging Completed"}},
        board_id=boards.PROJECTS_BOARD_ID)
    assert out == {"written": ["status_19"], "failed": {}}
    assert seen["boardId"] == str(boards.PROJECTS_BOARD_ID)
    assert seen["itemId"] == "555"
    # Default remains the Job Check / Ops board.
    seen.clear()
    mj.set_item_columns(_OkMC(), 101, {"status": {"label": "Hanging"}})
    assert seen["boardId"] == str(boards.JOBCHECK_BOARD_ID)


def test_save_missing_project_link_fails_trade_only():
    """Projects trade fields fail clearly when link_to_projects is empty;
    Ops fields still write."""
    ops_before = {
        "item_id": 101, "name": "Job A", "url": "https://monday/x",
        "values": {"status": "Hanging", "status_19": "Today"},
    }
    calls = {"set": []}

    class _MC:
        pass

    def fake_client():
        return _MC()

    def fake_get_item_values(mc, item_id, column_ids):
        return dict(ops_before, values=dict(ops_before["values"]))

    def fake_get_board_columns(mc, column_ids, board_id=None):
        out = {}
        for cid in column_ids:
            if cid in ("status", "status_19", "color_mkza9z7c",
                       "dup__of_hung_status1", "color_mkza855s"):
                out[cid] = {"id": cid, "type": "status",
                            "labels": [{"label": "Hanging"}, {"label": "Today"},
                                       {"label": "Done"}, {"label": "Framed"}]}
            else:
                out[cid] = {"id": cid, "type": "text", "labels": []}
        return out

    def fake_get_linked_project_id(mc, ops_item_id):
        return {"project_item_id": None,
                "error": "No linked Projects item on this Operations task "
                         "(link_to_projects is empty). Link the Projects item "
                         "in Monday before editing trade status or uploading "
                         "photos."}

    def fake_set_item_columns(mc, item_id, values, board_id=None):
        calls["set"].append({"item_id": item_id, "values": dict(values),
                             "board_id": board_id})
        return {"written": sorted(values), "failed": {}}

    import adapters.monday.client as mcmod
    import adapters.slack_notify as sn

    real = {
        "client": mcmod.MondayClient,
        "get_item_values": mj.get_item_values,
        "get_board_columns": mj.get_board_columns,
        "get_linked_project_id": mj.get_linked_project_id,
        "set_item_columns": mj.set_item_columns,
        "notify": sn.notify_jobcheck_saved,
    }
    try:
        mcmod.MondayClient = fake_client  # type: ignore
        mj.get_item_values = fake_get_item_values  # type: ignore
        mj.get_board_columns = fake_get_board_columns  # type: ignore
        mj.get_linked_project_id = fake_get_linked_project_id  # type: ignore
        mj.set_item_columns = fake_set_item_columns  # type: ignore

        def _boom(_payload):
            raise sn.SlackNotConfigured("no channel")
        sn.notify_jobcheck_saved = _boom  # type: ignore

        out = jf.save_job_check(101, {
            "status": "Hanging",
            "projects:status_19": "Done",
            "projects:color_mkza9z7c": "Framed",
        }, "mark@greenvalleycontractors.com")
    finally:
        mcmod.MondayClient = real["client"]  # type: ignore
        mj.get_item_values = real["get_item_values"]  # type: ignore
        mj.get_board_columns = real["get_board_columns"]  # type: ignore
        mj.get_linked_project_id = real["get_linked_project_id"]  # type: ignore
        mj.set_item_columns = real["set_item_columns"]  # type: ignore
        sn.notify_jobcheck_saved = real["notify"]  # type: ignore

    assert out["ok"] is False
    assert out["project_item_id"] is None
    assert len(calls["set"]) == 1
    assert calls["set"][0]["item_id"] == 101
    assert "status" in calls["set"][0]["values"]
    assert calls["set"][0]["board_id"] is None
    assert "status" in out["written"]
    assert "projects:status_19" in out["failures"]
    assert "projects:color_mkza9z7c" in out["failures"]
    assert "link_to_projects" in out["failures"]["projects:status_19"]
    assert "link_to_projects" in out["failures"]["projects:color_mkza9z7c"]
    assert "not a label" not in out["failures"]["projects:color_mkza9z7c"]



if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{failed} failed" if failed else "\nall tests passed")
    sys.exit(1 if failed else 0)


# ------------------------------------------------- Slack notice (pure)

def test_jobcheck_slack_message_routine_vs_blocked():
    from adapters.slack_notify import _jobcheck_message
    base = {"job": "4121 Witler St", "actor": "mark@greenvalleycontractors.com",
            "url": "https://monday/x",
            "changes": [{"label": "Stage", "old": "Hanging", "new": "Finishing"}]}
    routine = _jobcheck_message(base)
    assert routine.startswith("🔧 *Job update*")
    assert "_updated by mark_" in routine          # no email address in channel
    assert "*Stage*: Hanging → Finishing" in routine
    assert "<https://monday/x|Open in Monday>" in routine

    blocked = _jobcheck_message({**base, "changes": base["changes"] + [
        {"label": "Blocked", "old": "Clear", "new": "Waiting on GC"}]})
    assert blocked.startswith("🚧 *Job update — needs attention*")
    # The blocker must be the FIRST bullet, above routine progress.
    bullets = [l for l in blocked.splitlines() if l.startswith("•")]
    assert bullets[0].startswith("• *Blocked*")


def test_jobcheck_slack_clearing_a_blocker_is_not_an_alert():
    from adapters.slack_notify import _jobcheck_message
    msg = _jobcheck_message({"job": "J", "actor": "robert@x.com", "changes": [
        {"label": "Blocked", "old": "Waiting on GC", "new": "Clear"}]})
    assert msg.startswith("🔧 *Job update*")      # good news isn't an alarm


def test_jobcheck_slack_skips_cleanly_without_a_channel(monkeypatch=None):
    import os
    from adapters import slack_notify
    old = os.environ.pop("GVC_JOBCHECK_SLACK_CHANNEL", None)
    try:
        try:
            slack_notify.notify_jobcheck_saved({"job": "J", "changes": []})
            raise AssertionError("expected SlackNotConfigured")
        except slack_notify.SlackNotConfigured:
            pass
    finally:
        if old is not None:
            os.environ["GVC_JOBCHECK_SLACK_CHANNEL"] = old
