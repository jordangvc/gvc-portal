"""
Bulk-rename Monday Operations titles to
`Street, City, ST ZIP | Builder | Job Title`.

Dry-run is the default. `--apply` writes; when both flags are supplied,
`--dry-run` wins. Linked Projects titles remain authoritative; when a linked
Project is not standard yet, its Monday location JSON, Job Title hints, and
optional Nominatim lookup enrich the Operations title. CO rows cascade from
their parent title.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday.client import (  # noqa: E402
    COL_PROJECT_TYPE_STATUS,
    MondayClient,
)
from adapters.monday.rename import rename_item_name  # noqa: E402
from shared.boards import (  # noqa: E402
    JOBSTART_OPS_COL_LINK_PROJECTS,
    JOBSTART_P_COL_CUSTOMER,
    JOBSTART_P_COL_LOCATION,
    MORNING_COL_PROJECT_LINK,
    OPERATIONS_BOARD_ID,
    PROJECTS_BOARD_ID,
)
from subsystems.jobstart import naming, rename_enrich, rename_plan  # noqa: E402


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


def _project_column(item: dict, column_id: str) -> dict:
    return next(
        (
            column
            for column in (item.get("column_values") or [])
            if column.get("id") == column_id
        ),
        {},
    )


def _project_column_text(item: dict, column_id: str) -> str:
    column = _project_column(item, column_id)
    for key in ("display_value", "text"):
        raw = column.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def list_project_names(mc) -> tuple[list[dict], dict[int, str]]:
    """Page Projects once; include location + Job Title hint columns."""
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
              ... on BoardRelationValue { display_value }
            }
          }
        }
      }
    }
    """
    project_cols = [
        JOBSTART_P_COL_LOCATION,
        JOBSTART_P_COL_CUSTOMER,
        COL_PROJECT_TYPE_STATUS,
    ]
    projects: list[dict] = []
    by_id: dict[int, str] = {}
    cursor: Optional[str] = None
    while True:
        page = _items_page(mc._query(query, {
            "boardId": [str(PROJECTS_BOARD_ID)],
            "cursor": cursor,
            "cols": project_cols,
        }))
        if page is None:
            break
        for item in page.get("items") or []:
            name = (item.get("name") or "").strip()
            if not name or rename_plan.is_co_item_name(name):
                continue
            item_id = int(item["id"])
            location_column = _project_column(item, JOBSTART_P_COL_LOCATION)
            projects.append({
                "id": item_id,
                "name": name,
                "location": (location_column.get("text") or "").strip(),
                "location_value_json": location_column.get("value") or "",
                "location_column": location_column,
                "customer": _project_column_text(item, JOBSTART_P_COL_CUSTOMER),
                "project_type": _project_column_text(
                    item, COL_PROJECT_TYPE_STATUS,
                ),
            })
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


def build_project_parent_index(
    projects: list[dict],
    *,
    geocode: bool = True,
    geocode_street_fn=None,
    reverse_geocode_fn=None,
) -> dict[str, str]:
    """Plan Projects from their recorded locations for CO parent resolution."""
    project_plans = [
        rename_enrich.plan_enriched_row(
            name=project.get("name") or "",
            location_text=project.get("location"),
            location_value_json=project.get("location_value_json"),
            location_column=project.get("location_column"),
            customer=project.get("customer"),
            item_id=project.get("id"),
            board="projects",
            geocode=geocode,
            geocode_street_fn=geocode_street_fn,
            reverse_geocode_fn=reverse_geocode_fn,
            **rename_enrich.job_title_kwargs_from_monday(
                status=project.get("project_type") or "",
                customer=project.get("customer") or "",
            ),
        )
        for project in projects
    ]
    return rename_enrich.index_parent_titles(project_plans)


