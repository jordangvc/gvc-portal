"""
Rename existing Drive job folders to the standard Projects job name.

Missing city/state/ZIP is enriched from Monday location JSON, linked Bid
locations, and (when still needed) Nominatim before the folder target is
planned.

Safety:
  - Dry-run by default. ``--apply`` is required to write, and ``--dry-run``
    wins if both flags are supplied.
  - Reads only the Projects board's existing GFolder Link. It never searches
    or traverses Jake's Completed Plans tree.
  - Renames folders in place through Drive ``files.update``. It never creates,
    moves, copies, or deletes a folder, so the folder ID and links survive.
  - Only planner decisions with action ``rename`` are eligible.

Examples (run from the repository root):

  .venv/bin/python scripts/backfill_job_rename_drive.py --dry-run
  .venv/bin/python scripts/backfill_job_rename_drive.py --apply --limit 20
  .venv/bin/python scripts/backfill_job_rename_drive.py --no-geocode --limit 20
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.drive import (  # noqa: E402
    DriveNotConfigured,
    DriveUploader,
    folder_id_from_url,
    slug_for_path,
)
from adapters.monday.client import MondayClient  # noqa: E402
from adapters.monday.search import P_COL_BUILDER as PROJECT_BUILDER_COL  # noqa: E402
from adapters.monday.search import P_COL_LOCATION as PROJECT_LOCATION_COL  # noqa: E402
from shared.boards import (  # noqa: E402
    JOBSTART_BID_LOCATION_COL,
    JOBSTART_P_COL_CUSTOMER as PROJECT_CUSTOMER_COL,
    JOBSTART_P_COL_OPPORTUNITY as PROJECT_OPPORTUNITY_COL,
    PROJECTS_BOARD_ID,
    PROJECTS_GFOLDER_COL,
)
from subsystems.jobstart import location_lookup, rename_enrich, rename_plan  # noqa: E402


RENAME_SLEEP_SECONDS = 0.25
_READ_COLUMNS = (
    PROJECT_LOCATION_COL,
    PROJECT_BUILDER_COL,
    PROJECT_CUSTOMER_COL,
    PROJECTS_GFOLDER_COL,
    PROJECT_OPPORTUNITY_COL,
)


def _column_text(column: Optional[dict]) -> str:
    """Best available text for ordinary and board-relation columns."""
    if not column:
        return ""
    for key in ("display_value", "text"):
        value = column.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _link_url(column: Optional[dict]) -> str:
    """URL from Monday's typed LinkValue, with its JSON value as fallback."""
    if not column:
        return ""
    url = (column.get("url") or "").strip()
    if url:
        return url
    try:
        value = json.loads(column.get("value") or "{}")
    except (json.JSONDecodeError, TypeError):
        value = {}
    return str(value.get("url") or "").strip()


def _linked_ids(column: Optional[dict]) -> list[int]:
    """Read Monday BoardRelation ids from raw value JSON."""
    if not column:
        return []
    direct_ids = column.get("linked_item_ids") or []
    if direct_ids:
        return [int(item_id) for item_id in direct_ids if item_id]
    try:
        value = json.loads(column.get("value") or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    return [
        int(entry["linkedPulseId"])
        for entry in (value.get("linkedPulseIds") or [])
        if entry.get("linkedPulseId")
    ]


def shape_project_row(item: dict) -> dict:
    """Map one Projects board item into planner inputs."""
    columns = {
        column["id"]: column
        for column in (item.get("column_values") or [])
        if column.get("id")
    }
    group = item.get("group") or {}
    location_column = columns.get(PROJECT_LOCATION_COL) or {}
    return {
        "item_id": int(item["id"]),
        "name": (item.get("name") or "").strip(),
        "group_title": (group.get("title") or "").strip(),
        "location": _column_text(location_column),
        "location_value_json": location_column.get("value") or "",
        "location_column": location_column,
        "builder": _column_text(columns.get(PROJECT_BUILDER_COL)),
        "customer": _column_text(columns.get(PROJECT_CUSTOMER_COL)),
        "gfolder_url": _link_url(columns.get(PROJECTS_GFOLDER_COL)),
        "linked_bid_ids": _linked_ids(
            columns.get(PROJECT_OPPORTUNITY_COL)
        ),
    }


def list_projects_with_gfolder(
    mc,
    *,
    limit: Optional[int] = None,
) -> list[dict]:
    """Page Projects and return rows with a non-empty existing GFolder Link."""
    query = """
    query ($boardId: [ID!], $cursor: String, $cols: [String!]) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            name
            group { id title }
            column_values(ids: $cols) {
              id
              text
              value
              ... on LinkValue { url text }
              ... on BoardRelationValue { display_value linked_item_ids }
            }
          }
        }
      }
    }
    """
    rows: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {
            "boardId": [str(PROJECTS_BOARD_ID)],
            "cursor": cursor,
            "cols": list(_READ_COLUMNS),
        })
        boards = data.get("boards") or []
        if not boards:
            break
        page = boards[0].get("items_page") or {}
        for item in page.get("items") or []:
            row = shape_project_row(item)
            if not row["gfolder_url"]:
                continue
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                return rows
        cursor = page.get("cursor")
        if not cursor:
            break
    return rows


