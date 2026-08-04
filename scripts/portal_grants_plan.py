"""
Plan portal grants without changing the grants store.

Run from the repository root so ``shared`` is importable:

    PYTHONPATH=. .venv/bin/python scripts/portal_grants_plan.py --print
    PYTHONPATH=. .venv/bin/python scripts/portal_grants_plan.py --json
    PYTHONPATH=. .venv/bin/python scripts/portal_grants_plan.py --diff grants.json
    Get-Content grants.json | .venv\Scripts\python scripts\portal_grants_plan.py --diff -

This script is intentionally read-only. It never imports portal_store and never
calls the admin API. An authenticated admin applies reviewed changes in
``/ui/admin``; the generated API snippets are an explicit, human-run alternative.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, TextIO

from shared import access
from shared.auth import SESSION_COOKIE

ADMIN_API_PATH = "/ui/api/admin/users"
PORTAL_ORIGIN = "https://portal.greenvalleycontractors.com"

# Stored grants only. Every provisioned user also receives access.BASELINE
# automatically, so repeating morning/timeoff/fieldguide here would create noise.
# Role templates have no email and are guidance for future crew members.
GRANT_MATRIX: tuple[dict[str, Any], ...] = (
    {
        "name": "Jordan",
        "email": "jordan@greenvalleycontractors.com",
        "role": "owner",
        "features": ("*",),
        "reason": "Break-glass owner and portal administrator.",
    },
    {
        "name": "Andrea",
        "email": "andrea@greenvalleycontractors.com",
        "role": "office_admin",
        "features": ("*",),
        "reason": "Office administrator for billing, estimates, and access.",
    },
    {
        "name": "Jake",
        "email": "jake@greenvalleycontractors.com",
        "role": "sales",
        "features": ("estimate", "takeoff", "jobstart"),
        "reason": "Build bids and send complete won-job packets to Operations.",
    },
    {
        "name": "Mark",
        "email": "mark@greenvalleycontractors.com",
        "role": "operations_lead",
        "features": ("morning_ops", "jobcheck", "jobstart"),
        "reason": "Run field work, review handoffs, and update Job Check.",
    },
    {
        "name": "Robert",
        "email": "robert@greenvalleycontractors.com",
        "role": "operations",
        "features": ("morning_ops", "jobcheck", "jobstart"),
        "reason": "Review handoffs and update active field work.",
    },
    {
        "name": "Crew member",
        "email": None,
        "role": "crew",
        "features": ("morning_ops", "jobcheck"),
        "reason": "Template: daily work and Job Check, without sales or finance.",
    },
)

AUTOMATABLE_STEPS = (
    "Validate every recommended feature against shared.access.FEATURES.",
    "Print the matrix, emit machine-readable JSON, and diff a grants export.",
    "Generate admin API commands for missing or changed named users.",
)

HUMAN_ONLY_STEPS = (
    "Confirm each employee's current Workspace email and employment status.",
    "Review least-privilege differences; this planner never removes extra users.",
    "Sign in as an admin and apply approved changes in /ui/admin (preferred).",
    "If using generated API commands, supply an authenticated portal session cookie.",
)


def validate_matrix(matrix: tuple[dict[str, Any], ...] = GRANT_MATRIX) -> list[str]:
    """Return matrix validation errors. Pure; an empty list means valid."""
    errors: list[str] = []
    seen_emails: set[str] = set()
    for row in matrix:
        features = tuple(row.get("features") or ())
        unknown = sorted(
            feature for feature in features
            if feature != access.WILDCARD and feature not in access.ALL_FEATURES
        )
        if unknown:
            errors.append(f"{row.get('name')}: unknown features {unknown}")
        email = (row.get("email") or "").strip().lower()
        if email:
            if email in seen_emails:
                errors.append(f"duplicate email: {email}")
            seen_emails.add(email)
        elif row.get("role") != "crew":
            errors.append(f"{row.get('name')}: only a role template may omit email")
    return errors


def desired_users(
    matrix: tuple[dict[str, Any], ...] = GRANT_MATRIX,
) -> dict[str, dict[str, Any]]:
    """Named desired users keyed by normalized email. Role templates are omitted."""
    errors = validate_matrix(matrix)
    if errors:
        raise ValueError("; ".join(errors))
    users: dict[str, dict[str, Any]] = {}
    for row in matrix:
        email = (row.get("email") or "").strip().lower()
        if not email:
            continue
        users[email] = {
            "features": sorted(set(row["features"])),
            "person": {"name": row["name"]},
            "role": row["role"],
        }
    return users


def normalize_current_users(document: Any) -> dict[str, list[str]]:
    """
    Normalize grants.json or the GET /ui/api/admin/users response.

    Accepted shapes:
      {"version": 1, "users": {"email": {"features": [...]}}}
      {"users": [{"email": "...", "features": [...]}]}
      {"email": {"features": [...]}
    """
    if not isinstance(document, dict):
        raise ValueError("grants input must be a JSON object")
    raw_users = document.get("users", document)
    out: dict[str, list[str]] = {}
    if isinstance(raw_users, list):
        entries = (
            ((row or {}).get("email"), row or {})
            for row in raw_users
            if isinstance(row, dict)
        )
    elif isinstance(raw_users, dict):
        entries = raw_users.items()
    else:
        raise ValueError("grants input 'users' must be an object or list")

    for raw_email, raw_record in entries:
        email = str(raw_email or "").strip().lower()
        if not email:
            continue
        record = raw_record if isinstance(raw_record, dict) else {}
        features = record.get("features") or []
        if not isinstance(features, list):
            raise ValueError(f"{email}: features must be a list")
        out[email] = sorted(
            {str(feature).strip().lower() for feature in features if str(feature).strip()}
        )
    return out


def diff_users(
    current: dict[str, list[str]],
    desired: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Compare desired named users to current stored grants. Pure and read-only."""
    desired = desired or desired_users()
    rows: list[dict[str, Any]] = []
    for email, target in desired.items():
        wanted = target["features"]
        existing = current.get(email)
        if existing is None:
            status = "missing"
            existing = []
        elif set(existing) == set(wanted):
            status = "ok"
        else:
            status = "change"
        rows.append({
            "email": email,
            "name": (target.get("person") or {}).get("name"),
            "role": target.get("role"),
            "status": status,
            "current": sorted(existing),
            "desired": sorted(wanted),
            "add": sorted(set(wanted) - set(existing)),
            "remove": sorted(set(existing) - set(wanted)),
        })
    for email in sorted(set(current) - set(desired)):
        rows.append({
            "email": email,
            "name": None,
            "role": None,
            "status": "unmanaged",
            "current": current[email],
            "desired": None,
            "add": [],
            "remove": [],
        })
    return rows


def _feature_text(features: Optional[list[str] | tuple[str, ...]]) -> str:
    return ", ".join(features or ()) or "(baseline only)"


def _print_table(rows: list[list[str]], output: TextIO) -> None:
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row_number, row in enumerate(rows):
        print(
            "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)),
            file=output,
        )
        if row_number == 0:
            print("  ".join("-" * width for width in widths), file=output)


def print_plan(output: TextIO = sys.stdout) -> None:
    """Print the recommended matrix and the human/automation boundary."""
    errors = validate_matrix()
    if errors:
        raise ValueError("; ".join(errors))
    rows = [["Person", "Role", "Stored grants", "Email"]]
    for row in GRANT_MATRIX:
        rows.append([
            row["name"],
            row["role"],
            _feature_text(row["features"]),
            row.get("email") or "(role template)",
        ])
    _print_table(rows, output)
    print(f"\nAutomatic baseline: {_feature_text(sorted(access.BASELINE))}", file=output)
    print("\nAutomatable:", file=output)
    for step in AUTOMATABLE_STEPS:
        print(f"  - {step}", file=output)
    print("\nHuman-only:", file=output)
    for step in HUMAN_ONLY_STEPS:
        print(f"  - {step}", file=output)
    print(
        "\nNo writes were attempted. Apply reviewed grants in /ui/admin.",
        file=output,
    )


def plan_as_json() -> dict[str, Any]:
    """Machine-readable plan with explicit boundaries."""
    return {
        "ok": not validate_matrix(),
        "features_catalog": list(access.FEATURES),
        "automatic_baseline": sorted(access.BASELINE),
        "matrix": [
            {
                **row,
                "features": list(row["features"]),
                "template": row.get("email") is None,
            }
            for row in GRANT_MATRIX
        ],
        "automatable": list(AUTOMATABLE_STEPS),
        "human_only": list(HUMAN_ONLY_STEPS),
        "writes": False,
    }


def _api_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": row["email"],
        "features": row["desired"],
        "person": {"name": row["name"]},
    }


