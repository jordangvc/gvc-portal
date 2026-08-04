"""
Secret-free route and takeoff-contract smoke checks for agents and CI.

Run from the repository root:

    PYTHONPATH=. .venv/bin/python scripts/smoke_hub_daily.py --self-check
    PYTHONPATH=. .venv/bin/python scripts/smoke_hub_daily.py --contract

With no mode flags, both checks run. Any failed assertion returns a non-zero
exit status. Importing app.service may print expected missing-integration
warnings; no external service is called.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from subsystems.estimate.number import ESTIMATE_NUMBER_RE

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ESTIMATE = ROOT / "example_estimate.json"

REQUIRED_ROUTES = (
    "/ui/jobstart",
    "/ui/morning",
    "/ui/jobcheck",
    "/ui/estimate",
    "/ui/api/morning/photo-ready",
    "/ui/api/morning/suggest-links",
)
OPTIONAL_ROUTES = (
    "/ui/api/estimate/from-takeoff",
)

# The takeoff import is landing independently from this scripts/docs branch.
# Keep discovery narrow and explicit so this smoke check never imports every
# package in the repo looking for a function with a vague name.
NORMALIZER_CANDIDATES = (
    ("subsystems.estimate.takeoff_import", "normalize_takeoff_payload"),
    ("subsystems.estimate.takeoff", "normalize_takeoff_estimate"),
    ("subsystems.estimate.takeoff", "normalize_estimate"),
    ("subsystems.estimate.takeoff_import", "normalize_takeoff_estimate"),
    ("subsystems.takeoff.contract", "normalize_estimate"),
    ("subsystems.takeoff", "normalize_estimate"),
    ("orchestrators.takeoff_flow", "normalize_estimate"),
)


def registered_route_paths(app: Any) -> set[str]:
    """Return registered FastAPI paths. Pure for an already-created app."""
    return {
        str(getattr(route, "path", ""))
        for route in getattr(app, "routes", ())
        if getattr(route, "path", None)
    }


def check_routes(paths: set[str]) -> tuple[list[str], list[str]]:
    """Return (missing required, present optional) for a route-path set."""
    missing = [path for path in REQUIRED_ROUTES if path not in paths]
    optional_present = [path for path in OPTIONAL_ROUTES if path in paths]
    return missing, optional_present


def run_self_check() -> bool:
    """Import app.service and verify the daily hub's critical route surface."""
    try:
        service = importlib.import_module("app.service")
    except Exception as error:  # noqa: BLE001 — import smoke must report any failure
        print(f"FAIL import app.service: {type(error).__name__}: {error}")
        return False

    paths = registered_route_paths(service.app)
    missing, optional_present = check_routes(paths)
    for path in REQUIRED_ROUTES:
        state = "PASS" if path in paths else "FAIL"
        print(f"{state} route {path}")
    for path in OPTIONAL_ROUTES:
        state = "PASS" if path in optional_present else "SKIP"
        detail = "present" if state == "PASS" else "optional route not present"
        print(f"{state} route {path} ({detail})")
    if missing:
        print(f"FAIL route smoke: {len(missing)} required route(s) missing")
        return False
    print(f"PASS route smoke: {len(REQUIRED_ROUTES)} required route(s) present")
    return True


def find_takeoff_normalizer() -> tuple[Optional[Callable[[dict], dict]], Optional[str]]:
    """Find a known takeoff normalizer without requiring that feature branch."""
    for module_name, function_name in NORMALIZER_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            # Only suppress the candidate module/package being absent. A missing
            # dependency inside an existing candidate is a real contract failure.
            if error.name == module_name or module_name.startswith(f"{error.name}."):
                continue
            raise
        normalizer = getattr(module, function_name, None)
        if callable(normalizer):
            return normalizer, f"{module_name}.{function_name}"
    return None, None


def validate_contract_shape(payload: Any) -> list[str]:
    """Small structural validation of the cross-repo example contract."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["top-level payload must be an object"]
    for key in ("prepared_by", "client", "job", "estimate"):
        if not isinstance(payload.get(key), dict):
            errors.append(f"{key} must be an object")
    estimate = payload.get("estimate") or {}
    lines = estimate.get("line_items")
    if not isinstance(lines, list) or not lines:
        errors.append("estimate.line_items must be a non-empty list")
    else:
        for index, line in enumerate(lines):
            if not isinstance(line, dict):
                errors.append(f"estimate.line_items[{index}] must be an object")
                continue
            for key in ("description", "unit_price", "quantity"):
                if key not in line:
                    errors.append(f"estimate.line_items[{index}].{key} is required")
    return errors


def identifier_issue(payload: dict) -> Optional[str]:
    """
    Return why the estimate id is incompatible, or None.

    Blank is valid for takeoff import because the portal assigns the canonical
    YYYY-MMDD-NNN number at finalize.
    """
    identifier = str(((payload.get("estimate") or {}).get("identifier")) or "").strip()
    if not identifier:
        return None
    if ESTIMATE_NUMBER_RE.fullmatch(identifier):
        return None
    return (
        f"identifier {identifier!r} is not blank or EST-YYYY-MMDD-NNN "
        "(bare YYYY-MMDD-NNN also accepted)"
    )


def run_contract_check() -> bool:
    """Load the shared example and run a takeoff normalizer when one is present."""
    try:
        payload = json.loads(EXAMPLE_ESTIMATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL contract load {EXAMPLE_ESTIMATE}: {error}")
        return False

    errors = validate_contract_shape(payload)
    if errors:
        for error in errors:
            print(f"FAIL contract shape: {error}")
        return False
    print("PASS contract shape: example_estimate.json")

    try:
        normalizer, label = find_takeoff_normalizer()
    except Exception as error:  # noqa: BLE001 — dependency/import failure is actionable
        print(f"FAIL takeoff normalizer import: {type(error).__name__}: {error}")
        return False

    normalized = copy.deepcopy(payload)
    if normalizer is not None:
        try:
            result = normalizer(normalized)
        except Exception as error:  # noqa: BLE001 — smoke reports the integration error
            print(f"FAIL takeoff normalizer {label}: {type(error).__name__}: {error}")
            return False
        if result is not None:
            if not isinstance(result, dict):
                print(f"FAIL takeoff normalizer {label}: returned {type(result).__name__}")
                return False
            normalized = result
        print(f"PASS takeoff normalizer: {label}")
    else:
        print("SKIP takeoff normalizer: no known takeoff import module is present")

    issue = identifier_issue(normalized)
    if issue:
        prefix = "after normalization" if normalizer is not None else "in shared example"
        print(f"FAIL estimate identifier {prefix}: {issue}")
        if normalizer is None:
            print(
                "INFO expected future fix: takeoff import normalization should clear "
                "legacy EST-* identifiers before staging the portal draft"
            )
        return False
    print("PASS estimate identifier: blank or canonical portal format")
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Secret-free daily hub smoke checks for agents and CI.",
    )
    parser.add_argument("--self-check", action="store_true",
                        help="Import app.service and assert critical routes.")
    parser.add_argument("--contract", action="store_true",
                        help="Validate the takeoff estimate JSON contract.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    run_routes = args.self_check or not (args.self_check or args.contract)
    run_contract = args.contract or not (args.self_check or args.contract)
    passed = True
    if run_routes:
        passed = run_self_check() and passed
    if run_contract:
        passed = run_contract_check() and passed
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
