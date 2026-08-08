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

# Light-only / low-contrast traps that break emerald dark mode.
# Print blocks (@media print) are exempted line-by-line below.
LIGHT_ONLY_BG_RE = re.compile(
    r"background(?:-color)?\s*:\s*#fff(?:fff)?\b",
    re.I,
)
BAD_INLINE_INK_RE = re.compile(
    r"""style\s*=\s*["'][^"']*color\s*:\s*#(?:b91c1c|96763[Bb]|7A1F1F|8[Bb]1[Ee]1[Ee]|777)\b""",
    re.I,
)
BAD_BADGE_ACTIVE_RE = re.compile(
    r"\.badge\.active\s*\{[^}]*#e0f2fe",
    re.I | re.S,
)
BAD_PILL_OPEN_RE = re.compile(
    r"\.pill\.open\s*\{[^}]*#e0f2fe",
    re.I | re.S,
)
REQUIRED_CSS_TOKENS = (
    "--color-danger-ink:",
    "--color-warn-ink:",
    "--color-info-ink:",
    "--color-input-bg:",
    "--color-on-primary:",
    "--color-text-disabled:",
    ".gvc-msg--danger",
    ".gvc-callout--warn",
)

# Orphan custom props that are not defined in gvc.css (except Field Manual's
# private palette). These silently break dark mode / status panels.
# Trailing (?![\w-]) avoids matching --line-soft / --muted-foo.
ORPHAN_VAR_RE = re.compile(
    r"var\(\s*--(?:"
    r"warn-bg|warn-line|err-bg|err-line|ok-bg|ok-line|"
    r"green-dark|line|muted"
    r")(?![\w-])"
)
ORPHAN_VAR_ALLOW = {"fieldguide.html"}


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

        # Skip print-only white backgrounds; flag screen CSS / JS traps.
        in_print = False
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if "@media" in low and "print" in low:
                in_print = True
            if in_print and "}" in line and "@media" not in low:
                # crude: leave print block when a top-level closer appears;
                # nested rules still counted as print until a blank @media ends.
                pass
            if in_print and re.match(r"^\s*}\s*$", line):
                # closing a block; detect end of print media via stack-ish heuristic
                # by watching for `@media print { ... }` end when brace depth hits 0
                pass

        # Simpler print exemption: ignore lines inside `@media print { ... }`
        # via a brace-depth scan from each @media print.
        print_ranges: set[int] = set()
        lines = text.splitlines()
        idx = 0
        while idx < len(lines):
            if re.search(r"@media\s+print\b", lines[idx], re.I):
                depth = 0
                started = False
                j = idx
                while j < len(lines):
                    depth += lines[j].count("{") - lines[j].count("}")
                    if "{" in lines[j]:
                        started = True
                    print_ranges.add(j + 1)
                    j += 1
                    if started and depth <= 0:
                        break
                idx = j
                continue
            idx += 1

        for i, line in enumerate(lines, 1):
            if i in print_ranges:
                continue
            if LIGHT_ONLY_BG_RE.search(line) and "var(" not in line:
                violations.append(
                    f"{rel}:{i}: hard-coded white background breaks dark mode "
                    f"(use --color-input-bg / --gvc-surface)"
                )
            if BAD_INLINE_INK_RE.search(line):
                violations.append(
                    f"{rel}:{i}: hard-coded status ink in style= "
                    f"(use .gvc-msg--danger|warn|muted)"
                )

        if BAD_BADGE_ACTIVE_RE.search(text):
            violations.append(
                f"{rel}: .badge.active still uses light-only #e0f2fe "
                f"(use --color-info-soft / --color-info-ink)"
            )
        if BAD_PILL_OPEN_RE.search(text):
            violations.append(
                f"{rel}: .pill.open still uses light-only #e0f2fe "
                f"(use --color-info-soft / --color-info-ink)"
            )

        if path.name not in ORPHAN_VAR_ALLOW:
            for i, line in enumerate(lines, 1):
                for m in ORPHAN_VAR_RE.finditer(line):
                    violations.append(
                        f"{rel}:{i}: orphan CSS var {m.group(0)} "
                        f"(use --gvc-* / --color-* / .gvc-callout--* / .gvc-msg--*)"
                    )

    # Shared CSS must keep the aliases pages already use.
    css = (WEB / "gvc.css").read_text(encoding="utf-8")
    for token in (
        "--gvc-radius:", "--gvc-fs-sm:", "--gvc-green-tint:",
        ".btn--commit", ".gvc-empty",
        *REQUIRED_CSS_TOKENS,
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
