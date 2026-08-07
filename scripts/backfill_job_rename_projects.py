"""
Plan or apply the GVC job-title standard on Monday's Projects board.

Target title:

    Street Number Name, City, ST ZIP | Builder | Job Title

Looks up city/state/ZIP from Monday location JSON, linked Bid location, and
(when still needed) OpenStreetMap Nominatim for the OH/IN/KY area. Job Title
comes from Projects status (Residential/Commercial) + Customer. CO rows
cascade from their parent title — they are not skipped.

Safety:
  * Dry-run by default. ``--apply`` is required for Monday writes.
  * If ``--apply`` and ``--dry-run`` are both present, dry-run wins.
  * Geocode only when recorded Monday facts are incomplete.
  * This script renames Monday item titles only. It never renames Drive folders.

Examples:

    .venv/bin/python scripts/backfill_job_rename_projects.py --limit 20
    .venv/bin/python scripts/backfill_job_rename_projects.py --apply --limit 20
    .venv/bin/python scripts/backfill_job_rename_projects.py --no-geocode --limit 20
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday.client import (  # noqa: E402
    COL_BUILDER,
    COL_PROJECT_TYPE_STATUS,
    MondayClient,
)
from adapters.monday.rename import rename_item_name  # noqa: E402
from shared.boards import (  # noqa: E402
    BID_BOARD_ID,
    JOBSTART_BID_LOCATION_COL,
    JOBSTART_P_COL_CUSTOMER,
    JOBSTART_P_COL_LOCATION,
    JOBSTART_P_COL_OPPORTUNITY,
    PROJECTS_BOARD_ID,
    PROJECTS_GFOLDER_COL,
)
from subsystems.jobstart import rename_enrich, rename_plan  # noqa: E402

WRITE_DELAY_SECONDS = 0.2
PROJECT_COLUMN_IDS = (
    JOBSTART_P_COL_LOCATION,
    COL_BUILDER,
    JOBSTART_P_COL_CUSTOMER,
    COL_PROJECT_TYPE_STATUS,
    PROJECTS_GFOLDER_COL,
    JOBSTART_P_COL_OPPORTUNITY,
)


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or apply Projects-board titles as "
            "'Street Number Name, City, ST ZIP | Builder | Job Title'. "
            "Looks up missing city/state/ZIP and Job Title hints. "
            "Dry-run is the default."
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the plan without writing (default; wins over --apply).",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Rename eligible Monday items.",
    )
    parser.add_argument(
        "--limit", type=_positive_int, default=None, metavar="N",
        help="Process at most N Projects items.",
    )
    parser.add_argument(
        "--no-geocode", action="store_true",
        help="Use Monday/linked facts only — do not call Nominatim.",
    )
    return parser


def should_apply(args: argparse.Namespace) -> bool:
    return bool(args.apply) and not bool(args.dry_run)


def _column_map(item: dict) -> dict[str, dict]:
    return {
        cv["id"]: cv
        for cv in (item.get("column_values") or [])
        if cv.get("id")
    }


def _column_text(columns: dict[str, dict], column_id: str) -> str:
    cv = columns.get(column_id) or {}
    for key in ("display_value", "text"):
        raw = cv.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def _gfolder_url(columns: dict[str, dict]) -> Optional[str]:
    column = columns.get(PROJECTS_GFOLDER_COL) or {}
    url = (column.get("url") or "").strip()
    if url:
        return url
    raw = column.get("value")
    if not raw:
        return None
    try:
        return (json.loads(raw) or {}).get("url") or None
    except (json.JSONDecodeError, TypeError):
        return None


def _linked_ids(cv: Optional[dict]) -> list[int]:
    if not cv:
        return []
    try:
        parsed = json.loads(cv.get("value") or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for entry in parsed.get("linkedPulseIds") or []:
        pid = entry.get("linkedPulseId")
        if pid:
            out.append(int(pid))
    return out


def fetch_bid_location_hints(mc, bid_ids: list[int]) -> dict[int, str]:
    """Batch-read Bid Board location5 text/value → hint strings."""
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
    # Monday caps ids per call — chunk.
    for start in range(0, len(bid_ids), 50):
        chunk = bid_ids[start:start + 50]
        data = mc._query(query, {
            "ids": [str(i) for i in chunk],
            "cols": [JOBSTART_BID_LOCATION_COL],
        })
        for item in data.get("items") or []:
            for cv in item.get("column_values") or []:
                if cv.get("id") != JOBSTART_BID_LOCATION_COL:
                    continue
                from subsystems.jobstart.location_lookup import location_from_column_value
                pieces = location_from_column_value(cv)
                if pieces.get("hint"):
                    hints[int(item["id"])] = pieces["hint"]
    return hints


def plan_project_item(
    item: dict,
    *,
    bid_hints: Optional[dict[int, str]] = None,
    parent_index: Optional[dict[str, str]] = None,
    geocode: bool = True,
    geocode_street_fn=None,
    reverse_geocode_fn=None,
) -> dict:
    """Shape one Monday Projects item and run enriched rename planner."""
    columns = _column_map(item)
    name = (item.get("name") or "").strip()
    loc_cv = columns.get(JOBSTART_P_COL_LOCATION) or {}
    extras: list[Optional[str]] = []
    for bid_id in _linked_ids(columns.get(JOBSTART_P_COL_OPPORTUNITY)):
        hint = (bid_hints or {}).get(bid_id)
        if hint:
            extras.append(hint)

    parent_name = None
    if rename_plan.is_co_item_name(name) and parent_index is not None:
        parent_name = rename_enrich.resolve_parent_title(name, parent_index)

    customer = _column_text(columns, JOBSTART_P_COL_CUSTOMER)
    title_kwargs = rename_enrich.job_title_kwargs_from_monday(
        status=_column_text(columns, COL_PROJECT_TYPE_STATUS),
        customer=customer,
    )

    return rename_enrich.plan_enriched_row(
        name=name,
        location_text=_column_text(columns, JOBSTART_P_COL_LOCATION),
        location_value_json=loc_cv.get("value"),
        location_column=loc_cv,
        extra_hints=extras,
        builder=_column_text(columns, COL_BUILDER),
        customer=customer,
        parent_name=parent_name,
        item_id=int(item["id"]),
        board="projects",
        gfolder_url=_gfolder_url(columns),
        geocode=geocode and not rename_plan.is_co_item_name(name),
        geocode_street_fn=geocode_street_fn,
        reverse_geocode_fn=reverse_geocode_fn,
        **title_kwargs,
    )


def list_project_items(mc, *, limit: Optional[int] = None) -> list[dict]:
    """Page Projects; return raw items (with location value JSON)."""
    query = """
    query ($boardId: [ID!], $cursor: String, $cols: [String!]) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            name
            column_values(ids: $cols) {
              id
              text
              value
              ... on LinkValue { url }
              ... on BoardRelationValue { display_value }
            }
          }
        }
      }
    }
    """
    items: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(
            query,
            {
                "boardId": [str(PROJECTS_BOARD_ID)],
                "cursor": cursor,
                "cols": list(PROJECT_COLUMN_IDS),
            },
        )
        boards = data.get("boards") or []
        if not boards:
            break
        page = boards[0].get("items_page") or {}
        for item in page.get("items") or []:
            items.append(item)
            if limit is not None and len(items) >= limit:
                return items
        cursor = page.get("cursor")
        if not cursor:
            break
    return items


def list_project_rename_plans(
    mc,
    *,
    limit: Optional[int] = None,
    geocode: bool = True,
    geocode_street_fn=None,
    reverse_geocode_fn=None,
) -> list[dict]:
    """Page Projects, look up locations, plan renames, cascade CO titles."""
    items = list_project_items(mc, limit=limit)

    bid_ids: list[int] = []
    for item in items:
        columns = _column_map(item)
        bid_ids.extend(_linked_ids(columns.get(JOBSTART_P_COL_OPPORTUNITY)))
    bid_hints = fetch_bid_location_hints(mc, sorted(set(bid_ids)))

    # Pass 1 — non-CO rows (may geocode).
    non_co_plans: list[dict] = []
    co_items: list[dict] = []
    for item in items:
        name = (item.get("name") or "").strip()
        if rename_plan.is_co_item_name(name):
            co_items.append(item)
            continue
        non_co_plans.append(plan_project_item(
            item, bid_hints=bid_hints, geocode=geocode,
            geocode_street_fn=geocode_street_fn,
            reverse_geocode_fn=reverse_geocode_fn,
        ))

    parent_index = rename_enrich.index_parent_titles(non_co_plans)

    # Pass 2 — CO cascade from looked-up / renamed parents.
    co_plans = [
        plan_project_item(
            item, bid_hints=bid_hints, parent_index=parent_index, geocode=False,
        )
        for item in co_items
    ]
    return non_co_plans + co_plans


def _table_rows(plans: list[dict]) -> list[list[str]]:
    rows = []
    for plan in plans:
        rows.append([
            str(plan.get("action") or ""),
            str(plan.get("item_id") or ""),
            str(plan.get("old_name") or ""),
            str(plan.get("new_name") or ""),
            ",".join(plan.get("lookup_sources") or []) or "—",
            str(plan.get("note") or ""),
        ])
    return rows


def print_plan_table(plans: list[dict]) -> None:
    headers = ["ACTION", "ITEM ID", "CURRENT TITLE", "PROPOSED TITLE", "LOOKUP", "NOTE"]
    rows = _table_rows(plans)
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(v.ljust(widths[i]) for i, v in enumerate(row))

    print(format_row(headers))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(format_row(row))


def apply_rename_plans(
    mc,
    plans: list[dict],
    *,
    rename_fn: Callable[[object, int, int, str], None] = rename_item_name,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[int, list[tuple[int, str]]]:
    candidates = rename_plan.rename_candidates(plans)
    errors: list[tuple[int, str]] = []
    written = 0
    for index, plan in enumerate(candidates):
        item_id = int(plan["item_id"])
        try:
            rename_fn(mc, PROJECTS_BOARD_ID, item_id, plan["new_name"])
            written += 1
            print(f"WROTE {item_id}: {plan['old_name']} -> {plan['new_name']}")
        except Exception as exc:  # noqa: BLE001
            errors.append((item_id, f"{type(exc).__name__}: {exc}"))
            print(f"ERROR {item_id}: {errors[-1][1]}", file=sys.stderr)
        if index < len(candidates) - 1:
            sleep_fn(WRITE_DELAY_SECONDS)
    return written, errors


def _print_summary(plans, *, apply: bool, written: int = 0, errors: int = 0) -> None:
    summary = rename_plan.summarize(plans)
    print(
        "\nSummary: "
        f"total={summary['total']} "
        f"rename={summary['rename']} "
        f"already_standard={summary['skip_standard']} "
        f"incomplete={summary['skip_incomplete']} "
        f"written={written} errors={errors}"
        + ("" if apply else " (dry-run — pass --apply to write)")
    )


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    apply = should_apply(args)
    if args.apply and args.dry_run:
        print(
            "[warn] both --apply and --dry-run set; staying in dry-run",
            file=sys.stderr,
        )
    print(
        f"[projects-rename] mode={'APPLY' if apply else 'DRY-RUN'} "
        f"limit={args.limit or 'none'} "
        f"geocode={'off' if args.no_geocode else 'on'}",
        file=sys.stderr,
    )

    try:
        mc = MondayClient()
        plans = list_project_rename_plans(
            mc, limit=args.limit, geocode=not args.no_geocode,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[projects-rename] unable to read Monday: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    print_plan_table(plans)
    if not apply:
        _print_summary(plans, apply=False)
        return 0

    written, write_errors = apply_rename_plans(mc, plans)
    _print_summary(plans, apply=True, written=written, errors=len(write_errors))
    return 1 if write_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