def print_admin_snippets(changes: list[dict[str, Any]], output: TextIO = sys.stdout) -> None:
    """Print exact, human-run upserts for named missing/changed users."""
    actionable = [row for row in changes if row["status"] in {"missing", "change"}]
    if not actionable:
        print("\nNo admin upserts are needed.", file=output)
        return

    endpoint = f"{PORTAL_ORIGIN}{ADMIN_API_PATH}"
    print("\nPreferred: review and apply these rows in /ui/admin.", file=output)
    print(
        "Advanced alternative: paste the signed portal cookie from an authenticated "
        "admin session. These commands WRITE; this planner does not run them.",
        file=output,
    )
    print("\nPowerShell:", file=output)
    print(
        "$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession",
        file=output,
    )
    print(
        f'$session.Cookies.Add([System.Net.Cookie]::new("{SESSION_COOKIE}", '
        f'"<paste-signed-session-cookie>", "/", "portal.greenvalleycontractors.com"))',
        file=output,
    )
    for row in actionable:
        body = json.dumps(_api_payload(row), separators=(",", ":"))
        print(f"$body = '{body}'", file=output)
        print(
            f'Invoke-RestMethod -Method Post -Uri "{endpoint}" -WebSession $session '
            '-ContentType "application/json" -Body $body',
            file=output,
        )

    print("\ncurl:", file=output)
    for row in actionable:
        body = json.dumps(_api_payload(row), separators=(",", ":"))
        print(
            f"curl.exe --fail-with-body --request POST \"{endpoint}\" "
            f"--cookie \"{SESSION_COOKIE}=<paste-signed-session-cookie>\" "
            f"--header \"Content-Type: application/json\" --data-raw '{body}'",
            file=output,
        )


