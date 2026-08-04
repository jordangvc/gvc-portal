"""
Backfill Monday link columns that Job Check / photo upload need.

=========================================================================
Two modes (one script):

  gfolder   — Projects items with empty GFolder Link (link_mkwr6ef9): find the
              matching Drive folder under the shared Projects tree and stamp
              fill-if-empty via adapters.monday.jobcheck.set_projects_gfolder_if_empty.

  ops-link  — Operations items with empty link_to_projects: name-match a
              Projects item (threshold 0.85, skip ambiguous) and stamp
              fill-if-empty via set_ops_project_link_if_empty.

Safety
------
  • Dry-run by default — prints proposed stamps; no Monday writes.
  • --apply is required to write. If both --apply and --dry-run are passed,
    dry-run wins (never overwrite by accident).
  • NEVER overwrites a non-empty GFolder Link or an existing link_to_projects.
  • No PandaDoc. No item create/delete.

Drive search limitations (gfolder mode)
---------------------------------------
  Full tree walks of Projects/<year>/<Res|Comm>/<customer>/<job>/ are too
  heavy at board scale. This script uses DriveUploader.find_projects_job_folder:
  `name contains` on the street number / strongest token, then
  naming.best_match. Oddly renamed folders and ambiguous hits are skipped
  (reported as NO_MATCH / AMBIGUOUS), never guessed.

PowerShell (from the repo root, with MONDAY_API_TOKEN + Drive SA env set):

  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py --dry-run --limit 20
  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py --apply --limit 20

  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py gfolder --dry-run --limit 20
  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py gfolder --apply --limit 20
  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py ops-link --dry-run --limit 20
  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py ops-link --apply --limit 20

Bash / Cloud Agent:

  .venv/bin/python scripts/backfill_projects_gfolder.py --dry-run --limit 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.drive import DriveNotConfigured, DriveUploader  # noqa: E402
from adapters.monday import jobcheck as mj  # noqa: E402
from adapters.monday.client import MondayClient  # noqa: E402
from shared.boards import (  # noqa: E402
    OPERATIONS_BOARD_ID,
    PROJECTS_BOARD_ID,
    PROJECTS_GFOLDER_COL,
)
from subsystems.jobstart import naming  # noqa: E402

# Ops↔Projects link backfill uses a stricter bar than adopt-or-create (0.5).
OPS_LINK_THRESHOLD = 0.85

# Projects closed group + Lost — still backfillable, but CO rows are never jobs.
_PROJECTS_SKIP_CO_PREFIX = "CO."


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def is_empty_gfolder(url_or_text: Optional[str]) -> bool:
    """True when a GFolder Link cell has nothing usable."""
    s = (url_or_text or "").strip()
    if not s:
        return True
    if s.casefold() in {"gfolder", "link", "-"}:
        return True
    return False


def pick_ops_project_match(ops_name: str, projects: list[dict],
                           *, threshold: float = OPS_LINK_THRESHOLD
                           ) -> Optional[dict]:
    """
    PURE. Pick a Projects candidate for an Ops item name, or None.

    Uses naming.best_match with a high threshold (default 0.85). Ambiguous
    top-2 and below-threshold scores return None.
    """
    return naming.best_match(ops_name, projects, threshold=threshold)


# ---------------------------------------------------------------------------
# Monday reads
# ---------------------------------------------------------------------------

def list_projects_missing_gfolder(mc, *, limit: Optional[int] = None
                                  ) -> list[dict]:
    """
    Page the Projects board; return items whose GFolder Link is empty.
    Skips top-level CO.{n} rows. Each row: {item_id, name, group_title}.
    """
    gcol = PROJECTS_GFOLDER_COL
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
            }
          }
        }
      }
    }
    """
    out: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {
            "boardId": [str(PROJECTS_BOARD_ID)],
            "cursor": cursor,
            "cols": [gcol],
        })
        boards = data.get("boards") or []
        if not boards:
            break
        page = boards[0]["items_page"]
        for item in page.get("items") or []:
            name = (item.get("name") or "").strip()
            if name.startswith(_PROJECTS_SKIP_CO_PREFIX):
                continue
            gurl = None
            for cv in item.get("column_values") or []:
                if cv.get("id") == gcol:
                    gurl = mj._link_column_url(cv)
                    break
            if not is_empty_gfolder(gurl):
                continue
            group = item.get("group") or {}
            out.append({
                "item_id": int(item["id"]),
                "name": name,
                "group_title": group.get("title"),
            })
            if limit is not None and len(out) >= limit:
                return out
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