def _enrich_from_project(
    *,
    row_name: str,
    item_id: int,
    project: dict,
    geocode: bool,
    geocode_street_fn,
    reverse_geocode_fn,
) -> dict:
    """Plan an Ops title using its linked/matched Project's location facts."""
    customer = project.get("customer") or ""
    return rename_enrich.plan_enriched_row(
        name=row_name,
        location_text=project.get("location"),
        location_value_json=project.get("location_value_json"),
        location_column=project.get("location_column"),
        customer=customer,
        item_id=item_id,
        board="operations",
        geocode=geocode,
        geocode_street_fn=geocode_street_fn,
        reverse_geocode_fn=reverse_geocode_fn,
        **rename_enrich.job_title_kwargs_from_monday(
            status=project.get("project_type") or "",
            customer=customer,
        ),
    )


def plan_operation_item(
    row: dict,
    *,
    projects: list[dict],
    project_names: dict[int, str],
    parent_index: Optional[dict[str, str]] = None,
    geocode: bool = True,
    geocode_street_fn=None,
    reverse_geocode_fn=None,
) -> dict:
    """Mirror Projects first, then enrich unresolved Ops/CO titles."""
    name = (row.get("name") or "").strip()
    item_id = int(row["item_id"])
    linked_ids = row.get("linked_project_ids") or []
    projects_by_id = {int(project["id"]): project for project in projects}
    linked_project = next(
        (
            projects_by_id.get(int(project_id))
            for project_id in linked_ids
            if projects_by_id.get(int(project_id))
        ),
        None,
    )
    linked_name = next(
        (project_names.get(int(project_id)) for project_id in linked_ids
         if project_names.get(int(project_id))),
        None,
    )

    if rename_plan.is_co_item_name(name):
        parent_name = linked_name if naming.is_standard(linked_name or "") else None
        if not parent_name and linked_project:
            linked_customer = linked_project.get("customer") or ""
            project_plan = rename_enrich.plan_enriched_row(
                name=linked_project.get("name") or "",
                location_text=linked_project.get("location"),
                location_value_json=linked_project.get("location_value_json"),
                location_column=linked_project.get("location_column"),
                customer=linked_customer,
                geocode=geocode,
                geocode_street_fn=geocode_street_fn,
                reverse_geocode_fn=reverse_geocode_fn,
                **rename_enrich.job_title_kwargs_from_monday(
                    status=linked_project.get("project_type") or "",
                    customer=linked_customer,
                ),
            )
            if naming.is_standard(project_plan.get("new_name") or ""):
                parent_name = project_plan["new_name"]
        if not parent_name:
            parent_name = rename_enrich.resolve_parent_title(
                name, parent_index or {},
            )
        plan = rename_enrich.plan_enriched_row(
            name=name,
            parent_name=parent_name,
            item_id=item_id,
            board="operations",
            geocode=False,
        )
        source = (
            "linked_project_parent"
            if linked_name and parent_name
            else "parent_index"
            if parent_name
            else "ops_name"
        )
        return {**plan, "source": source}

    if linked_ids:
        if linked_name and naming.is_standard(linked_name):
            plan = rename_enrich.plan_enriched_row(
                name=name,
                linked_project_name=linked_name,
                item_id=item_id,
                board="operations",
                geocode=False,
            )
            return {**plan, "source": "linked_project"}
        # Prefer the Projects planner's proposed standard title over re-parsing
        # the Ops row (keeps Ops aligned when Projects is still short/stale).
        planned_parent = (parent_index or {}).get(linked_name or "")
        if planned_parent and naming.is_standard(planned_parent):
            plan = rename_enrich.plan_enriched_row(
                name=name,
                linked_project_name=planned_parent,
                item_id=item_id,
                board="operations",
                geocode=False,
            )
            return {**plan, "source": "linked_project_planned"}
        if linked_project:
            plan = _enrich_from_project(
                row_name=name,
                item_id=item_id,
                project=linked_project,
                geocode=geocode,
                geocode_street_fn=geocode_street_fn,
                reverse_geocode_fn=reverse_geocode_fn,
            )
            return {**plan, "source": "linked_project_enriched"}

        plan = rename_enrich.plan_enriched_row(
            name=name,
            item_id=item_id,
            board="operations",
            geocode=geocode,
            geocode_street_fn=geocode_street_fn,
            reverse_geocode_fn=reverse_geocode_fn,
        )
        if plan["action"] == "skip_incomplete":
            plan["note"] = (
                f"Linked Project {linked_ids[0]} missing from Projects index. "
                f"{plan.get('note') or ''}"
            ).strip()
        return {**plan, "source": "linked_project"}

    plan = rename_enrich.plan_enriched_row(
        name=name,
        item_id=item_id,
        board="operations",
        geocode=False,
    )
    if plan["action"] != "skip_incomplete":
        return {**plan, "source": "ops_name"}

    hit = naming.best_match(name, projects, threshold=MATCH_THRESHOLD)
    if hit:
        if naming.is_standard(hit.get("name") or ""):
            mirrored = rename_enrich.plan_enriched_row(
                name=name,
                linked_project_name=hit["name"],
                item_id=item_id,
                board="operations",
                geocode=False,
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

        planned_match = (parent_index or {}).get(hit.get("name") or "")
        if planned_match and naming.is_standard(planned_match):
            mirrored = rename_enrich.plan_enriched_row(
                name=name,
                linked_project_name=planned_match,
                item_id=item_id,
                board="operations",
                geocode=False,
            )
            mirrored["note"] = (
                f"Mirror planned Projects title for match {hit['id']} "
                f"(score {hit['score']:.2f})."
            )
            return {
                **mirrored,
                "source": "matched_project_planned",
                "match_project_id": int(hit["id"]),
                "match_score": hit["score"],
            }

        enriched = _enrich_from_project(
            row_name=name,
            item_id=item_id,
            project=hit,
            geocode=geocode,
            geocode_street_fn=geocode_street_fn,
            reverse_geocode_fn=reverse_geocode_fn,
        )
        if enriched["action"] != "skip_incomplete":
            return {
                **enriched,
                "source": "matched_project_enriched",
                "match_project_id": int(hit["id"]),
                "match_score": hit["score"],
            }

    geocoded = rename_enrich.plan_enriched_row(
        name=name,
        item_id=item_id,
        board="operations",
        geocode=geocode,
        geocode_street_fn=geocode_street_fn,
        reverse_geocode_fn=reverse_geocode_fn,
    )
    return {
        **geocoded,
        "source": (
            "ops_name_geocoded"
            if "nominatim_tri_state" in geocoded.get("lookup_sources", [])
            else "ops_name"
        ),
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


def run(*, apply: bool, limit: Optional[int], geocode: bool = True) -> int:
    """Load both boards, plan every selected Ops row, and optionally write."""
    try:
        mc = MondayClient()
    except Exception as exc:  # noqa: BLE001
        print(f"Monday not configured: {exc}", file=sys.stderr)
        return 2

    mode = "APPLY" if apply else "DRY-RUN"
    print(
        f"[ops-rename] mode={mode} threshold={MATCH_THRESHOLD} "
        f"limit={limit or 'none'} geocode={'on' if geocode else 'off'}",
        file=sys.stderr,
    )
    projects, project_names = list_project_names(mc)
    parent_index = build_project_parent_index(projects, geocode=geocode)
    rows = list_operations_items(mc, limit=limit)
    plans = [
        plan_operation_item(
            row,
            projects=projects,
            project_names=project_names,
            parent_index=parent_index,
            geocode=geocode,
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
    parser.add_argument(
        "--no-geocode",
        action="store_true",
        help="Use Monday/linked Project facts only — do not call Nominatim.",
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
    return run(
        apply=apply,
        limit=args.limit,
        geocode=not args.no_geocode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
