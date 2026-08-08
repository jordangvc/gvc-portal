#!/usr/bin/env python3
"""
Lightweight UI consistency lint for GVC portal pages.

Fails on high-confidence anti-patterns that fight docs/UI-SYSTEM.md:
  - linear-gradient in web/*.html style (Command forbids gradients)
  - pages missing gvc.css
  - tool pages missing gvc-topbar (hub excluded)
  - undefined-looking token names that historically drifted

Exit 0 = clean. Exit 1 = violations. Run from repo root:

    python scripts/ui_consistency_check.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

# Hub intentionally uses a different shell.
SKIP_TOPBAR = {"hub.html"}
# Asset / non-page files
SKIP_FILES = {
    "gvc.css", "gvc-theme.js", "gvc-command.js", "gvc-status-picker.js",
}

GRAD_RE = re.compile(r"linear-gradient\s*\(", re.I)
GVC_CSS_RE = re.compile(r"/ui/gvc\.css")
TOPBAR_RE = re.compile(r"gvc-topbar|hub-rail|hub-app")
# Tokens that must resolve via aliases in gvc.css — flag raw typos instead.
BAD_TOKEN_RE = re.compile(
    r"var\(\s*--gvc-(radii|fontsize|space0|green-light-tint)\b"
)


def html_pages() -> list[Path]:
    return sorted(
        p for p in WEB.glob("*.html")
        if p.name not in SKIP_FILES
    )


def main() -> int:
    violations: list[str] = []
    pages = html_pages()
    if not pages:
        print("No web/*.html pages found", file=sys.stderr)
        return 1

    for path in pages:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()

        if path.suffix == ".html" and not GVC_CSS_RE.search(text):
            violations.append(f"{rel}: missing link to /ui/gvc.css")

        if path.name not in SKIP_TOPBAR and path.suffix == ".html":
            if not TOPBAR_RE.search(text):
                violations.append(
                    f"{rel}: no gvc-topbar / hub shell marker"
                )

        # Gradients in page CSS fight Command + the shared .btn--commit solid gold.
        if GRAD_RE.search(text):
            # Allow data-URI / comments mentioning the word only if real usage.
            for i, line in enumerate(text.splitlines(), 1):
                if GRAD_RE.search(line) and "/*" not in line:
                    violations.append(
                        f"{rel}:{i}: linear-gradient forbidden in portal UI "
                        f"(use solid --gvc-gold / .btn--commit)"
                    )

        for i, line in enumerate(text.splitlines(), 1):
            if BAD_TOKEN_RE.search(line):
                violations.append(f"{rel}:{i}: unknown --gvc-* token")

    # Shared CSS must keep the aliases pages already use.
    css = (WEB / "gvc.css").read_text(encoding="utf-8")
    for token in (
        "--gvc-radius:", "--gvc-fs-sm:", "--gvc-green-tint:",
        ".btn--commit", ".gvc-empty",
    ):
        if token not in css:
            violations.append(f"web/gvc.css: missing required rule/token {token!r}")

    if violations:
        print(f"UI consistency check FAILED ({len(violations)}):")
        for v in violations:
            print(f"  - {v}")
        print("\nSee docs/UI-SYSTEM.md and docs/UX-CHECKLIST.md")
        return 1

    print(f"UI consistency check OK ({len(pages)} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
