"""
Bulk-rename Monday Bid Board titles to the canonical job-name standard.

The planner is shared with the other rename slices; this script owns only the
Bid Board read/apply path. Missing city/state/ZIP is looked up from Monday's
location JSON and, when needed, Nominatim instead of being skipped immediately.

Safety:
  * Dry-run is the default.
  * --apply is required for Monday writes.
  * If --apply and --dry-run are both passed, dry-run wins.
  * Lost/cancelled bids are never planned or renamed.
  * Geocoding is on by default; pass --no-geocode to use Monday facts only.

Examples (from the repository root):

  .venv/bin/python scripts/backfill_job_rename_bids.py --limit 20
  .venv/bin/python scripts/backfill_job_rename_bids.py --apply --limit 20
  .venv/bin/python scripts/backfill_job_rename_bids.py --no-geocode --limit 20
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
    BID_BOARD_ID,
    JOBSTART_ACCEPTED_STAGE,
    JOBSTART_BID_CUSTOMER_COL,
    JOBSTART_BID_LOCATION_COL,
    JOBSTART_BID_STAGE_COL,
    JOBSTART_DEAD_STAGE_WORDS,
)
from subsystems.jobstart import rename_enrich  # noqa: E402
from subsystems.jobstart.rename_plan import rename_candidates, summarize  # noqa: E402


BID_READ_COLUMNS = (
    JOBSTART_BID_LOCATION_COL,
    JOBSTART_BID_CUSTOMER_COL,
    JOBSTART_BID_STAGE_COL,
)
WRITE_DELAY_SECONDS = 0.2


def _column_text(column_values: list[dict], column_id: str) -> str:
    """Return one Monday column's display text."""
    for value in column_values:
        if value.get("id") == column_id:
            return (value.get("text") or "").strip()
    return ""


def _column(column_values: list[dict], column_id: str) -> dict:
    """Return one raw Monday column value, including its value JSON."""
    return next(
        (value for value in column_values if value.get("id") == column_id),
        {},
    )


def is_dead_stage(stage: Optional[str]) -> bool:
    """Lost/cancel substring match using the shared Job Start policy."""
    text = (stage or "").strip().lower()
    return any(word in text for word in JOBSTART_DEAD_STAGE_WORDS)


def _stage_priority(stage: Optional[str]) -> int:
    """Accepted/Won bids sort before other live stages."""
    text = (stage or "").strip().lower()
    accepted = JOBSTART_ACCEPTED_STAGE.strip().lower()
    return 0 if text == accepted or "won" in text else 1


