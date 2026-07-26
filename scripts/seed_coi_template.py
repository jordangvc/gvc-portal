"""
Seed (or replace) the blank COI template in the portal state bucket.
=========================================================================
One-time CLI alternative to the admin upload UI on /ui/coi — useful for the
initial seed before anyone opens the page, and at annual renewal.

Run locally from the repo root with the usual env available (.env is loaded):
    python scripts/seed_coi_template.py \
        --file "~/Downloads/COI - BLANK - expMay_2027.pdf" \
        --expiry-label expMay_2027

Requires GVC_PORTAL_STATE_BUCKET (or GVC_GCS_PREVIEW_BUCKET) + the service
account JSON (GVC_DRIVE_CREDENTIALS / .google-service-account.json).
Idempotent: re-running overwrites in place; the state bucket's object
versioning keeps every prior template recoverable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from shared import paths

load_dotenv(paths.ENV_FILE)

from subsystems.coi.template import (  # noqa: E402 — after .env load
    CoiTemplateInvalid,
    PortalStoreNotConfigured,
    put_template,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", required=True, help="Path to the blank COI PDF")
    ap.add_argument("--expiry-label", default="",
                    help="e.g. expMay_2027 — appears in generated filenames")
    ap.add_argument("--actor", default="jordan@greenvalleycontractors.com",
                    help="Recorded as uploaded_by in the template metadata")
    args = ap.parse_args()

    pdf_path = Path(args.file).expanduser()
    if not pdf_path.exists():
        print(f"ERROR: {pdf_path} not found.", file=sys.stderr)
        return 2

    try:
        meta = put_template(pdf_path.read_bytes(),
                            expiry_label=args.expiry_label, actor=args.actor)
    except CoiTemplateInvalid as e:
        print(f"ERROR: template rejected — {e}", file=sys.stderr)
        return 1
    except PortalStoreNotConfigured as e:
        print(f"ERROR: store not configured — {e}", file=sys.stderr)
        return 1

    print("Template stored:")
    for k, v in meta.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