def fetch_bid_location_hints(mc, bid_ids: list[int]) -> dict[int, str]:
    """Batch-read linked Bid location text/value as recorded address hints."""
    if not bid_ids:
        return {}
    query = """
    query ($ids: [ID!], $cols: [String!]) {
      items(ids: $ids) {
        id
        column_values(ids: $cols) { id text value }
      }
    }
    """
    hints: dict[int, str] = {}
    for start in range(0, len(bid_ids), 50):
        chunk = bid_ids[start:start + 50]
        data = mc._query(query, {
            "ids": [str(item_id) for item_id in chunk],
            "cols": [JOBSTART_BID_LOCATION_COL],
        })
        for item in data.get("items") or []:
            location_column = next(
                (
                    column
                    for column in (item.get("column_values") or [])
                    if column.get("id") == JOBSTART_BID_LOCATION_COL
                ),
                {},
            )
            hint = location_lookup.location_from_column_value(
                location_column
            ).get("hint")
            if hint:
                hints[int(item["id"])] = hint
    return hints


def drive_names_match(old_name: str, new_name: str) -> bool:
    """Whether two titles collapse to the same Drive-safe folder name."""
    return slug_for_path(old_name) == slug_for_path(new_name)


def plan_drive_rename(
    row: dict,
    *,
    bid_hints: Optional[dict[int, str]] = None,
    geocode: bool = True,
    geocode_street_fn=None,
    reverse_geocode_fn=None,
) -> dict:
    """Pure Projects row -> planner decision plus Drive-safe folder target."""
    extra_hints = [
        (bid_hints or {}).get(int(bid_id))
        for bid_id in (row.get("linked_bid_ids") or [])
        if (bid_hints or {}).get(int(bid_id))
    ]
    planned = rename_enrich.plan_enriched_row(
        name=row.get("name") or "",
        location_text=row.get("location") or None,
        location_value_json=row.get("location_value_json") or None,
        location_column=row.get("location_column"),
        extra_hints=extra_hints,
        builder=row.get("builder") or None,
        customer=row.get("customer") or None,
        item_id=row.get("item_id"),
        board="projects",
        gfolder_url=row.get("gfolder_url") or None,
        geocode=geocode and not rename_plan.is_co_item_name(
            row.get("name") or ""
        ),
        geocode_street_fn=geocode_street_fn,
        reverse_geocode_fn=reverse_geocode_fn,
    )
    if rename_plan.is_co_item_name(row.get("name") or ""):
        planned = {
            **planned,
            "action": "skip_co",
            "ok": False,
            "note": (
                "CO row shares its parent GFolder; never rename that folder "
                "to a CO title."
            ),
        }
    old_slug = slug_for_path(planned.get("old_name") or "")
    new_slug = slug_for_path(planned.get("new_name") or "")
    folder_id = folder_id_from_url(row.get("gfolder_url") or "")
    out = {
        **planned,
        "folder_id": folder_id,
        "old_slug": old_slug,
        "new_slug": new_slug,
    }
    if planned.get("action") != "rename":
        return out
    if not folder_id:
        return {
            **out,
            "action": "skip_folder",
            "ok": False,
            "note": "GFolder Link does not contain a resolvable Drive folder ID.",
        }
    if drive_names_match(planned.get("old_name") or "",
                         planned.get("new_name") or ""):
        return {
            **out,
            "action": "skip_drive_standard",
            "ok": True,
            "note": "Already matches the Drive-safe folder name.",
        }
    return out