def fetch_bid_rows(mc, *, limit: Optional[int] = None) -> tuple[list[dict], int]:
    """
    Page the Bid Board and return live rows plus the dead-stage skip count.

    The limit applies after dead-stage filtering and Accepted/Won prioritization.
    """
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
            }
          }
        }
      }
    }
    """
    rows: list[dict] = []
    dead_skipped = 0
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {
            "boardId": [str(BID_BOARD_ID)],
            "cursor": cursor,
            "cols": list(BID_READ_COLUMNS),
        })
        boards = data.get("boards") or []
        if not boards:
            break
        page = boards[0].get("items_page") or {}
        for item in page.get("items") or []:
            values = item.get("column_values") or []
            stage = _column_text(values, JOBSTART_BID_STAGE_COL)
            if is_dead_stage(stage):
                dead_skipped += 1
                continue
            location_column = _column(values, JOBSTART_BID_LOCATION_COL)
            rows.append({
                "item_id": int(item["id"]),
                "name": (item.get("name") or "").strip(),
                "location": _column_text(values, JOBSTART_BID_LOCATION_COL),
                "location_value_json": location_column.get("value") or "",
                "location_column": location_column,
                "customer": _column_text(values, JOBSTART_BID_CUSTOMER_COL),
                "stage": stage,
            })
        cursor = page.get("cursor")
        if not cursor:
            break

    rows.sort(key=lambda row: _stage_priority(row.get("stage")))
    if limit is not None:
        rows = rows[:limit]
    return rows, dead_skipped


def build_plans(
    rows: list[dict],
    *,
    geocode: bool = True,
    geocode_street_fn=None,
    reverse_geocode_fn=None,
) -> list[dict]:
    """Look up missing location facts, then plan each eligible Bid row."""
    plans: list[dict] = []
    for row in rows:
        plan = rename_enrich.plan_enriched_row(
            name=row["name"],
            location_text=row.get("location"),
            location_value_json=row.get("location_value_json"),
            location_column=row.get("location_column"),
            customer=row.get("customer"),
            item_id=row["item_id"],
            board="bid_board",
            geocode=geocode,
            geocode_street_fn=geocode_street_fn,
            reverse_geocode_fn=reverse_geocode_fn,
        )
        plan["stage"] = row.get("stage") or ""
        plans.append(plan)
    return plans


def _clip(value: object, width: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def print_table(plans: list[dict]) -> None:
    """Print one compact, dependency-free review table."""
    widths = (15, 12, 18, 46, 46, 42)
    headings = ("Result", "Item ID", "Stage", "Current title",
                "Proposed title", "Note")
    print("  ".join(_clip(value, width).ljust(width)
                    for value, width in zip(headings, widths)))
    print("  ".join("-" * width for width in widths))
    for plan in plans:
        result = plan.get("write_status") or plan.get("action") or "unknown"
        values = (
            result,
            plan.get("item_id"),
            plan.get("stage"),
            plan.get("old_name"),
            plan.get("new_name"),
            plan.get("write_error") or plan.get("note"),
        )
        print("  ".join(_clip(value, width).ljust(width)
                        for value, width in zip(values, widths)))


def run(
    *,
    apply: bool,
    limit: Optional[int],
    geocode: bool = True,
    mc=None,
) -> int:
    """Plan the Bid Board rename and optionally apply safe candidates."""
    mode = "APPLY" if apply else "DRY-RUN"
    print(
        f"[bid-rename] mode={mode} limit={limit or 'none'} "
        f"geocode={'on' if geocode else 'off'}",
        file=sys.stderr,
    )
    if mc is None:
        try:
            mc = MondayClient()
        except Exception as error:  # noqa: BLE001
            print(f"Monday not configured: {error}", file=sys.stderr)
            return 2

    try:
        rows, dead_skipped = fetch_bid_rows(mc, limit=limit)
    except Exception as error:  # noqa: BLE001
        print(f"Could not read Bid Board: {type(error).__name__}: {error}",
              file=sys.stderr)
        return 2

    plans = build_plans(rows, geocode=geocode)
    candidates = rename_candidates(plans)
    written = 0
    write_errors = 0
    if apply:
        for index, plan in enumerate(candidates):
            try:
                rename_item_name(
                    mc,
                    BID_BOARD_ID,
                    int(plan["item_id"]),
                    plan["new_name"],
                )
            except Exception as error:  # noqa: BLE001
                write_errors += 1
                plan["write_status"] = "ERROR"
                plan["write_error"] = f"{type(error).__name__}: {error}"
            else:
                written += 1
                plan["write_status"] = "RENAMED"
            if index < len(candidates) - 1:
                time.sleep(WRITE_DELAY_SECONDS)
    else:
        for plan in candidates:
            plan["write_status"] = "WOULD_RENAME"

    print_table(plans)
    counts = summarize(plans)
    print(
        "\nSummary: "
        f"processed={counts['total']} "
        f"rename={counts['rename']} "
        f"already_standard={counts['skip_standard']} "
        f"incomplete={counts['skip_incomplete']} "
        f"co_skipped={counts['skip_co']} "
        f"dead_stage_skipped={dead_skipped} "
        f"written={written} "
        f"write_errors={write_errors}"
    )
    if not apply:
        print("Dry-run only — pass --apply to write.", file=sys.stderr)
    return 0 if write_errors == 0 else 1


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Bulk-rename live Bid Board titles. Dry-run by default."),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned renames only (also the default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write planned title changes to Monday.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Process at most N non-dead bids after priority sorting.",
    )
    parser.add_argument(
        "--no-geocode",
        action="store_true",
        help="Use Monday location facts only — do not call Nominatim.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    apply = bool(args.apply) and not bool(args.dry_run)
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
