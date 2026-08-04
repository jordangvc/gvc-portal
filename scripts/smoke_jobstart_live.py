"""
Optional, read-only Monday smoke for Job Start.

The script exits 0 with SKIPPED when MONDAY_API_TOKEN is absent, so secret-free
CI remains green. Even ``--apply`` is refused in v1: this harness performs no
Monday mutation.

    PYTHONPATH=. .venv/bin/python scripts/smoke_jobstart_live.py
    PYTHONPATH=. .venv/bin/python scripts/smoke_jobstart_live.py --limit 5 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

from adapters.monday import jobstart as monday_jobstart
from adapters.monday.client import MondayClient
from shared import boards


def inspect_accepted_bids(mc: MondayClient, *, limit: int = 5) -> dict[str, Any]:
    """
    Read accepted bids and inspect linked Projects GFolder values.

    Only the first ``limit`` accepted rows receive detail/GFolder queries. The
    summary distinguishes a missing Projects link from a linked Projects item
    whose GFolder Link cell is empty.
    """
    bids = [
        bid for bid in monday_jobstart.fetch_bids(mc)
        if bid.get("stage_state") == "accepted"
    ]
    sample = bids[:limit]
    rows: list[dict[str, Any]] = []
    without_project_link = 0
    linked_projects_without_gfolder = 0
    detail_missing = 0

    for bid in sample:
        detail = monday_jobstart.get_bid_detail(mc, int(bid["item_id"]))
        if not detail:
            detail_missing += 1
            rows.append({
                "item_id": bid["item_id"],
                "name": bid.get("name"),
                "status": "DETAIL_MISSING",
            })
            continue

        project_ids = [int(value) for value in detail.get("existing_project_ids") or []]
        if not project_ids:
            without_project_link += 1
            rows.append({
                "item_id": bid["item_id"],
                "name": bid.get("name"),
                "status": "NO_PROJECT_LINK",
                "project_ids": [],
                "gfolder": None,
            })
            continue

        project_id = project_ids[0]
        values = monday_jobstart.fetch_item_column_texts(
            mc,
            project_id,
            [boards.PROJECTS_GFOLDER_COL],
        )
        gfolder = (values.get(boards.PROJECTS_GFOLDER_COL) or "").strip() or None
        if not gfolder:
            linked_projects_without_gfolder += 1
        rows.append({
            "item_id": bid["item_id"],
            "name": bid.get("name"),
            "status": "READY" if gfolder else "GFOLDER_EMPTY",
            "project_ids": project_ids,
            "gfolder": gfolder,
        })

    return {
        "ok": detail_missing == 0,
        "mode": "read-only",
        "accepted_visible": len(bids),
        "sampled": len(sample),
        "without_project_link": without_project_link,
        "linked_projects_without_gfolder": linked_projects_without_gfolder,
        "detail_missing": detail_missing,
        "rows": rows,
        "writes": 0,
    }


def print_report(report: dict[str, Any]) -> None:
    print(
        "Job Start Monday smoke (READ-ONLY): "
        f"accepted={report['accepted_visible']} sampled={report['sampled']} "
        f"no_project_link={report['without_project_link']} "
        f"empty_gfolder={report['linked_projects_without_gfolder']} "
        f"detail_missing={report['detail_missing']}"
    )
    for row in report["rows"]:
        print(
            f"  {row['status']:<15} {row['item_id']:<12} "
            f"{row.get('name') or '(unnamed)'}"
        )
    print("PASS read-only smoke" if report["ok"] else "FAIL read-only smoke")
    print("No Monday writes were attempted.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Optional read-only Job Start Monday smoke.",
    )
    parser.add_argument("--limit", type=int, default=5,
                        help="Accepted bids to inspect in detail (default: 5).")
    parser.add_argument("--json", action="store_true",
                        help="Print machine-readable JSON.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Reserved. Refused in v1 because this smoke is read-only.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.apply:
        print(
            "REFUSED: --apply is not implemented; smoke_jobstart_live.py v1 is "
            "read-only and cannot write Monday.",
            file=sys.stderr,
        )
        return 2
    if args.limit < 1:
        print("FAIL: --limit must be at least 1", file=sys.stderr)
        return 2
    if not (os.environ.get("MONDAY_API_TOKEN") or "").strip():
        print("SKIPPED: MONDAY_API_TOKEN is not set; no live Monday checks ran.")
        return 0

    try:
        report = inspect_accepted_bids(MondayClient(), limit=args.limit)
    except Exception as error:  # noqa: BLE001 — live smoke reports adapter/API failures
        print(
            f"FAIL Job Start Monday smoke: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