def list_all_project_names(mc) -> list[dict]:
    """Every Projects item as {id, name} (incl. CO rows — caller filters)."""
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
    out: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {
            "boardId": [str(PROJECTS_BOARD_ID)],
            "cursor": cursor,
        })
        boards = data.get("boards") or []
        if not boards:
            break
        page = boards[0]["items_page"]
        for item in page.get("items") or []:
            name = (item.get("name") or "").strip()
            if not name or name.startswith(_PROJECTS_SKIP_CO_PREFIX):
                continue
            out.append({"id": int(item["id"]), "name": name})
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


def list_ops_missing_project_link(mc, *, limit: Optional[int] = None
                                  ) -> list[dict]:
    """
    Page Operations; return items whose link_to_projects is empty.
    Each row: {item_id, name, group_title}.
    """
    col = mj.CONTEXT_COL_PROJECT_LINK
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
    out: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {
            "boardId": [str(OPERATIONS_BOARD_ID)],
            "cursor": cursor,
            "cols": [col],
        })
        boards = data.get("boards") or []
        if not boards:
            break
        page = boards[0]["items_page"]
        for item in page.get("items") or []:
            linked: list = []
            for cv in item.get("column_values") or []:
                if cv.get("id") != col:
                    continue
                linked = list(cv.get("linked_item_ids") or [])
                if not linked:
                    linked = [x.get("id") for x in (cv.get("linked_items") or [])
                              if x and x.get("id")]
                break
            if linked:
                continue
            name = (item.get("name") or "").strip()
            if not name:
                continue
            group = item.get("group") or {}
            out.append({
                "item_id": int(item["id"]),
                "name": name,
                "group_title": group.get("title"),
            })
            if limit is not None and len(out) >= limit:
                return out
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_gfolder(*, apply: bool, limit: Optional[int]) -> int:
    print(f"[gfolder] mode={'APPLY' if apply else 'DRY-RUN'} "
          f"limit={limit or 'none'}", file=sys.stderr)
    try:
        mc = MondayClient()
    except Exception as e:  # noqa: BLE001
        print(f"Monday not configured: {e}", file=sys.stderr)
        return 2
    try:
        drive = DriveUploader()
    except DriveNotConfigured as e:
        print(f"Drive not configured: {e}", file=sys.stderr)
        return 2

    print("[gfolder] listing Projects items with empty GFolder…",
          file=sys.stderr)
    rows = list_projects_missing_gfolder(mc, limit=limit)
    print(f"[gfolder] {len(rows)} candidate(s)", file=sys.stderr)

    proposed = matched = written = skipped = no_match = errors = 0
    for i, row in enumerate(rows, 1):
        name = row["name"]
        iid = row["item_id"]
        print(f"[gfolder] ({i}/{len(rows)}) {name!r} …", file=sys.stderr)
        try:
            hit = drive.find_projects_job_folder(job_hint=name)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"ERROR   {iid:<12} {name:<48} drive: {type(e).__name__}: {e}")
            continue
        if not hit:
            no_match += 1
            print(f"NO_MATCH {iid:<12} {name}")
            continue
        url = hit.get("webViewLink") or ""
        score = hit.get("score")
        score_s = f"{score:.2f}" if isinstance(score, (int, float)) else "?"
        matched += 1
        proposed += 1
        if not apply:
            print(f"WOULD   {iid:<12} {name:<48} → {hit.get('name')!r} "
                  f"(score {score_s}) {url}")
            continue
        result = mj.set_projects_gfolder_if_empty(mc, iid, url)
        if result.get("skipped"):
            skipped += 1
            print(f"SKIP    {iid:<12} {name:<48} already set: "
                  f"{result.get('gfolder_url')}")
        elif result.get("written"):
            written += 1
            print(f"WROTE   {iid:<12} {name:<48} → {hit.get('name')!r} "
                  f"(score {score_s})")
        else:
            errors += 1
            print(f"ERROR   {iid:<12} {name:<48} "
                  f"{result.get('error') or result.get('reason')}")

    print(f"\n[gfolder] done. candidates={len(rows)} matched={matched} "
          f"proposed={proposed} written={written} skipped={skipped} "
          f"no_match={no_match} errors={errors}"
          f"{'' if apply else ' (dry-run — pass --apply to write)'}",
          file=sys.stderr)
    return 0 if errors == 0 else 1


