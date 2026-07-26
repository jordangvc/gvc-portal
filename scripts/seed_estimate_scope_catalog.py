"""
Seed (or replace) the standard-scope catalog in the portal state bucket.
=========================================================================
The estimate form works WITHOUT this — load_catalog() falls back to the shipped
DEFAULT_CATALOG when no object exists. Running this simply writes that default
(or a file you supply) into GCS so it shows as "stored" and admins can edit it
from the estimate page. Handy for the initial seed and for resetting.

Run locally from the repo root with the usual env available (.env is loaded):
    python scripts/seed_estimate_scope_catalog.py                # write defaults
    python scripts/seed_estimate_scope_catalog.py --from-file catalog.json
    python scripts/seed_estimate_scope_catalog.py --force        # overwrite existing

Requires GVC_PORTAL_STATE_BUCKET (or GVC_GCS_PREVIEW_BUCKET) + the service
account JSON (GVC_DRIVE_CREDENTIALS / .google-service-account.json).
Idempotent: re-running overwrites in place; the state bucket's object
versioning keeps every prior catalog recoverable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from shared import paths

load_dotenv(paths.ENV_FILE)

from subsystems.estimate import scope_catalog as sc  # noqa: E402 — after .env


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-file", default="",
                    help="Path to a catalog JSON to seed (defaults to the shipped catalog)")
    ap.add_argument("--actor", default="jordan@greenvalleycontractors.com",
                    help="Recorded as updated_by in the catalog")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite even if a catalog is already stored")
    args = ap.parse_args()

    info = sc.catalog_info()
    if info.get("source") == "stored" and not args.force:
        print(f"A catalog is already stored (updated_by={info.get('updated_by')}, "
              f"updated_at={info.get('updated_at')}). Re-run with --force to overwrite.")
        return 0

    if args.from_file:
        path = Path(args.from_file).expanduser()
        if not path.exists():
            print(f"ERROR: {path} not found.", file=sys.stderr)
            return 2
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = sc.default_catalog()

    try:
        stored = sc.put_catalog(raw, actor=args.actor)
    except sc.ScopeCatalogInvalid as e:
        print(f"ERROR: catalog rejected — {e}", file=sys.stderr)
        return 1
    except sc.PortalStoreNotConfigured as e:
        print(f"ERROR: store not configured — {e}", file=sys.stderr)
        return 1

    n_trades, n_scopes = sc.catalog_counts(stored)
    print(f"Catalog stored: {n_trades} trades, {n_scopes} scopes "
          f"(updated_by={stored.get('updated_by')}).")
    for trade in stored.get("trades", []):
        titles = ", ".join(s["title"] for s in trade.get("scopes", []))
        print(f"  {trade['name']}: {titles}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