def should_apply(*, apply: bool, dry_run: bool) -> bool:
    """Safety switch: explicit dry-run always wins over apply."""
    return bool(apply) and not bool(dry_run)


def run(*, apply: bool, limit: Optional[int], geocode: bool = True) -> int:
    """Plan every selected row and optionally rename eligible Drive folders."""
    mode = "APPLY" if apply else "DRY-RUN"
    print(
        f"[drive-rename] mode={mode} limit={limit or 'none'} "
        f"geocode={'on' if geocode else 'off'}",
        file=sys.stderr,
    )
    try:
        monday = MondayClient()
    except Exception as exc:  # noqa: BLE001
        print(f"Monday not configured: {exc}", file=sys.stderr)
        return 2

    drive = None
    if apply:
        try:
            drive = DriveUploader()
        except DriveNotConfigured as exc:
            print(f"Drive not configured: {exc}", file=sys.stderr)
            return 2

    rows = list_projects_with_gfolder(monday, limit=limit)
    bid_ids = sorted({
        int(bid_id)
        for row in rows
        for bid_id in (row.get("linked_bid_ids") or [])
    })
    bid_hints = fetch_bid_location_hints(monday, bid_ids)
    renamed = eligible = skipped = errors = 0
    for row in rows:
        decision = plan_drive_rename(
            row,
            bid_hints=bid_hints,
            geocode=geocode,
        )
        action = decision["action"]
        item_id = decision.get("item_id")
        old_slug = decision.get("old_slug") or decision.get("old_name") or ""
        new_slug = decision.get("new_slug") or decision.get("new_name") or ""
        if action != "rename":
            skipped += 1
            note = decision.get("note") or action
            print(f"SKIP    {item_id:<12} {old_slug!r} ({action}: {note})")
            continue

        eligible += 1
        folder_id = decision["folder_id"]
        if not apply:
            print(f"WOULD   {item_id:<12} {old_slug!r} -> {new_slug!r} "
                  f"(folder {folder_id})")
            continue
        try:
            result = drive.rename_file(folder_id, new_slug)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"ERROR   {item_id:<12} {old_slug!r} -> {new_slug!r}: "
                  f"{type(exc).__name__}: {exc}")
            continue
        renamed += 1
        print(f"RENAMED {item_id:<12} {old_slug!r} -> "
              f"{result.get('filename') or new_slug!r} "
              f"(folder {result.get('file_id') or folder_id})")
        time.sleep(RENAME_SLEEP_SECONDS)

    print(
        f"\n[drive-rename] done. rows={len(rows)} eligible={eligible} "
        f"renamed={renamed} skipped={skipped} errors={errors}"
        f"{'' if apply else ' (dry-run — pass --apply to write)'}",
        file=sys.stderr,
    )
    return 0 if errors == 0 else 1


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("--limit must be at least 1")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rename existing Projects GFolder targets in place. "
            "Dry-run by default; never creates folders or renames Monday items."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed Drive folder renames only (default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rename eligible Drive folders in place.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Cap Projects rows with a GFolder Link processed in this run.",
    )
    parser.add_argument(
        "--no-geocode",
        action="store_true",
        help="Use Monday/linked Bid facts only — do not call Nominatim.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    apply = should_apply(apply=args.apply, dry_run=args.dry_run)
    if args.apply and args.dry_run:
        print("[warn] both --apply and --dry-run set; staying in dry-run",
              file=sys.stderr)
    return run(
        apply=apply,
        limit=args.limit,
        geocode=not args.no_geocode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
