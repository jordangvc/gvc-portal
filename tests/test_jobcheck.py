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
    # Display order is config order.
    assert effective[0]["label"] == "Stage"
    assert effective[-1]["label"] == "Shower Instructions"


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
