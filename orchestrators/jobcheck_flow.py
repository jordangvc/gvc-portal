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
from typing import Any, Optional

from shared import activity
from shared import boards

# Longest value the form may write to a text/long_text column. Generous for
# field notes, small enough that a runaway client can't stuff megabytes in.
MAX_TEXT_LEN = 4000
MAX_UPDATE_LEN = 4000


# ---------------------------------------------------------------------------
# Pure config + validation helpers (unit-tested without Monday)
# ---------------------------------------------------------------------------

def allowlisted_columns() -> list[dict]:
    """
    The effective allowlist: JOBCHECK_COLUMNS minus anything hard-excluded or
    declaring a render type the form doesn't support. This is THE gate config
    edits pass through — a money/link column added to the config never
    reaches the form or the validator's allow set.
    """
    out: list[dict] = []
    seen: set[str] = set()
    # Read via the module attribute (not a from-import) so the gate always
    # sees the CURRENT config — including edits made after import.
    for entry in boards.JOBCHECK_COLUMNS:
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
                    "type": rtype})
    return out


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
    PURE. The UI's {col_id: raw} dict → (shaped, errors, accepted).
      shaped   — {col_id: Monday API value} for every accepted column
      errors   — {col_id: message} for every rejected column
      accepted — {col_id: allowlist entry} (label/type) for logging
    Rejections: column not on the effective allowlist (incl. anything
    hard-excluded), a status label the board doesn't have (when
    `status_labels` provides the board's real set), or an unshapeable value.
    """
    allowed = {c["id"]: c for c in allowlisted_columns()}
    shaped: dict[str, Any] = {}
    errors: dict[str, str] = {}
    accepted: dict[str, dict] = {}
    for col_id, raw in (values or {}).items():
        col_id = str(col_id).strip()
        entry = allowed.get(col_id)
        if entry is None:
            errors[col_id] = "Column is not on the Job Check allowlist."
            continue
        try:
            api_value = shape_value(entry["type"], raw)
        except ValueError as e:
            errors[col_id] = str(e)
            continue
        if (entry["type"] == "status" and isinstance(api_value, dict)
                and status_labels is not None):
            known = status_labels.get(col_id) or []
            if api_value["label"] not in known:
                errors[col_id] = (f"'{api_value['label']}' is not a label on "
                                  f"this board column.")
                continue
        shaped[col_id] = api_value
        accepted[col_id] = entry
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
    from adapters.monday.client import MondayClient
    from adapters.monday import jobcheck as mj

    jobs = mj.fetch_active_jobs(MondayClient())
    jobs.sort(key=lambda j: j["name"].lower())
    return {"ok": True, "count": len(jobs), "jobs": jobs}


def get_job_detail(item_id: int) -> Optional[dict]:
    """
    Everything the form needs for one job: the read-only context header plus
    the allowlisted columns with their current values (and, for status
    columns, the board's label+color set in tap-to-cycle order). Returns
    None when the item doesn't exist.
    """
    from adapters.monday.client import MondayClient
    from adapters.monday import jobcheck as mj

    cols = allowlisted_columns()
    col_ids = [c["id"] for c in cols]
    # Item values + column metadata are independent Monday reads — overlap them
    # on separate sessions (requests.Session is not thread-safe).
    mc_item, mc_meta = MondayClient(), MondayClient()
    with ThreadPoolExecutor(max_workers=2) as pool:
        item_fut = pool.submit(mj.get_item_values, mc_item, int(item_id), col_ids)
        meta_fut = pool.submit(mj.get_board_columns, mc_meta, col_ids)
        item = item_fut.result()
        meta = meta_fut.result()
    if item is None:
        return None

    values = item["values"]
    form_columns = []
    for c in cols:
        m = meta.get(c["id"]) or {}
        row = {"id": c["id"], "label": c["label"], "type": c["type"],
               "value": values.get(c["id"])}
        if c["type"] == "status":
            row["labels"] = m.get("labels") or []
        form_columns.append(row)

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
        },
        "columns": form_columns,
    }


def save_job_check(item_id: int, values: dict, actor: str) -> dict:
    """
    THE save: validate against the allowlist (hard exclusions re-checked),
    write the accepted columns to the ONE selected item, re-read it, log
    who/item/columns old→new to the activity store, and return the confirmed
    values plus per-column failures. No silent partial writes — anything that
    didn't land is named in `failures`.

    Returns {ok, item_id, written: [col_id...],
             failures: {col_id: message}, confirmed: {col_id: text}}.
    `ok` is True only when every submitted column was written.
    """
    from adapters.monday.client import MondayClient
    from adapters.monday import jobcheck as mj

    item_id = int(item_id)
    cols = allowlisted_columns()
    col_ids = [c["id"] for c in cols]
    mc = MondayClient()

    # Snapshot BEFORE the write (also proves the item exists), and pull the
    # board's real status labels so the validator can check them.
    before = mj.get_item_values(mc, item_id, col_ids)
    if before is None:
        return {"ok": False, "item_id": item_id, "written": [],
                "failures": {"_item": f"Monday item {item_id} not found."},
                "confirmed": {}}
    meta = mj.get_board_columns(mc, col_ids)
    status_labels = {cid: [l["label"] for l in (m.get("labels") or [])]
                     for cid, m in meta.items() if m.get("type") == "status"}

    shaped, errors, accepted = validate_values(values,
                                               status_labels=status_labels)
    failures: dict[str, str] = dict(errors)
    written: list[str] = []
    if shaped:
        result = mj.set_item_columns(mc, item_id, shaped)
        written = result["written"]
        failures.update(result["failed"])

    # Re-read → confirmed values (the form re-renders from these, so what
    # the crew sees after Save is what Monday actually holds).
    after = mj.get_item_values(mc, item_id, col_ids) or before
    confirmed = {cid: after["values"].get(cid) for cid in col_ids}

    changed = {cid: accepted[cid] for cid in written if cid in accepted}
    activity.log_event(
        "jobcheck.save",
        actor=actor,
        target=str(item_id),
        result="ok" if not failures else ("partial" if written else "error"),
        severity="INFO" if not failures else "WARNING",
        job=before["name"],
        columns=",".join(written) or "none",
        changes=describe_changes(before["values"], after["values"], changed)
                or "no changes",
        failed=",".join(sorted(failures)) or None,
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
        from adapters import slack_notify
        try:
            posted = slack_notify.notify_jobcheck_saved({
                "job": before["name"],
                "actor": actor,
                "url": before.get("url"),
                "changes": [
                    {"label": next((c["label"] for c in cols if c["id"] == cid), cid),
                     "old": before["values"].get(cid),
                     "new": after["values"].get(cid)}
                    for cid in written
                ],
            })
            slack_status = "posted" if posted else "skipped"
        except slack_notify.SlackNotConfigured:
            slack_status = "skipped — no #operations channel configured"
        except Exception as e:  # noqa: BLE001 — a notice never breaks a save
            slack_status = f"FAILED — {type(e).__name__}: {e}"
            print(f"[jobcheck] Slack notice failed (non-fatal): {e}",
                  file=sys.stderr)

    return {"ok": not failures, "item_id": item_id, "written": written,
            "failures": failures, "confirmed": confirmed,
            "slack": slack_status}


# ---------------------------------------------------------------------------
# Monday updates + Drive photos (Operations item; never auto-changes Stage)
# ---------------------------------------------------------------------------

def list_updates(item_id: int, *, limit: int = 25) -> dict:
    """Recent Monday updates on the Ops item for the Job Check Updates card."""
    from adapters.monday.client import MondayClient
    from adapters.monday import jobcheck as mj

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

    from adapters.monday.client import MondayClient
    from adapters.monday import jobcheck as mj

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

    from pathlib import Path as _Path

    from adapters.drive import DriveUploader
    from adapters.monday.client import MondayClient
    from adapters.monday import jobcheck as mj

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

