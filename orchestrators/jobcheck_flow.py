"""
Job Check flow — pick an active job, fill the allowlist, one save to Monday.
=========================================================================
The portal's FIRST write surface into Monday (docs/portal-job-check-design.md,
Jordan 2026-07-27). Monday stays the source of truth — this flow is a faster
pair of hands updating it: the field crew picks an active Projects-board job,
the form renders ONLY the columns allowlisted in shared/boards.py
(JOBCHECK_COLUMNS), and a single explicit Save writes them back.

Guardrails (enforced HERE, not trusted to the UI):
- Only allowlisted column ids are ever written; unknown ids are rejected.
- Money/contract/link/relation columns can never be written even if someone
  edits JOBCHECK_COLUMNS — the hard-exclusion sets in shared/boards.py are
  re-checked on every save (validate_values).
- Status labels are validated against the board's real label set (fetched
  live), so the form can't invent a label.
- Writes happen ONLY through save_job_check(), which is only reached from
  the explicit POST route — no background writes, no automation.
- Column updates on EXISTING items only; nothing here creates or deletes.
- Every save logs who/item/columns old→new to the activity store.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from pathlib import Path as _Path
from typing import Any, Optional

from adapters import slack_notify
from adapters.drive import DriveUploader
from adapters.monday import jobcheck as mj
from adapters.monday import lien as ml
from adapters.monday.client import MondayClient
from shared import activity
from shared import boards
from subsystems.morning import link_suggest as ls

# Longest value the form may write to a text/long_text column. Generous for
# field notes, small enough that a runaway client can't stuff megabytes in.
MAX_TEXT_LEN = 4000
MAX_UPDATE_LEN = 4000


# ---------------------------------------------------------------------------
# Pure config + validation helpers (unit-tested without Monday)
# ---------------------------------------------------------------------------

BOARD_OPS = "ops"
BOARD_PROJECTS = "projects"


def _gate_column_entries(entries, *, board: str) -> list[dict]:
    """Apply the hard-exclusion / render-type gate to a config tuple."""
    out: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        col_id = str(entry.get("id") or "").strip()
        rtype = str(entry.get("type") or "").strip()
        if not col_id or col_id in seen:
            continue
        if col_id in boards.JOBCHECK_HARD_EXCLUDED_IDS:
            continue
        if rtype in boards.JOBCHECK_HARD_EXCLUDED_TYPES:
            continue
        if rtype not in boards.JOBCHECK_RENDER_TYPES:
            continue
        seen.add(col_id)
        out.append({"id": col_id, "label": entry.get("label") or col_id,
                    "type": rtype, "board": board})
    return out


def allowlisted_columns() -> list[dict]:
    """
    The effective Ops allowlist: JOBCHECK_COLUMNS minus anything hard-excluded
    or declaring a render type the form doesn't support. This is THE gate
    config edits pass through — a money/link column added to the config never
    reaches the form or the validator's allow set. Each entry is tagged
    board="ops".
    """
    # Read via the module attribute (not a from-import) so the gate always
    # sees the CURRENT config — including edits made after import.
    return _gate_column_entries(boards.JOBCHECK_COLUMNS, board=BOARD_OPS)


def allowlisted_projects_trade_columns() -> list[dict]:
    """
    Projects-board trade statuses (phase-2 slice 1). Same hard-exclusion gate
    as Ops; never mixed into JOBCHECK_COLUMNS. Each entry is tagged
    board="projects". status_19 here is Hanging Status — not Ops Scheduled Day.
    """
    return _gate_column_entries(boards.JOBCHECK_PROJECTS_TRADE_COLUMNS,
                                board=BOARD_PROJECTS)


def form_columns() -> list[dict]:
    """Ops allowlist + Projects trade columns, in display order."""
    return allowlisted_columns() + allowlisted_projects_trade_columns()


def field_key(entry_or_board, col_id: Optional[str] = None) -> str:
    """Stable form/API key: 'ops:<id>' or 'projects:<id>'."""
    if col_id is None and isinstance(entry_or_board, dict):
        board = entry_or_board.get("board") or BOARD_OPS
        col_id = entry_or_board["id"]
    else:
        board = entry_or_board or BOARD_OPS
    return f"{board}:{col_id}"


def fieldguide_anchor(column_id: str, board: str) -> Optional[str]:
    """
    Field Manual deep-link for a Job Check column, if one exists.
    Projects and Ops use separate maps so shared column ids (status_19)
    cannot leak the wrong how-to (Hanging vs Scheduled Day).
    """
    if board == BOARD_PROJECTS:
        return boards.JOBCHECK_FIELDGUIDE_ANCHORS.get(column_id)
    if board == BOARD_OPS:
        return boards.JOBCHECK_OPS_FIELDGUIDE_ANCHORS.get(column_id)
    return None


def parse_value_key(key: str) -> tuple[str, str]:
    """
    Split a submitted values key into (board, col_id).
    Bare column ids default to the Ops board (backward compatible).
    """
    key = str(key or "").strip()
    if key.startswith("projects:"):
        return BOARD_PROJECTS, key.split(":", 1)[1]
    if key.startswith("ops:"):
        return BOARD_OPS, key.split(":", 1)[1]
    return BOARD_OPS, key


def shape_value(render_type: str, raw: Any) -> Any:
    """
    PURE. One UI value → the Monday API column value (the JSON dict/string
    change_multiple_column_values expects). Empty/None input means "clear
    the column" and maps to Monday's null. Raises ValueError with a
    human-readable message when the value can't be shaped.
    """
    is_empty = raw is None or (isinstance(raw, str) and not raw.strip())

    if render_type == "status":
        if is_empty:
            return None
        if not isinstance(raw, str):
            raise ValueError("Status value must be a label string.")
        return {"label": raw.strip()}

    if render_type == "checkbox":
        if is_empty:
            return None
        if isinstance(raw, bool):
            checked = raw
        elif isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
            checked = raw.strip().lower() == "true"
        else:
            raise ValueError("Checkbox value must be true or false.")
        return {"checked": True} if checked else None

    if render_type == "date":
        if is_empty:
            return None
        if not isinstance(raw, str):
            raise ValueError("Date must be a YYYY-MM-DD string.")
        text = raw.strip()
        try:
            _date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"Not a valid date (need YYYY-MM-DD): {text!r}")
        return {"date": text}

    if render_type == "number":
        if is_empty:
            return ""            # empty string clears a numbers column
        try:
            num = float(str(raw).strip().replace(",", ""))
        except ValueError:
            raise ValueError(f"Not a number: {raw!r}")
        return str(int(num)) if num == int(num) else str(num)

    if render_type in ("text", "long_text"):
        text = "" if is_empty else str(raw).strip()
        if len(text) > MAX_TEXT_LEN:
            raise ValueError(f"Text too long ({len(text)} chars; "
                             f"max {MAX_TEXT_LEN}).")
        return {"text": text} if render_type == "long_text" else text

    raise ValueError(f"Unsupported column type: {render_type!r}")


def validate_values(values: dict, *,
                    status_labels: Optional[dict[str, list[str]]] = None,
                    ) -> tuple[dict[str, Any], dict[str, str], dict[str, dict]]:
    """
    PURE. The UI's {key: raw} dict → (shaped, errors, accepted).
      shaped   — {key: Monday API value} for every accepted column
      errors   — {key: message} for every rejected column
      accepted — {key: allowlist entry} (label/type/board) for logging
    Keys may be bare Ops column ids (backward compatible) or board-scoped
    `ops:<id>` / `projects:<id>` field keys. status_labels is looked up by
    the same key (and, for bare Ops ids, by the bare id).
    Rejections: column not on the effective allowlist (incl. anything
    hard-excluded), a status label the board doesn't have (when
    `status_labels` provides the board's real set), or an unshapeable value.
    """
    ops_allowed = {c["id"]: c for c in allowlisted_columns()}
    proj_allowed = {c["id"]: c for c in allowlisted_projects_trade_columns()}
    shaped: dict[str, Any] = {}
    errors: dict[str, str] = {}
    accepted: dict[str, dict] = {}
    for raw_key, raw in (values or {}).items():
        key = str(raw_key).strip()
        board, col_id = parse_value_key(key)
        # Preserve the submitted key shape in outputs (bare vs prefixed).
        out_key = key
        if board == BOARD_PROJECTS:
            entry = proj_allowed.get(col_id)
        else:
            entry = ops_allowed.get(col_id)
        if entry is None:
            errors[out_key] = "Column is not on the Job Check allowlist."
            continue
        try:
            api_value = shape_value(entry["type"], raw)
        except ValueError as e:
            errors[out_key] = str(e)
            continue
        if (entry["type"] == "status" and isinstance(api_value, dict)
                and status_labels is not None):
            # Only enforce labels when this board/column was actually fetched.
            # Missing Projects link means trade labels were never loaded — the
            # save path reports the link failure instead of a fake empty set.
            if (out_key in status_labels
                    or field_key(board, col_id) in status_labels
                    or (board == BOARD_OPS and col_id in status_labels)):
                known = (status_labels.get(out_key)
                         or status_labels.get(field_key(board, col_id))
                         or status_labels.get(col_id)
                         or [])
                if api_value["label"] not in known:
                    errors[out_key] = (f"'{api_value['label']}' is not a label on "
                                       f"this board column.")
                    continue
        shaped[out_key] = api_value
        accepted[out_key] = entry
    return shaped, errors, accepted


def describe_changes(old: dict, new: dict, columns: dict[str, dict]) -> str:
    """PURE. Old/new {col_id: text} → one flat 'Label: old → new; …' string
    for the activity log (flat scalars only — shared/activity.py's rule)."""
    parts = []
    for col_id, entry in columns.items():
        before = old.get(col_id) or "(empty)"
        after = new.get(col_id) or "(empty)"
        parts.append(f"{entry.get('label', col_id)}: {before} → {after}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Flows (Monday I/O via adapters/monday/jobcheck.py)
# ---------------------------------------------------------------------------

def list_active_jobs() -> dict:
    """Dropdown payload: every active Projects-board job, A→Z by name."""
    jobs = mj.fetch_active_jobs(MondayClient())
    jobs.sort(key=lambda j: j["name"].lower())
    return {"ok": True, "count": len(jobs), "jobs": jobs}


def get_job_detail(item_id: int) -> Optional[dict]:
    """
    Everything the form needs for one job: the read-only context header plus
    the allowlisted Ops columns and Projects trade-status columns with their
    current values (and, for status columns, each board's label+color set in
    tap-to-cycle order). Returns None when the Ops item doesn't exist.

    Each column is tagged with board="ops"|"projects" and a stable
    field_key. project_item_id is additive on the job payload (None when
    link_to_projects is empty — trade fields stay visible but disabled).
    """
    ops_cols = allowlisted_columns()
    trade_cols = allowlisted_projects_trade_columns()
    ops_ids = [c["id"] for c in ops_cols]
    trade_ids = [c["id"] for c in trade_cols]
    item_id = int(item_id)

    # Item values + Ops metadata + the Projects/GFolder chain are independent
    # Monday reads. Use separate sessions because requests.Session is not
    # thread-safe.
    mc_item, mc_meta, mc_gf = MondayClient(), MondayClient(), MondayClient()
    with ThreadPoolExecutor(max_workers=3) as pool:
        item_fut = pool.submit(mj.get_item_values, mc_item, item_id, ops_ids)
        meta_fut = pool.submit(mj.get_board_columns, mc_meta, ops_ids)
        gf_fut = pool.submit(
            mj.get_linked_project_gfolder, mc_gf, item_id)
        item = item_fut.result()
        meta = meta_fut.result()
        ginfo = gf_fut.result()
    if item is None:
        return None

    values = item["values"]
    project_item_id = ginfo.get("project_item_id")
    project_link_error = ginfo.get("error")

    trade_values: dict = {}
    trade_meta: dict = {}
    if project_item_id and trade_ids:
        mc_pitem, mc_pmeta = MondayClient(), MondayClient()
        with ThreadPoolExecutor(max_workers=2) as pool:
            pitem_fut = pool.submit(
                mj.get_item_values, mc_pitem, int(project_item_id), trade_ids)
            pmeta_fut = pool.submit(
                mj.get_board_columns, mc_pmeta, trade_ids,
                boards.PROJECTS_BOARD_ID)
            pitem = pitem_fut.result()
            trade_meta = pmeta_fut.result()
        if pitem is not None:
            trade_values = pitem.get("values") or {}

    form_columns = []
    for c in ops_cols:
        m = meta.get(c["id"]) or {}
        row = {"id": c["id"], "label": c["label"], "type": c["type"],
               "board": BOARD_OPS, "field_key": field_key(c),
               "value": values.get(c["id"]), "writable": True}
        if c["type"] == "status":
            row["labels"] = m.get("labels") or []
        anchor = fieldguide_anchor(c["id"], BOARD_OPS)
        if anchor:
            row["fieldguide_anchor"] = anchor
        form_columns.append(row)

    trade_writable = bool(project_item_id)
    for c in trade_cols:
        m = trade_meta.get(c["id"]) or {}
        row = {"id": c["id"], "label": c["label"], "type": c["type"],
               "board": BOARD_PROJECTS, "field_key": field_key(c),
               "value": trade_values.get(c["id"]) if trade_writable else None,
               "writable": trade_writable}
        if c["type"] == "status":
            row["labels"] = m.get("labels") or []
        anchor = fieldguide_anchor(c["id"], BOARD_PROJECTS)
        if anchor:
            row["fieldguide_anchor"] = anchor
        form_columns.append(row)

    photo_ready = mj.photo_ready_status(ginfo)
    return {
        "ok": True,
        "job": {
            "item_id": item["item_id"],
            "name": item["name"],
            "url": item["url"],
            "group": item["group_title"],
            # Operations-board context (2026-07-28) — named for what they now
            # actually hold, rather than reusing the old Projects-board keys.
            "project": values.get(mj.CONTEXT_COL_PROJECT_LINK),
            "location": values.get(mj.CONTEXT_COL_LOCATION),
            "ops_owner": values.get(mj.CONTEXT_COL_OPS_OWNER),
            "overdue": values.get(mj.CONTEXT_COL_OVERDUE),
            "project_status": values.get(mj.CONTEXT_COL_PROJECT_STATUS),
            "progress": values.get(mj.CONTEXT_COL_PROGRESS),
            "project_item_id": project_item_id,
            "project_link_error": project_link_error,
            "photo_ready": photo_ready.get("photo_ready"),
            "photo_ready_reason": photo_ready.get("photo_block_reason")
                or photo_ready.get("reason"),
            "gfolder_url": photo_ready.get("gfolder_url"),
            "gfolder": ginfo,
        },
        "columns": form_columns,
        "photo_ready": photo_ready,
    }


def save_job_check(item_id: int, values: dict, actor: str) -> dict:
    """
    THE save: validate against the board-scoped allowlists (hard exclusions
    re-checked), write Ops columns to the selected Operations item and
    Projects trade columns to the linked Projects item, re-read both, log
    who/item/columns old→new to the activity store, and return the confirmed
    values plus per-column failures. No silent partial writes — anything that
    didn't land is named in `failures`.

    Missing link_to_projects → clear per-column failure for Projects fields;
    Ops fields still save.

    Returns {ok, item_id, project_item_id, written: [field_key...],
             failures: {field_key: message}, confirmed: {field_key|col_id: text}}.
    `ok` is True only when every submitted column was written.
    """
    item_id = int(item_id)
    ops_cols = allowlisted_columns()
    trade_cols = allowlisted_projects_trade_columns()
    ops_ids = [c["id"] for c in ops_cols]
    trade_ids = [c["id"] for c in trade_cols]
    all_cols = ops_cols + trade_cols
    mc = MondayClient()

    # Snapshot BEFORE the write (also proves the Ops item exists), resolve the
    # linked Projects item, and pull each board's real status labels.
    before = mj.get_item_values(mc, item_id, ops_ids)
    if before is None:
        return {"ok": False, "item_id": item_id, "project_item_id": None,
                "written": [],
                "failures": {"_item": f"Monday item {item_id} not found."},
                "confirmed": {}}
    link = mj.get_linked_project_id(mc, item_id)
    project_item_id = link.get("project_item_id")

    ops_meta = mj.get_board_columns(mc, ops_ids)
    status_labels: dict[str, list[str]] = {}
    for cid, m in ops_meta.items():
        if m.get("type") == "status":
            labels = [l["label"] for l in (m.get("labels") or [])]
            status_labels[cid] = labels
            status_labels[field_key(BOARD_OPS, cid)] = labels

    before_trade: dict = {}
    if project_item_id and trade_ids:
        trade_meta = mj.get_board_columns(mc, trade_ids, boards.PROJECTS_BOARD_ID)
        for cid, m in trade_meta.items():
            if m.get("type") == "status":
                status_labels[field_key(BOARD_PROJECTS, cid)] = [
                    l["label"] for l in (m.get("labels") or [])
                ]
        pbefore = mj.get_item_values(mc, int(project_item_id), trade_ids)
        if pbefore is not None:
            before_trade = pbefore.get("values") or {}

    shaped, errors, accepted = validate_values(values,
                                               status_labels=status_labels)
    failures: dict[str, str] = dict(errors)
    written: list[str] = []

    # Split shaped values by board; map back to bare Monday column ids for the
    # mutation, but keep field keys in written/failures for the UI.
    ops_shaped: dict[str, Any] = {}
    proj_shaped: dict[str, Any] = {}
    ops_key_by_col: dict[str, str] = {}
    proj_key_by_col: dict[str, str] = {}
    for key, api_value in shaped.items():
        board, col_id = parse_value_key(key)
        if board == BOARD_PROJECTS:
            proj_shaped[col_id] = api_value
            proj_key_by_col[col_id] = field_key(BOARD_PROJECTS, col_id)
        else:
            ops_shaped[col_id] = api_value
            ops_key_by_col[col_id] = key  # preserve bare vs ops: prefix

    if ops_shaped:
        result = mj.set_item_columns(mc, item_id, ops_shaped)
        for cid in result["written"]:
            written.append(ops_key_by_col.get(cid, cid))
        for cid, msg in result["failed"].items():
            failures[ops_key_by_col.get(cid, cid)] = msg

    if proj_shaped:
        if not project_item_id:
            msg = (link.get("error") or
                   "No linked Projects item (link_to_projects is empty). "
                   "Ops fields can still save; use “Link a Projects item” "
                   "on this page to edit trade status.")
            for cid in proj_shaped:
                failures[field_key(BOARD_PROJECTS, cid)] = msg
        else:
            result = mj.set_item_columns(
                mc, int(project_item_id), proj_shaped,
                board_id=boards.PROJECTS_BOARD_ID)
            for cid in result["written"]:
                written.append(proj_key_by_col.get(
                    cid, field_key(BOARD_PROJECTS, cid)))
            for cid, msg in result["failed"].items():
                failures[proj_key_by_col.get(
                    cid, field_key(BOARD_PROJECTS, cid))] = msg

    # Re-read → confirmed values (the form re-renders from these, so what
    # the crew sees after Save is what Monday actually holds).
    after = mj.get_item_values(mc, item_id, ops_ids) or before
    confirmed: dict[str, Any] = {cid: after["values"].get(cid) for cid in ops_ids}
    for cid in ops_ids:
        confirmed[field_key(BOARD_OPS, cid)] = after["values"].get(cid)

    after_trade = before_trade
    if project_item_id and trade_ids:
        pafter = mj.get_item_values(mc, int(project_item_id), trade_ids)
        if pafter is not None:
            after_trade = pafter.get("values") or {}
    for cid in trade_ids:
        confirmed[field_key(BOARD_PROJECTS, cid)] = after_trade.get(cid)

    # Audit trail: describe Ops + Projects changes with board-scoped labels.
    changed_ops = {}
    changed_proj = {}
    for key in written:
        entry = accepted.get(key) or accepted.get(
            field_key(*parse_value_key(key)))
        if not entry:
            continue
        if entry.get("board") == BOARD_PROJECTS:
            changed_proj[entry["id"]] = entry
        else:
            changed_ops[entry["id"]] = entry
    change_bits = []
    if changed_ops:
        change_bits.append(describe_changes(
            before["values"], after["values"], changed_ops))
    if changed_proj:
        change_bits.append(describe_changes(
            before_trade, after_trade, changed_proj))
    changes_text = "; ".join(b for b in change_bits if b) or "no changes"

    activity.log_event(
        "jobcheck.save",
        actor=actor,
        target=str(item_id),
        result="ok" if not failures else ("partial" if written else "error"),
        severity="INFO" if not failures else "WARNING",
        job=before["name"],
        columns=",".join(written) or "none",
        changes=changes_text,
        failed=",".join(sorted(failures)) or None,
        project_item_id=str(project_item_id) if project_item_id else None,
    )
    if failures:
        print(f"[jobcheck] partial save on item {item_id}: "
              f"{failures}", file=sys.stderr)

    # Tell ops in Slack. Best-effort by contract: the Monday write has already
    # landed and the crew has already seen "saved", so a Slack problem must
    # never turn a successful save into an error. Silent when the channel isn't
    # configured. Skipped when nothing was actually written (a pure-failure
    # save has nothing to announce).
    slack_status = None
    if written:
        try:
            slack_changes = []
            for key in written:
                board, cid = parse_value_key(key)
                label = next((c["label"] for c in all_cols
                              if c["id"] == cid and c["board"] == board), cid)
                if board == BOARD_PROJECTS:
                    old_v, new_v = before_trade.get(cid), after_trade.get(cid)
                else:
                    old_v = before["values"].get(cid)
                    new_v = after["values"].get(cid)
                slack_changes.append({"label": label, "old": old_v, "new": new_v})
            posted = slack_notify.notify_jobcheck_saved({
                "job": before["name"],
                "actor": actor,
                "url": before.get("url"),
                "changes": slack_changes,
            })
            slack_status = "posted" if posted else "skipped"
        except slack_notify.SlackNotConfigured:
            slack_status = "skipped — no #operations channel configured"
        except Exception as e:  # noqa: BLE001 — a notice never breaks a save
            slack_status = f"FAILED — {type(e).__name__}: {e}"
            print(f"[jobcheck] Slack notice failed (non-fatal): {e}",
                  file=sys.stderr)

    return {"ok": not failures, "item_id": item_id,
            "project_item_id": project_item_id,
            "written": written, "failures": failures, "confirmed": confirmed,
            "slack": slack_status}


# Full Completion date on Ops — UI soft-gate for Ready to Invoice (not required
# server-side; crew can confirm past an empty date).
FULL_COMPLETION_COL = "date_mm1ghszy"


def mark_ready_to_invoice(item_id: int, actor_email: str) -> dict[str, Any]:
    """
    Explicit tap: move this Operations item into Ready to Invoice so Billing
    Hub's Ready queue picks it up. NEVER auto-fired from save_job_check.

    Validates the Ops item exists and is not already in that group. Missing
    link_to_projects is a warning only (Billing can still find the row by
    name). Stamps Ready for Invoice Date via the dedicated adapter path.
    """
    item_id = int(item_id)
    actor = (actor_email or "").strip() or "unknown"
    mc = MondayClient()

    before = mj.get_item_values(mc, item_id, [FULL_COMPLETION_COL])
    if before is None:
        return {
            "ok": False,
            "item_id": item_id,
            "error": "ITEM_NOT_FOUND",
            "detail": f"Monday item {item_id} not found.",
            "monday_url": None,
            "billing_href": "/ui/billing",
            "warnings": [],
        }

    if (before.get("group_id") or "") == mj.READY_TO_INVOICE_GROUP_ID:
        return {
            "ok": False,
            "item_id": item_id,
            "error": "ALREADY_READY",
            "detail": ("This job is already in Ready to Invoice — "
                       "it should appear on Billing Hub."),
            "monday_url": before.get("url"),
            "billing_href": "/ui/billing",
            "warnings": [],
            "group_id": before.get("group_id"),
            "group_title": before.get("group_title"),
        }

    warnings: list[str] = []
    link = mj.get_linked_project_id(mc, item_id)
    project_item_id = link.get("project_item_id")
    if not project_item_id:
        warnings.append(
            "No linked Projects item (link_to_projects is empty). "
            "Moved anyway — Billing Hub may need the link for Project #."
        )

    full_done = bool((before.get("values") or {}).get(FULL_COMPLETION_COL))
    if not full_done:
        warnings.append(
            "Full Completion date is not set — marked ready anyway after confirm."
        )

    moved = mj.move_ops_item_to_ready_to_invoice(
        mc, item_id, current_group_id=before.get("group_id"))
    if not moved.get("ok"):
        activity.log_event(
            "jobcheck.ready_to_invoice",
            actor=actor,
            target=str(item_id),
            result="error",
            severity="WARNING",
            job=before.get("name"),
            error=moved.get("error"),
        )
        return {
            "ok": False,
            "item_id": item_id,
            "error": "MOVE_FAILED",
            "detail": moved.get("error") or "Couldn't move the item in Monday.",
            "monday_url": before.get("url"),
            "billing_href": "/ui/billing",
            "warnings": warnings,
        }

    activity.log_event(
        "jobcheck.ready_to_invoice",
        actor=actor,
        target=str(item_id),
        result="ok",
        job=before.get("name"),
        group_moved=str(bool(moved.get("group_moved"))),
        date_written=str(bool(moved.get("date_written"))),
        ready_date=moved.get("ready_date"),
        project_item_id=str(project_item_id) if project_item_id else None,
        warnings="; ".join(warnings) or None,
        date_error=moved.get("date_error"),
    )

    return {
        "ok": True,
        "item_id": item_id,
        "job": before.get("name"),
        "monday_url": before.get("url"),
        "billing_href": "/ui/billing",
        "group_moved": bool(moved.get("group_moved")),
        "date_written": bool(moved.get("date_written")),
        "ready_date": moved.get("ready_date"),
        "date_error": moved.get("date_error"),
        "project_item_id": project_item_id,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Monday updates + Drive photos (Operations item; never auto-changes Stage)
# ---------------------------------------------------------------------------

def suggest_project_links(ops_item_id: int, *, limit: int = 8) -> dict[str, Any]:
    """
    Read-only heuristic Ops→Projects link suggestions for Job Check.
    Reuses the same name-matching scorer as the Morning Brief (link_suggest +
    fetch_active_projects). NEVER writes Monday.
    """
    item_id = int(ops_item_id)
    limit = max(1, min(int(limit or 8), 100))
    mc = MondayClient()

    item = mj.get_item_values(mc, item_id, [])
    if item is None:
        return {
            "ok": False,
            "item_id": item_id,
            "error": "ITEM_NOT_FOUND",
            "detail": f"Monday item {item_id} not found.",
        }

    ops_name = item.get("name") or ""
    link = mj.get_linked_project_id(mc, item_id)
    projects = ml.fetch_active_projects(mc)[:limit]
    project_candidates = [
        {"id": p["item_id"], "name": p["name"], "url": p.get("url")}
        for p in projects
    ]
    project_sug = ls.suggest_project_for_ops(ops_name, project_candidates)
    ranked = sorted(
        ({**c, "score": round(ls.score_name_match(ops_name, c.get("name") or ""), 3)}
         for c in project_candidates),
        key=lambda c: c["score"], reverse=True,
    )[:limit]

    return {
        "ok": True,
        "item_id": item_id,
        "ops_name": ops_name,
        "already_linked": link.get("project_item_id"),
        "project": project_sug,
        "suggestions": ranked,
        "candidates_scanned": len(project_candidates),
    }


def link_project(ops_item_id: int, projects_item_id: int,
                 actor: str) -> dict[str, Any]:
    """
    Fill-if-empty link from Operations link_to_projects → one Projects item.
    Reuses set_ops_project_link_if_empty (never overwrites). Re-fetches job
    detail on success so trade statuses become editable immediately.
    """
    ops_item_id = int(ops_item_id)
    projects_item_id = int(projects_item_id)
    mc = MondayClient()

    before = mj.get_item_values(mc, ops_item_id, [])
    if before is None:
        return {
            "ok": False,
            "linked": False,
            "already": False,
            "detail": None,
            "error": f"Operations item {ops_item_id} not found.",
        }

    result = mj.set_ops_project_link_if_empty(
        mc, ops_item_id, projects_item_id)
    linked = bool(result.get("written"))
    already = bool(result.get("skipped") and result.get("reason") == "already_set")

    activity.log_event(
        "jobcheck.link_project",
        actor=actor,
        target=str(ops_item_id),
        result="ok" if result.get("ok") else "error",
        severity="INFO" if result.get("ok") else "WARNING",
        job=before["name"],
        projects_item_id=str(projects_item_id),
        written=str(linked),
        already=str(already),
        reason=result.get("reason") or result.get("error"),
    )

    detail = get_job_detail(ops_item_id) if result.get("ok") else None
    return {
        "ok": bool(result.get("ok")),
        "linked": linked,
        "already": already,
        "detail": detail,
        "project_item_id": result.get("project_item_id"),
        "error": result.get("error"),
        "reason": result.get("reason"),
    }


def list_updates(item_id: int, *, limit: int = 25) -> dict:
    """Recent Monday updates on the Ops item for the Job Check Updates card."""
    item_id = int(item_id)
    mc = MondayClient()
    # Prove the item exists (same board the form edits).
    before = mj.get_item_values(mc, item_id, [])
    if before is None:
        return {"ok": False, "item_id": item_id, "error": "ITEM_NOT_FOUND",
                "detail": f"Monday item {item_id} not found.", "updates": []}
    updates = mj.list_item_updates(mc, item_id, limit=limit)
    return {"ok": True, "item_id": item_id, "job": before["name"],
            "count": len(updates), "updates": updates}


def post_update(item_id: int, text: str, actor: str) -> dict:
    """
    Post a Monday update on the Operations item. Pure validation first
    (empty/over-long rejected); never changes Stage or any column.
    """
    item_id = int(item_id)
    body = (text or "").strip()
    if not body:
        return {"ok": False, "item_id": item_id, "error": "EMPTY_UPDATE",
                "detail": "Update text is required."}
    if len(body) > MAX_UPDATE_LEN:
        return {"ok": False, "item_id": item_id, "error": "UPDATE_TOO_LONG",
                "detail": f"Update too long ({len(body)} chars; max {MAX_UPDATE_LEN})."}

    mc = MondayClient()
    before = mj.get_item_values(mc, item_id, [])
    if before is None:
        return {"ok": False, "item_id": item_id, "error": "ITEM_NOT_FOUND",
                "detail": f"Monday item {item_id} not found."}

    # Tag the poster so Monday's update stream shows who wrote it from the portal.
    who = (actor or "").split("@")[0] or "portal"
    full = f"Job Check update ({who}):\n{body}"
    upd = mj.create_item_update(mc, item_id, full)
    activity.log_event(
        "jobcheck.update",
        actor=actor,
        target=str(item_id),
        result="ok",
        job=before["name"],
        chars=len(body),
        update_id=str((upd or {}).get("id") or "") or None,
    )
    return {"ok": True, "item_id": item_id, "update": upd, "body": full}


def upload_photos(item_id: int, files: list, actor: str,
                  note: Optional[str] = None) -> dict:
    """
    Upload photo files to the project's Drive Pictures folder, then post a
    Monday update on the Operations item with the note + Drive links.

    Chain: Ops item → linked Projects item → GFolder Link → Pictures
    (create Pictures if missing). Clear error if GFolder is missing/unshared.
    Does NOT change Stage.
    """
    item_id = int(item_id)
    files = list(files or [])
    if not files:
        return {"ok": False, "item_id": item_id, "error": "NO_FILES",
                "detail": "Choose at least one photo to upload."}

    mc = MondayClient()
    before = mj.get_item_values(mc, item_id, [])
    if before is None:
        return {"ok": False, "item_id": item_id, "error": "ITEM_NOT_FOUND",
                "detail": f"Monday item {item_id} not found."}

    ginfo = mj.get_linked_project_gfolder(mc, item_id)
    if not ginfo.get("folder_id"):
        return {
            "ok": False,
            "item_id": item_id,
            "error": "GFOLDER_MISSING",
            "detail": ginfo.get("error") or "No GFolder Link for this job.",
            "advice": ("Open the linked Projects item in Monday and paste the "
                       "project Drive folder URL into GFolder Link. The portal "
                       "uploads into that folder's Pictures subfolder — not "
                       "Jake Completed Plans."),
            "gfolder": ginfo,
        }

    try:
        drive = DriveUploader()
        pictures = drive.resolve_project_pictures_folder(ginfo["folder_id"])
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "item_id": item_id,
            "error": "DRIVE_UNAVAILABLE",
            "detail": (f"Couldn't open the project Drive folder "
                       f"({type(e).__name__}: {e}). Is the folder shared with "
                       f"the portal service account?"),
            "advice": ("Ask an admin to share the project folder with the Drive "
                       "service account, then retry."),
            "gfolder": ginfo,
        }

    uploaded: list[dict] = []
    failures: list[str] = []
    for entry in files:
        # Accept (filename, bytes, mimetype) tuples or dicts.
        if isinstance(entry, dict):
            filename = entry.get("filename") or "photo.jpg"
            data = entry.get("data") or b""
            mimetype = entry.get("mimetype") or "image/jpeg"
        else:
            filename, data, mimetype = entry[0], entry[1], (
                entry[2] if len(entry) > 2 else "image/jpeg")
        filename = _Path(str(filename)).name or "photo.jpg"
        if not data:
            failures.append(f"{filename}: empty file")
            continue
        try:
            result = drive.upload_or_replace_file(
                folder_id=pictures["folder_id"],
                filename=filename,
                data=data,
                mimetype=mimetype or "image/jpeg",
            )
            uploaded.append(result)
        except Exception as e:  # noqa: BLE001
            failures.append(f"{filename}: {type(e).__name__}: {e}")

    if not uploaded:
        return {
            "ok": False,
            "item_id": item_id,
            "error": "UPLOAD_FAILED",
            "detail": "No photos uploaded. " + "; ".join(failures[:5]),
            "failures": failures,
            "gfolder": ginfo,
            "pictures": pictures,
        }

    who = (actor or "").split("@")[0] or "portal"
    note_text = (note or "").strip()
    lines = [f"Job Check photos ({who})"]
    if note_text:
        lines.append(note_text)
    for u in uploaded:
        link = u.get("web_view_link") or ""
        name = u.get("filename") or "photo"
        lines.append(f"{name}: {link}" if link else name)
    body = "\n".join(lines)
    upd = mj.create_item_update(mc, item_id, body)

    activity.log_event(
        "jobcheck.photo",
        actor=actor,
        target=str(item_id),
        result="ok" if not failures else "partial",
        severity="INFO" if not failures else "WARNING",
        job=before["name"],
        files=len(uploaded),
        failed=len(failures),
        pictures_folder=pictures.get("folder_id"),
        update_id=str((upd or {}).get("id") or "") or None,
    )
    return {
        "ok": not failures,
        "item_id": item_id,
        "uploaded": uploaded,
        "failures": failures,
        "monday_update": upd,
        "body": body,
        "gfolder": ginfo,
        "pictures": pictures,
    }

