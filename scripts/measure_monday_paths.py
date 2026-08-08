"""
Measure Monday GraphQL cost on portal hot paths.
=========================================================================
With MONDAY_API_TOKEN + GVC_MONDAY_TRACE=1:

  .venv/bin/python scripts/measure_monday_paths.py
  .venv/bin/python scripts/measure_monday_paths.py --path billing
  .venv/bin/python scripts/measure_monday_paths.py --path hub --email you@…

Without a token, prints the static cold-path budget (code-derived) and exits 0.

Live runs clear L1 monday_cache first so counts reflect cold GraphQL, not hits.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Static budgets from code audit (1-page lists, cache miss). Pagination and
# per-card GFolder walks multiply these.
COLD_PATH_BUDGET = {
    "billing_hub": {
        "min_graphql": 3,
        "typical_graphql": "3–6+",
        "shape": "3 parallel board walks (ready + enrich, bids, projects)",
        "html_blocks_on_monday": False,
        "api_blocks_live_paint": True,
        "notes": "Ready enrich +1 when linked projects exist; full Ops walk on group-filter fail.",
    },
    "office_hub": {
        "min_graphql": 5,
        "typical_graphql": "5–6+ (no GFolder)",
        "shape": "morning brief ∥ billing (owner also ∥ pulse); hub skips GFolder",
        "html_blocks_on_monday": False,
        "api_blocks_live_paint": True,
        "notes": (
            "Hub build_employee_brief(attach_gfolder=False) — Open Drive is Morning-only. "
            "Morning page still attaches GFolder in PARALLEL (2 GraphQL × unique Ops cards). "
            "Job Check + Morning use separate Ops cache keys → duplicate full walks when both cold. "
            "Boot is /hub only; warm runs AFTER first paint (not concurrent)."
        ),
    },
    "billing_search": {
        "min_graphql": 9,
        "typical_graphql": "9–14 (projects∥bids)",
        "shape": "rich projects (5–7 legs) ∥ rich bids (4 legs)",
        "html_blocks_on_monday": False,
        "api_blocks_live_paint": True,
        "notes": "Wall time should be max(projects, bids), not sum — search_billing parallelizes.",
    },
    "jobcheck_list": {
        "min_graphql": 1,
        "typical_graphql": "1–P pages",
        "shape": "serial Ops pagination",
        "html_blocks_on_monday": False,
        "api_blocks_live_paint": True,
        "notes": "Same Operations board as Morning, different cache key.",
    },
    "jobcheck_detail": {
        "min_graphql": 3,
        "typical_graphql": "3 or 6",
        "shape": "3 parallel reads; +2 if linked Projects trades",
        "html_blocks_on_monday": False,
        "api_blocks_live_paint": True,
        "notes": "",
    },
    "invoice_search": {
        "min_graphql": 5,
        "typical_graphql": "5–7",
        "shape": "parallel field legs + optional spine needles",
        "html_blocks_on_monday": False,
        "api_blocks_live_paint": True,
        "notes": "",
    },
    "client_query": {
        "retries": False,
        "rate_limit_handling": False,
        "timeout_s": 30,
        "notes": "One POST; 429/ComplexityException raise immediately — no backoff.",
    },
}


def _print_budget() -> None:
    print("=== Static cold-path Monday budget (code audit) ===\n")
    for name, row in COLD_PATH_BUDGET.items():
        if name == "client_query":
            print(f"[{name}] retries={row['retries']} "
                  f"429_handling={row['rate_limit_handling']} "
                  f"timeout={row['timeout_s']}s")
            print(f"  {row['notes']}\n")
            continue
        print(f"[{name}]")
        print(f"  min GraphQL: {row['min_graphql']}  typical: {row['typical_graphql']}")
        print(f"  shape: {row['shape']}")
        print(f"  HTML waits on Monday: {row['html_blocks_on_monday']}  "
              f"JSON API blocks live paint: {row['api_blocks_live_paint']}")
        if row.get("notes"):
            print(f"  note: {row['notes']}")
        print()


def _run_live(path: str, email: str) -> int:
    os.environ["GVC_MONDAY_TRACE"] = "1"
    # Force re-read of the enable flag if client was imported earlier.
    import adapters.monday.client as mc_mod
    mc_mod._TRACE_ENABLED = True
    from adapters.monday import cache as monday_cache
    from adapters.monday.client import (
        MondayClient,
        monday_trace_summary,
        reset_monday_trace,
    )

    monday_cache.clear()
    reset_monday_trace()
    # Prove token works before spending board walks.
    MondayClient()

    if path == "billing":
        from orchestrators import billing_flow
        billing_flow.billing_hub_payload()
    elif path == "hub":
        from orchestrators import hub_flow
        hub_flow.build_hub_payload(email)
    elif path == "jobcheck":
        from orchestrators import jobcheck_flow
        jobcheck_flow.list_active_jobs()
    else:
        print(f"Unknown --path {path}", file=sys.stderr)
        return 2

    summary = monday_trace_summary()
    print(json.dumps(summary, indent=2))
    print(
        f"\nRESULT path={path} graphql_calls={summary['count']} "
        f"sum_ms={summary['total_ms']} max_ms={summary['max_ms']} "
        f"rate_limited={summary['rate_limited']}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", choices=("billing", "hub", "jobcheck", "budget"),
                    default="budget")
    ap.add_argument("--email", default="dev-bypass@localhost",
                    help="Hub actor email (needs grants in env/GCS)")
    args = ap.parse_args()

    if args.path == "budget" or not os.environ.get("MONDAY_API_TOKEN"):
        _print_budget()
        if not os.environ.get("MONDAY_API_TOKEN"):
            print("MONDAY_API_TOKEN unset — live timing skipped. "
                  "Export the token and re-run with --path billing|hub|jobcheck.")
            return 0
        if args.path == "budget":
            return 0
    return _run_live(args.path, args.email)


if __name__ == "__main__":
    raise SystemExit(main())
