#!/usr/bin/env python3
"""Bake catalog ``.nextpath`` blocks into ``web/fieldguide.html``.

Run after adding/changing ``content/fieldguide/procedures/*.json`` next_steps
so Job Check how-tos stay offline-navigable without waiting on the catalog API.

  .venv/bin/python scripts/bake_fieldguide_nextpath.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from subsystems.fieldguide.catalog import clear_cache, load_catalog  # noqa: E402
from subsystems.fieldguide.render import render_nextpath_html  # noqa: E402


def bake(html_path: Path | None = None) -> list[str]:
    clear_cache()
    cat = load_catalog()
    path = html_path or (ROOT / "web" / "fieldguide.html")
    html = path.read_text(encoding="utf-8")
    changed: list[str] = []
    for pid, proc in sorted(cat["by_id"].items()):
        frag = render_nextpath_html(proc, include_related=False, catalog_nav=True)
        if not frag:
            continue
        sec_re = re.compile(
            rf'(<section class="doc" id="{re.escape(pid)}">)(.*?)(</section>)',
            re.S,
        )
        m = sec_re.search(html)
        if not m:
            raise SystemExit(f"missing HTML section for catalog id {pid!r}")
        body = m.group(2)
        body = re.sub(
            r'\n?<div class="nextpath" data-catalog-nav="1"[^>]*>.*?</div>\n?',
            "\n",
            body,
            count=1,
            flags=re.S,
        )
        prov = re.search(r'\n?(  <div class="provenance">)', body)
        if prov:
            insert_at = prov.start(1)
            body = body[:insert_at] + "\n  " + frag + "\n\n" + body[insert_at:]
        else:
            body = body.rstrip() + "\n\n  " + frag + "\n"
        html = html[: m.start()] + m.group(1) + body + m.group(3) + html[m.end() :]
        changed.append(pid)
    path.write_text(html, encoding="utf-8")
    return changed


def main() -> None:
    changed = bake()
    print(f"baked nextpath for {len(changed)} procedures: {', '.join(changed)}")


if __name__ == "__main__":
    main()