def run_ops_link(*, apply: bool, limit: Optional[int]) -> int:
    print(f"[ops-link] mode={'APPLY' if apply else 'DRY-RUN'} "
          f"threshold={OPS_LINK_THRESHOLD} limit={limit or 'none'}",
          file=sys.stderr)
    try:
        mc = MondayClient()
    except Exception as e:  # noqa: BLE001
        print(f"Monday not configured: {e}", file=sys.stderr)
        return 2

    print("[ops-link] loading Projects name index…", file=sys.stderr)
    projects = list_all_project_names(mc)
    print(f"[ops-link] {len(projects)} Projects item(s)", file=sys.stderr)

    print("[ops-link] listing Ops items with empty link_to_projects…",
          file=sys.stderr)
    rows = list_ops_missing_project_link(mc, limit=limit)
    print(f"[ops-link] {len(rows)} candidate(s)", file=sys.stderr)

    proposed = matched = written = skipped = no_match = errors = 0
    for i, row in enumerate(rows, 1):
        name = row["name"]
        iid = row["item_id"]
        print(f"[ops-link] ({i}/{len(rows)}) {name!r} …", file=sys.stderr)
        hit = pick_ops_project_match(name, projects, threshold=OPS_LINK_THRESHOLD)
        if not hit:
            no_match += 1
            print(f"NO_MATCH {iid:<12} {name}")
            continue
        matched += 1
        proposed += 1
        score = hit.get("score")
        score_s = f"{score:.2f}" if isinstance(score, (int, float)) else "?"
        pid = int(hit["id"])
        if not apply:
            print(f"WOULD   {iid:<12} {name:<48} → Projects {pid} "
                  f"{hit.get('name')!r} (score {score_s} how={hit.get('how')})")
            continue
        result = mj.set_ops_project_link_if_empty(mc, iid, pid)
        if result.get("skipped"):
            skipped += 1
            print(f"SKIP    {iid:<12} {name:<48} already linked → "
                  f"{result.get('project_item_id')}")
        elif result.get("written"):
            written += 1
            print(f"WROTE   {iid:<12} {name:<48} → Projects {pid} "
                  f"{hit.get('name')!r} (score {score_s})")
        else:
            errors += 1
            print(f"ERROR   {iid:<12} {name:<48} "
                  f"{result.get('error') or result.get('reason')}")

    print(f"\n[ops-link] done. candidates={len(rows)} matched={matched} "
          f"proposed={proposed} written={written} skipped={skipped} "
          f"no_match={no_match} errors={errors}"
          f"{'' if apply else ' (dry-run — pass --apply to write)'}",
          file=sys.stderr)
    return 0 if errors == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=("Backfill Projects GFolder Link and/or Ops→Projects "
                     "links. Dry-run by default."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py "
            "--dry-run --limit 20\n"
            "  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py "
            "--apply --limit 20\n"
            "  .venv\\Scripts\\python scripts/backfill_projects_gfolder.py "
            "ops-link --dry-run --limit 20\n"
        ),
    )
    p.add_argument(
        "mode",
        nargs="?",
        default="gfolder",
        choices=("gfolder", "ops-link"),
        help="gfolder (default) | ops-link",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print proposed stamps only (default behaviour if --apply omitted).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write fill-if-empty stamps to Monday.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Cap how many empty-link candidates to process.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    # Dry-run is the default; --dry-run wins over --apply for safety.
    apply = bool(args.apply) and not bool(args.dry_run)
    if args.apply and args.dry_run:
        print("[warn] both --apply and --dry-run set; staying in dry-run",
              file=sys.stderr)
    if args.mode == "ops-link":
        return run_ops_link(apply=apply, limit=args.limit)
    return run_gfolder(apply=apply, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