def _read_json(path: str, stdin: TextIO = sys.stdin) -> Any:
    if path == "-":
        return json.load(stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def print_diff(document: Any, output: TextIO = sys.stdout) -> list[dict[str, Any]]:
    current = normalize_current_users(document)
    rows = diff_users(current)
    table = [["Status", "Email", "Current", "Desired"]]
    for row in rows:
        table.append([
            row["status"].upper(),
            row["email"],
            _feature_text(row["current"]),
            _feature_text(row["desired"]) if row["desired"] is not None else "(not managed)",
        ])
    _print_table(table, output)
    print(
        "\nUNMANAGED means present in the export but outside this recommendation; "
        "the planner never proposes deletion.",
        file=output,
    )
    print_admin_snippets(rows, output)
    return rows


def self_test() -> None:
    """Small pure-function checks; no store, network, or secrets."""
    assert validate_matrix() == []
    users = desired_users()
    assert users["jordan@greenvalleycontractors.com"]["features"] == ["*"]
    assert "crew" not in users
    current = normalize_current_users({
        "version": 1,
        "users": {
            "jake@greenvalleycontractors.com": {
                "features": ["takeoff", "estimate", "jobstart"],
            },
            "extra@greenvalleycontractors.com": {"features": ["jobcheck"]},
        },
    })
    rows = {row["email"]: row for row in diff_users(current)}
    assert rows["jake@greenvalleycontractors.com"]["status"] == "ok"
    assert rows["mark@greenvalleycontractors.com"]["status"] == "missing"
    assert rows["extra@greenvalleycontractors.com"]["status"] == "unmanaged"
    assert all(feature in access.ALL_FEATURES for feature in ("jobstart", "morning_ops"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only recommended portal grants planner.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--print", dest="print_mode", action="store_true",
                      help="Print the human-readable matrix (default).")
    mode.add_argument("--json", action="store_true",
                      help="Print the machine-readable plan.")
    mode.add_argument(
        "--diff",
        nargs="?",
        const="-",
        metavar="GRANTS_JSON",
        help="Compare with a grants.json path; omit the path or use '-' for stdin.",
    )
    mode.add_argument("--self-test", action="store_true",
                      help="Run pure assertions without network access.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.self_test:
            self_test()
            print("PASS portal_grants_plan self-test")
            return 0
        if args.json:
            print(json.dumps(plan_as_json(), indent=2))
            return 0
        if args.diff is not None:
            print_diff(_read_json(args.diff))
            return 0
        print_plan()
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL portal_grants_plan: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
