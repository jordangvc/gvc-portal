"""Field Guide platform — schema, catalog, search, render.

Run: .venv/bin/pytest tests/test_fieldguide_platform.py -q
  or: python tests/test_fieldguide_platform.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_normalize_authoring_aliases() -> None:
    from subsystems.fieldguide.schema import normalize_procedure

    proc = normalize_procedure({
        "id": "hang",
        "title": "Hanging",
        "trade_id": "drywall",
        "category_id": "seq",
        "plain_words": "Ceiling first.",
        "short_answer": "Hang board right.",
        "role_tags": ["crew"],
        "search_tags": ["Hang"],
        "synonyms": ["hang rock"],
        "when_to_use": ["After framing"],
        "tools": [{"name": "Screw gun", "optional": False}],
        "materials": [{"name": "Board", "optional": True}],
        "steps": [
            {"order": 1, "title": "Ceiling", "body": "Do ceiling first", "checklist": True}
        ],
        "warnings": [{"severity": "warn", "title": "Paper", "body": "Don't tear"}],
        "variations": [{
            "id": "v1", "title": "High", "when": "Tall", "changes": ["Use lift"]
        }],
        "quality_checks": [{"id": "q1", "prompt": "Edges land", "severity": "required"}],
        "common_mistakes": [{
            "title": "Float", "wrong": "No backing", "right": "Add backing", "why": "Cracks"
        }],
        "related_ids": ["scrape"],
        "next_steps": [{"id": "scrape", "label": "Next scrape", "why": "Job Check"}],
        "review_status": "approved",
        "owner": "field-ops",
        "last_reviewed": "2026-08-06",
        "provenance": {"kind": "gvc_practice", "note": "GVC practice"},
    })
    check("trade alias", proc["trade"] == "drywall")
    check("lede from plain_words", "Ceiling first" in proc["lede"])
    check("roles alias", proc["roles"] == ["crew"])
    check("tags lower", proc["tags"] == ["hang"])
    check("tools named", "Screw gun" in proc["tools"][0])
    check("optional material", "optional" in proc["materials"][0])
    check("step merges title+body", "Ceiling" in proc["steps"][0]["text"])
    check("warn kind", proc["warnings"][0]["kind"] == "warn")
    check("variation guidance", "Use lift" in proc["variations"][0]["guidance"])
    check("gov approved", proc["governance"]["status"] == "approved")
    check("related from ids", proc["related"][0]["procedure_id"] == "scrape")
    check("next step id", proc["next_steps"][0]["procedure_id"] == "scrape")


def test_validate_approved_requires_field_language() -> None:
    from subsystems.fieldguide.validate import validate_procedure

    raw = {
        "id": "x",
        "title": "X",
        "trade": "drywall",
        "summary": "sum",
        "steps": [{"text": "one"}],
        "governance": {"status": "approved"},
        "provenance": {"note": "n"},
        "when_to_use": "now",
        "quality_checks": ["ok"],
        "common_mistakes": ["bad"],
        # missing synonyms
    }
    try:
        validate_procedure(raw, require_approved_complete=True)
        check("should have raised", False)
    except ValueError as e:
        check("synonyms required", "synonyms" in str(e))


def test_repo_catalog_hang_scrape() -> None:
    from subsystems.fieldguide.catalog import (
        clear_cache, load_catalog, catalog_summary, audit_link_targets,
        get_procedure, resolve_procedure_id,
    )
    from subsystems.fieldguide.search import search_procedures, related_suggestions
    from subsystems.fieldguide.render import render_procedure_article

    clear_cache()
    cat = load_catalog(strict=True)
    spine = {"framing", "preboard-walk", "hang", "scrape", "finish",
             "level5-skim", "cleanout"}
    check("job-check spine migrated", spine <= set(cat["by_id"]))
    ops = {"jobstart-firstday", "job-conditions", "window-returns", "scaffold-lifts"}
    check("ops logistics migrated", ops <= set(cat["by_id"]))
    check("approved cards", len(cat["cards"]) >= 12)
    check("act migrated", "act" in cat["by_id"])
    check("act approved card", any(c["id"] == "act" for c in cat["cards"]))
    check("jobcheck hang", cat["jobcheck_anchors"].get("Hanging Status") == "hang")
    check("jobcheck scrape", cat["jobcheck_anchors"].get("Scrapping Status") == "scrape")
    check("jobcheck taped→finish", cat["jobcheck_anchors"].get("Taped Status") == "finish")
    check("jobcheck framing", cat["jobcheck_anchors"].get("Framing Status") == "framing")
    check("jobcheck skim", cat["jobcheck_anchors"].get("Text/Skim") == "level5-skim")

    hits = search_procedures("scrapping")
    check("scrapping → scrape first", hits and hits[0]["id"] == "scrape")
    hits2 = search_procedures("hang rock")
    check("hang rock → hang", hits2 and hits2[0]["id"] == "hang")
    hits3 = search_procedures("knockdown")
    check("knockdown → scrape", hits3 and hits3[0]["id"] == "scrape")
    hits4 = search_procedures("taped")
    check("taped alias → finish", hits4 and hits4[0]["id"] == "finish")
    hits5 = search_procedures("frame")
    check("frame alias → framing", hits5 and hits5[0]["id"] == "framing")

    check("resolve frame", resolve_procedure_id("frame") == "framing")
    check("resolve drop-ceiling", resolve_procedure_id("drop-ceiling") == "act")
    check("resolve act", resolve_procedure_id("act") == "act")
    check("get_procedure taped", get_procedure("taped")["id"] == "finish")

    rel = related_suggestions("hang")
    check("hang has next scrape", any(r["id"] == "scrape" for r in rel))
    rel_f = related_suggestions("scrape")
    check("scrape next reaches finish", any(r["id"] == "finish" for r in rel_f))

    html = render_procedure_article(cat["by_id"]["hang"])
    check("article doc class", 'class="doc"' in html and 'id="hang"' in html)
    check("plainwords", "plainwords" in html)
    check("steps checklist", 'class="steps"' in html and 'class="txt"' in html)
    check("nextpath data-go", "nextpath" in html and 'data-go="scrape"' in html)
    check("no dead end — next scrape", "#scrape" in html or 'data-go="scrape"' in html)

    audit = audit_link_targets()
    check("next_steps audit clean", audit["ok"] is True)
    check("audit counts spine + ops + act", audit["procedure_count"] >= 12)

    summary = catalog_summary()
    check("summary ok", summary["ok"] is True)
    check("platform templates listed", "task_guide" in summary["platform"]["templates"])


def test_manifest_categories_alias() -> None:
    from subsystems.fieldguide.validate import validate_manifest

    man = validate_manifest({
        "version": "1",
        "categories": [{
            "id": "g1",
            "title": "Seq",
            "summary": "blurb",
            "procedure_ids": ["a"],
        }],
        "trades": [],
    }, {"a", "b"})
    check("groups from categories", man["groups"][0]["id"] == "g1")
    check("ungrouped b", man["ungrouped"] == ["b"])


def test_stale_draft_excluded_from_default_search() -> None:
    from subsystems.fieldguide.schema import normalize_procedure
    from subsystems.fieldguide.search import score_procedure

    draft = normalize_procedure({
        "id": "drafty",
        "title": "Drafty",
        "trade": "drywall",
        "summary": "x",
        "steps": [{"text": "s"}],
        "review_status": "draft",
        "synonyms": ["zzzunique"],
    })
    # score still works; catalog search filters drafts
    check("score positive", score_procedure(draft, "zzzunique") > 0)


def test_jobcheck_anchors_match_catalog_spine() -> None:
    """boards.py Field Manual deep-links must resolve in the catalog."""
    from shared.boards import (
        JOBCHECK_FIELDGUIDE_ANCHORS,
        JOBCHECK_OPS_FIELDGUIDE_ANCHORS,
    )
    from subsystems.fieldguide.catalog import (
        clear_cache, load_catalog, resolve_procedure_id,
    )

    clear_cache()
    known = set(load_catalog()["by_id"])
    missing = []
    for _col, anchor in {
        **JOBCHECK_FIELDGUIDE_ANCHORS,
        **JOBCHECK_OPS_FIELDGUIDE_ANCHORS,
    }.items():
        pid = resolve_procedure_id((anchor or "").lstrip("#"))
        if pid in known:
            continue
        missing.append(f"{_col}→{anchor}")
    check("no unknown Job Check→Field Manual anchors", missing == [])


def test_ops_jobcheck_anchors_in_manifest() -> None:
    """Ops Job Check column labels map to migrated catalog procedures."""
    from subsystems.fieldguide.catalog import clear_cache, load_catalog

    clear_cache()
    anchors = load_catalog()["jobcheck_anchors"]
    check("Scaffolding anchor", anchors.get("Scaffolding") == "scaffold-lifts")
    check("Heater/Cans anchor", anchors.get("Heater/Cans") == "job-conditions")
    check("Lock Box anchor", anchors.get("Lock Box") == "jobstart-firstday")
    check("Window type anchor", anchors.get("Window type") == "window-returns")
    check("Open questions anchor", anchors.get("Open questions") == "jobstart-firstday")


def test_shell_catalog_nav_wiring() -> None:
    """Job Check → Field Guide must not dead-end: shell injects catalog nextpath."""
    import re

    shell = (ROOT / "web" / "fieldguide.html").read_text(encoding="utf-8")
    service = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    check("shell enhanceCatalogDoc", "function enhanceCatalogDoc" in shell)
    check("shell fetches procedure API", "/ui/api/fieldguide/procedure/" in shell)
    check("shell builds nextpath", "buildCatalogNextpathEl" in shell
          and "data-catalog-nav" in shell)
    check("shell sync nextpath from cards", "catalogNextById" in shell
          and "mountCatalogNextpath" in shell)
    check("shell hydrates synonyms or search",
          "hydrateCatalogSearch" in shell or "scheduleCatalogSearch" in shell)
    check("procedure API returns render html",
          "fieldguide_render.render_procedure_article" in service)
    check("html key not hard-None",
          '"html": None,  # shell still owns' not in service)

    from subsystems.fieldguide.catalog import clear_cache, get_procedure, load_catalog
    from subsystems.fieldguide.coach import coach_payload
    from subsystems.fieldguide.render import render_nextpath_html, render_procedure_article

    clear_cache()
    hang = get_procedure("hang")
    check("hang has next scrape",
          any(l.get("procedure_id") == "scrape" for l in (hang.get("next_steps") or [])))
    html = render_procedure_article(hang)
    check("rendered html has nextpath", 'class="nextpath"' in html)
    check("rendered scrape button", 'data-go="scrape"' in html)
    frag = render_nextpath_html(hang, include_related=False, catalog_nav=True)
    check("nextpath helper data-go", 'data-go="scrape"' in frag)

    # Offline-first: every catalog procedure's shell section already has nextpath.
    missing_bake = []
    for pid, proc in load_catalog()["by_id"].items():
        m = re.search(
            rf'<section class="doc" id="{re.escape(pid)}">(.*?)</section>',
            shell,
            re.S,
        )
        if not m or 'data-catalog-nav="1"' not in m.group(1):
            missing_bake.append(pid)
            continue
        for link in proc.get("next_steps") or []:
            tid = link.get("procedure_id") or ""
            if tid and f'data-go="{tid}"' not in m.group(1):
                missing_bake.append(f"{pid}→{tid}")
    check("baked nextpath for all catalog docs", missing_bake == [])

    coach = coach_payload("hang")
    check("coach catalog_backed", coach.get("catalog_backed") is True)
    related_ids = {r.get("id") for r in coach.get("related") or []}
    check("coach related includes scrape", "scrape" in related_ids)

    act_coach = coach_payload("act")
    check("act catalog_backed", act_coach.get("catalog_backed") is True)

def main() -> None:
    print("test_fieldguide_platform")
    test_normalize_authoring_aliases()
    test_validate_approved_requires_field_language()
    test_repo_catalog_hang_scrape()
    test_manifest_categories_alias()
    test_stale_draft_excluded_from_default_search()
    test_jobcheck_anchors_match_catalog_spine()
    test_ops_jobcheck_anchors_in_manifest()
    test_shell_catalog_nav_wiring()
    print("ALL OK")


if __name__ == "__main__":
    main()
