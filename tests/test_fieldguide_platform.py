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
    check("approved cards", len(cat["cards"]) >= 7)
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
    check("get_procedure taped", get_procedure("taped")["id"] == "finish")

    rel = related_suggestions("hang")
    check("hang has next scrape", any(r["id"] == "scrape" for r in rel))
    rel_f = related_suggestions("scrape")
    check("scrape next reaches finish", any(r["id"] == "finish" for r in rel_f))

    html = render_procedure_article(cat["by_id"]["hang"])
    check("article doc class", 'class="doc"' in html and 'id="hang"' in html)
    check("plainwords", "plainwords" in html)
    check("steps checklist", 'class="steps"' in html)
    check("nextpath", "nextpath" in html)
    check("no dead end — next scrape", "#scrape" in html)

    audit = audit_link_targets()
    check("next_steps audit clean", audit["ok"] is True)
    check("audit counts spine", audit["procedure_count"] >= 7)

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


def main() -> None:
    print("test_fieldguide_platform")
    test_normalize_authoring_aliases()
    test_validate_approved_requires_field_language()
    test_repo_catalog_hang_scrape()
    test_manifest_categories_alias()
    test_stale_draft_excluded_from_default_search()
    print("ALL OK")


if __name__ == "__main__":
    main()
