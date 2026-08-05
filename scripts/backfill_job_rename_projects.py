"""
Plan or apply the GVC job-title standard on Monday's Projects board.

Target title:

    Street Number Name, City, ST ZIP | Builder

Safety:
  * Dry-run by default. ``--apply`` is required for Monday writes.
  * If ``--apply`` and ``--dry-run`` are both present, dry-run wins.
  * CO. rows and incomplete addresses/builders are skipped by rename_plan.
  * This script renames Monday item titles only. It never renames Drive folders.

Examples (from the repository root):

    .venv/bin/python scripts/backfill_job_rename_projects.py --limit 20
    .venv/bin/python scripts/backfill_job_rename_projects.py --apply --limit 20
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday.client import COL_BUILDER, MondayClient  # noqa: E402
from adapters.monday.rename import rename_item_name  # noqa: E402
from shared.boards import (  # noqa: E402
    JOBSTART_P_COL_CUSTOMER,
    JOBSTART_P_COL_LOCATION,
    PROJECTS_BOARD_ID,
    PROJECTS_GFOLDER_COL,
)
from subsystems.jobstart import rename_plan  # noqa: E402

WRITE_DELAY_SECONDS = 0.2
PROJECT_COLUMN_IDS = (
    JOBSTART_P_COL_LOCATION,
    COL_BUILDER,
    JOBSTART_P_COL_CUSTOMER,
    PROJECTS_GFOLDER_COL,
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
            "'Street Number Name, City, ST ZIP | Builder'. "
            "Dry-run is the default."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without writing (default; wins over --apply).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rename eligible Monday items.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Process at most N Projects items.",
    )
    return parser


def should_apply(args: argparse.Namespace) -> bool:
    """Return whether writes are enabled; an explicit dry-run always wins."""
    return bool(args.apply) and not bool(args.dry_run)


def _column_map(item: dict) -> dict[str, dict]:
    return {
        cv["id"]: cv
        for cv in (item.get("column_values") or [])
        if cv.get("id")
    }


def _column_text(columns: dict[str, dict], column_id: str) -> str:
    return (columns.get(column_id, {}).get("text") or "").strip()


def _gfolder_url(columns: dict[str, dict]) -> Optional[str]:
    column = columns.get(PROJECTS_GFOLDER_COL) or {}
    url = (column.get("url") or "").strip()
    return url or None


def plan_project_item(item: dict) -> dict:
    """PURE. Shape one Monday Projects item and run the shared rename planner."""
    columns = _column_map(item)
    return rename_plan.plan_row(
        name=(item.get("name") or "").strip(),
        location=_column_text(columns, JOBSTART_P_COL_LOCATION),
        builder=_column_text(columns, COL_BUILDER),
        customer=_column_text(columns, JOBSTART_P_COL_CUSTOMER),
        item_id=int(item["id"]),
        board="projects",
        gfolder_url=_gfolder_url(columns),
    )


def list_project_rename_plans(
    mc,
    *,
    limit: Optional[int] = None,
) -> list[dict]:
    """Page Projects in batches of 200 and return one rename plan per item."""
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
              ... on LinkValue { url }
            }
          }
        }
      }
    }
    """
    plans: list[dict] = []
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
            plans.append(plan_project_item(item))
            if limit is not None and len(plans) >= limit:
                return plans
        cursor = page.get("cursor")
        if not cursor:
            break
    return plans


def _table_rows(plans: list[dict]) -> list[list[str]]:
    rows = []
    for plan in plans:
        rows.append(
            [
                str(plan.get("action") or ""),
                str(plan.get("item_id") or ""),
                str(plan.get("old_name") or ""),
                str(plan.get("new_name") or ""),
                str(plan.get("gfolder_url") or "—"),
                str(plan.get("note") or ""),
            ]
        )
    return rows


def print_plan_table(plans: list[dict]) -> None:
    headers = ["ACTION", "ITEM ID", "CURRENT TITLE", "PROPOSED TITLE", "GFOLDER", "NOTE"]
    rows = _table_rows(plans)
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headers[index])
        for index in range(len(headers))
    ]

    def format_row(row: list[str]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        )

    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(format_row(row))


def apply_rename_plans(
    mc,
    plans: list[dict],
    *,
    rename_fn: Callable[[object, int, int, str], None] = rename_item_name,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[int, list[tuple[int, str]]]:
    """Apply only eligible rename plans, pausing between Monday writes."""
    candidates = rename_plan.rename_candidates(plans)
    errors: list[tuple[int, str]] = []
    written = 0
    for index, plan in enumerate(candidates):
        item_id = int(plan["item_id"])
        try:
            rename_fn(mc, PROJECTS_BOARD_ID, item_id, plan["new_name"])
            written += 1
            print(f"WROTE {item_id}: {plan['old_name']} -> {plan['new_name']}")
        except Exception as exc:  # noqa: BLE001 — continue the bounded backfill
            errors.append((item_id, f"{type(exc).__name__}: {exc}"))
            print(f"ERROR {item_id}: {errors[-1][1]}", file=sys.stderr)
        if index < len(candidates) - 1:
            sleep_fn(WRITE_DELAY_SECONDS)
    return written, errors


def _print_summary(
    plans: list[dict],
    *,
    apply: bool,
    written: int = 0,
    errors: int = 0,
) -> None:
    summary = rename_plan.summarize(plans)
    print(
        "\nSummary: "
        f"total={summary['total']} "
        f"rename={summary['rename']} "
        f"already_standard={summary['skip_standard']} "
        f"incomplete={summary['skip_incomplete']} "
        f"co_skipped={summary['skip_co']} "
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
        f"limit={args.limit or 'none'}",
        file=sys.stderr,
    )

    try:
        mc = MondayClient()
        plans = list_project_rename_plans(mc, limit=args.limit)
    except Exception as exc:  # noqa: BLE001 — configuration/API error at CLI boundary
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
    _print_summary(
        plans,
        apply=True,
        written=written,
        errors=len(write_errors),
    )
    return 1 if write_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
