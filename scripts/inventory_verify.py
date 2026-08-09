"""One-command inventory release check (docs/inventory/TEST_PLAN.md).

    python scripts/inventory_verify.py

Runs: compileall on touched packages → inventory pytest slice → adjacent
invariant suites → node --check on inventory JS/pages → app import (route
registration). Exits non-zero on the first failure. CI's full gate is a
superset; this is the fast local loop.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT)}

STEPS: list[tuple[str, list[str]]] = [
    ("compile", [sys.executable, "-m", "compileall", "-q",
                 "subsystems/inventory", "orchestrators/inventory_flow.py",
                 "app/service.py", "scripts/seed_inventory.py"]),
    ("pytest inventory", [sys.executable, "-m", "pytest", "-q", "--tb=short",
                          "tests/test_inventory_domain.py",
                          "tests/test_inventory_ledger.py",
                          "tests/test_inventory_api.py",
                          "tests/test_inventory_ui.py",
                          "tests/test_inventory_admin_ui.py",
                          "tests/test_inventory_review_fixes.py"]),
    ("pytest adjacents", [sys.executable, "-m", "pytest", "-q", "--tb=short",
                          "tests/test_admin_roles.py",
                          "tests/test_role_home_reachable.py",
                          "tests/test_mobile_baseline.py"]),
]

JS_FILES = ["web/gvc-inventory.js"]
PAGES = ["web/inventory.html", "web/inventory-admin.html"]


def run(label: str, cmd: list[str]) -> None:
    print(f"\n== {label}: {' '.join(cmd[:4])} ...")
    r = subprocess.run(cmd, cwd=str(ROOT), env=ENV)
    if r.returncode != 0:
        print(f"FAIL: {label}")
        raise SystemExit(r.returncode)


def node_check_file(path: Path) -> None:
    r = subprocess.run(["node", "--check", str(path)], cwd=str(ROOT))
    if r.returncode != 0:
        print(f"FAIL: node --check {path}")
        raise SystemExit(1)


def main() -> int:
    for label, cmd in STEPS:
        run(label, cmd)
    print("\n== node --check inventory JS")
    for f in JS_FILES:
        node_check_file(ROOT / f)
    for page in PAGES:
        html = (ROOT / page).read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        src = re.sub(r"\{\{[A-Z0-9_]+\}\}", '"x@y"', "\n".join(scripts))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as tmp:
            tmp.write(src)
            name = tmp.name
        try:
            node_check_file(Path(name))
        finally:
            os.unlink(name)
        print(f"  ok  {page} inline JS")
    print("\nALL INVENTORY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
