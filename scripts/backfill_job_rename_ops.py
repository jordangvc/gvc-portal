"""
Bulk-rename Monday Operations titles to `Street, City, ST ZIP | Builder`.

Dry-run is the default. `--apply` writes; when both flags are supplied,
`--dry-run` wins. CO rows are always skipped.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday.client import MondayClient  # noqa: E402
from adapters.monday.rename import rename_item_name  # noqa: E402
from shared.boards import (  # noqa: E402
    JOBSTART_OPS_COL_LINK_PROJECTS,
    MORNING_COL_PROJECT_LINK,
    OPERATIONS_BOARD_ID,
    PROJECTS_BOARD_ID,
)
from subsystems.jobstart import naming  # noqa: E402
from subsystems.jobstart import rename_plan  # noqa: E402


MATCH_THRESHOLD = 0.85
WRITE_DELAY_SECONDS = 0.2
# Job Start and Morning Brief intentionally alias the same Operations relation.
OPS_PROJECT_LINK_COL = (
    JOBSTART_OPS_COL_LINK_PROJECTS or MORNING_COL_PROJECT_LINK
)


def _items_page(data: dict) -> Optional[dict]:
    boards = data.get("boards") or []
    if not boards:
        return None
    return boards[0].get("items_page") or {}


def list_project_names(mc) -> tuple[list[dict], dict[int, str]]:
    """Page Projects once; return candidates plus an id-to-name index."""
    query = """
    query ($boardId: [ID!], $cursor: String) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items { id name }
        }
      }
    }
    """
    projects: list[dict] = []
    by_id: dict[int, str] = {}
    cursor: Optional[str] = None
    while True:
        page = _items_page(mc._query(query, {
            "boardId": [str(PROJECTS_BOARD_ID)],
            "cursor": cursor,
        }))
        if page is None:
            break
        for item in page.get("items") or []:
            name = (item.get("name") or "").strip()
            if not name or rename_plan.is_co_item_name(name):
                continue
            item_id = int(item["id"])
            projects.append({"id": item_id, "name": name})
            by_id[item_id] = name
        cursor = page.get("cursor")
        if not cursor:
            break
    return projects, by_id


def _linked_project_ids(item: dict) -> list[int]:
    for value in item.get("column_values") or []:
        if value.get("id") != OPS_PROJECT_LINK_COL:
            continue
        raw_ids = list(value.get("linked_item_ids") or [])
        if not raw_ids:
            raw_ids = [
                linked.get("id")
                for linked in (value.get("linked_items") or [])
                if linked and linked.get("id")
            ]
        return [int(item_id) for item_id in raw_ids if item_id]
    return []


def list_operations_items(mc, *, limit: Optional[int] = None) -> list[dict]:
    """Page Operations and return title plus linked Projects ids."""
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
              ... on BoardRelationValue {
                linked_item_ids
                linked_items { id }
              }
            }
          }
        }
      }
    }
    """
    rows: list[dict] = []
    cursor: Optional[str] = None
    while True:
        page = _items_page(mc._query(query, {
            "boardId": [str(OPERATIONS_BOARD_ID)],
            "cursor": cursor,
            "cols": [OPS_PROJECT_LINK_COL],
        }))
        if page is None:
            break
        for item in page.get("items") or []:
            rows.append({
                "item_id": int(item["id"]),
                "name": (item.get("name") or "").strip(),
                "linked_project_ids": _linked_project_ids(item),
            })
            if limit is not None and len(rows) >= limit:
                return rows
        cursor = page.get("cursor")
        if not cursor:
            break
    return rows


def plan_operation_item(
    row: dict,
    *,
    projects: list[dict],
    project_names: dict[int, str],
) -> dict:
    """Build one safe Operations rename decision."""
    name = (row.get("name") or "").strip()
    item_id = int(row["item_id"])
    base_args = {"name": name, "item_id": item_id, "board": "operations"}

    if rename_plan.is_co_item_name(name):
        return {**rename_plan.plan_row(**base_args), "source": "ops_name"}

    linked_ids = row.get("linked_project_ids") or []
    linked_name = next(
        (project_names.get(int(project_id)) for project_id in linked_ids
         if project_names.get(int(project_id))),
        None,
    )
    if linked_ids:
        plan = rename_plan.plan_row(
            **base_args,
            linked_project_name=linked_name,
        )
        if not linked_name and plan["action"] == "skip_incomplete":
            plan["note"] = (
                f"Linked Project {linked_ids[0]} missing from Projects index. "
                f"{plan.get('note') or ''}"
            ).strip()
        return {**plan, "source": "linked_project"}

    plan = rename_plan.plan_row(**base_args)
    if plan["action"] != "skip_incomplete":
        return {**plan, "source": "ops_name"}

    hit = naming.best_match(name, projects, threshold=MATCH_THRESHOLD)
    if not hit or not naming.is_standard(hit.get("name") or ""):
        return {**plan, "source": "ops_name"}

    mirrored = rename_plan.plan_row(
        **base_args,
        linked_project_name=hit["name"],
    )
    mirrored["note"] = (
        f"Mirror unique Projects match {hit['id']} "
        f"(score {hit['score']:.2f})."
    )
    return {
        **mirrored,
        "source": "matched_project",
        "match_project_id": int(hit["id"]),
        "match_score": hit["score"],
    }


def apply_plans(mc, plans: list[dict]) -> list[dict]:
    """Apply rename decisions, continuing after individual write errors."""
    errors: list[dict] = []
    candidates = rename_plan.rename_candidates(plans)
    for index, plan in enumerate(candidates):
        try:
            rename_item_name(
                mc,
                OPERATIONS_BOARD_ID,
                int(plan["item_id"]),
                plan["new_name"],
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "item_id": plan["item_id"],
                "old_name": plan["old_name"],
                "error": f"{type(exc).__name__}: {exc}",
            })
        if index + 1 < len(candidates):
            time.sleep(WRITE_DELAY_SECONDS)
    return errors


def _short(value: object, width: int) -> str:
    text = str(value or "").replace("\n", " ")
    if len(text) <= width:
        return text
    return f"{text[:width - 1]}…"


def print_table(plans: list[dict]) -> None:
    """Print a compact, reviewable decision table."""
    header = (
        f"{'ACTION':<16} {'ITEM':>10} {'SOURCE':<16} "
        f"{'OLD TITLE':<42} {'NEW TITLE':<42} NOTE"
    )
    print(header)
    print("-" * len(header))
    for plan in plans:
        print(
            f"{plan['action']:<16} {plan['item_id']:>10} "
            f"{plan.get('source', ''):<16} "
            f"{_short(plan.get('old_name'), 42):<42} "
            f"{_short(plan.get('new_name'), 42):<42} "
            f"{_short(plan.get('note'), 72)}"
        )


def run(*, apply: bool, limit: Optional[int]) -> int:
    """Load both boards, plan every selected Ops row, and optionally write."""
    try:
        mc = MondayClient()
    except Exception as exc:  # noqa: BLE001
        print(f"Monday not configured: {exc}", file=sys.stderr)
        return 2

    mode = "APPLY" if apply else "DRY-RUN"
    print(
        f"[ops-rename] mode={mode} threshold={MATCH_THRESHOLD} "
        f"limit={limit or 'none'}",
        file=sys.stderr,
    )
    projects, project_names = list_project_names(mc)
    rows = list_operations_items(mc, limit=limit)
    plans = [
        plan_operation_item(
            row,
            projects=projects,
            project_names=project_names,
        )
        for row in rows
    ]
    print_table(plans)

    errors = apply_plans(mc, plans) if apply else []
    for error in errors:
        print(
            f"ERROR item {error['item_id']} {error['old_name']!r}: "
            f"{error['error']}",
            file=sys.stderr,
        )

    summary = rename_plan.summarize(plans)
    written = summary["rename"] - len(errors) if apply else 0
    print(
        "\n[ops-rename] "
        f"total={summary['total']} rename={summary['rename']} "
        f"already_standard={summary['skip_standard']} "
        f"incomplete={summary['skip_incomplete']} "
        f"co_skipped={summary['skip_co']} "
        f"written={written} "
        f"errors={len(errors)}"
        f"{'' if apply else ' (dry-run — pass --apply to write)'}",
        file=sys.stderr,
    )
    return 1 if errors else 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("--limit must be at least 1")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rename Monday Operations titles to "
            "'Street, City, ST ZIP | Builder'. Dry-run by default."
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
        help="Write approved rename decisions to Monday.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Process at most N Operations items.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    apply = bool(args.apply) and not bool(args.dry_run)
    if args.apply and args.dry_run:
        print(
            "[warn] both --apply and --dry-run set; staying in dry-run",
            file=sys.stderr,
        )
    return run(apply=apply, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
