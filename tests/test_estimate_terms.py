"""
Estimate standard terms: commercial / residential split (2026-10-19).

Regression for the residential boilerplate (1/2" board, Level 5, "design
provided by GVC", "verbal directions", payment on completion, six month
warranty) that printed on every estimate, including $4.62M and $340K
commercial GC bids.

Renders the real Jinja template to HTML (no WeasyPrint, no Drive, no Gmail).
Runs under pytest OR directly: ``python tests/test_estimate_terms.py``.
"""
from __future__ import annotations

import html
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from orchestrators import estimate_flow as ef  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

BANNED_IN_COMMERCIAL_DEFAULTS = (
    "1/2",
    "level 5",
    "design provided by gvc",
    "verbal direction",
    "payment due upon receipt",
    "six (6) months",
)


def _payload(project_type: str | None, *, line_detail: str = "5/8\" Type X board, finished for paint.",
             special_notes=None) -> dict:
    d = json.loads((ROOT / "example_estimate.json").read_text(encoding="utf-8"))
    d["job"]["project_type"] = project_type
    d["estimate"]["identifier"] = "EST-2026-1019-001"
    for li in d["estimate"]["line_items"]:
        li["detail"] = line_detail
    d["estimate"]["special_notes"] = special_notes or []
    return d


def _terms_section(rendered: str) -> str:
    """Plain text of the terms page only."""
    start = rendered.index('<div class="terms">')
    end = rendered.find("<!-- Additional Services", start)
    chunk = rendered[start:end if end != -1 else None]
    return html.unescape(re.sub(r"<[^>]+>", " ", chunk))


def _visible_text(rendered: str) -> str:
    """Rendered page text only: drops <style>, data: URIs (the base64 logo
    happens to contain "1/2") and tags."""
    s = re.sub(r"<style.*?</style>", " ", rendered, flags=re.S)
    s = re.sub(r"data:[^\"')\s]+", " ", s)
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


def _render(payload: dict, **kw) -> str:
    return ef.render_estimate_html(ef.enrich(payload, **kw))


def _all_default_text(terms: dict) -> str:
    return " ".join([terms.get("title", ""), terms.get("precedence", "")]
                    + list(terms.get("items") or []))


# -- Defaults themselves ----------------------------------------------------

def test_old_single_boilerplate_is_gone():
    assert not hasattr(ef, "STANDARD_BOILERPLATE")


@pytest.mark.parametrize("terms", [ef.COMMERCIAL_TERMS, ef.RESIDENTIAL_TERMS],
                         ids=["commercial", "residential"])
def test_no_default_hard_codes_board_design_or_verbal_direction(terms):
    text = _all_default_text(terms).lower()
    for banned in ("1/2", "design provided by gvc", "verbal direction"):
        assert banned not in text, banned
    # No default PROMISES Level 5. Residential may say Level 5 is priced only
    # when listed; it must not say the work "will achieve" it.
    assert "level 5 finish" not in text


def test_commercial_defaults_have_no_residential_language():
    text = _all_default_text(ef.COMMERCIAL_TERMS).lower()
    for banned in BANNED_IN_COMMERCIAL_DEFAULTS:
        assert banned not in text, banned


# -- Rendered commercial estimate ------------------------------------------

def test_commercial_estimate_terms_page_is_clean():
    terms = _terms_section(_render(_payload("commercial"))).lower()
    assert "standard terms (commercial)" in terms
    for banned in BANNED_IN_COMMERCIAL_DEFAULTS:
        assert banned not in terms, banned


def _estimate_body(rendered: str) -> str:
    """Everything the estimate says about THIS job: cover, pricing, notes,
    scope details and terms. Stops at the Additional Services page, which is
    the generic offerings menu from the scope catalog (it lists a 1/2 in board
    offering by design; see the PR note)."""
    cut = rendered.find("<!-- Additional Services")
    return _visible_text(rendered[:cut] if cut != -1 else rendered)


def test_commercial_whole_document_has_no_banned_text_when_scope_does_not():
    doc = _estimate_body(_render(_payload("commercial"))).lower()
    for banned in ("1/2", "level 5", "design provided by gvc", "verbal direction"):
        assert banned not in doc, banned


def test_level_5_appears_only_when_job_scope_says_so():
    payload = _payload("commercial", line_detail="All surfaces finished to Level 5.")
    rendered = _render(payload)
    assert "Level 5" in _visible_text(rendered)          # from the job's scope
    assert "level 5" not in _terms_section(rendered).lower()  # never from terms


def test_monday_style_label_selects_commercial():
    assert ef.select_terms("Commercial")["project_type"] == "commercial"
    assert ef.select_terms("  COMMERCIAL ")["project_type"] == "commercial"


# -- Residential selection --------------------------------------------------

def test_residential_selection_works():
    rendered = _render(_payload("residential"))
    terms = _terms_section(rendered)
    assert "Standard Terms (Residential)" in terms
    assert "finish is Level 4" in terms
    assert "Progress billing per the GC subcontract" not in terms
    assert ef.select_terms("Residential")["project_type"] == "residential"


def test_commercial_and_residential_render_different_terms():
    c = _terms_section(_render(_payload("commercial")))
    r = _terms_section(_render(_payload("residential")))
    assert c != r
    assert "Retainage" in c and "Retainage" not in r


# -- Scope governs, always --------------------------------------------------

@pytest.mark.parametrize("ptype", ["commercial", "residential", ""])
@pytest.mark.parametrize("notes", [[], ["Use 5/8 in board at corridors."]],
                         ids=["no-notes", "with-notes"])
def test_scope_governs_line_always_prints(ptype, notes):
    terms = _terms_section(_render(_payload(ptype, special_notes=notes))).lower()
    assert "scope of work section" in terms
    assert "scope section controls" in terms


# -- Blank / unknown project_type is caught ---------------------------------

@pytest.mark.parametrize("bad", [None, "", "   ", "Repair", "Specialty Project"])
def test_blank_or_unknown_project_type_fails_validation(bad):
    with pytest.raises(ValueError, match="project_type"):
        ef.validate(_payload(bad))


@pytest.mark.parametrize("bad", [None, "", "Repair"])
def test_select_terms_strict_raises(bad):
    with pytest.raises(ValueError, match="project_type"):
        ef.select_terms(bad)


def test_strict_enrich_raises_on_blank():
    with pytest.raises(ValueError, match="project_type"):
        ef.enrich(_payload(""), strict_terms=True)


def test_preview_with_blank_type_shows_needs_review_not_a_guess():
    terms = _terms_section(_render(_payload("")))
    assert "NEEDS REVIEW" in terms
    assert "Standard Terms (Commercial)" not in terms
    assert "Standard Terms (Residential)" not in terms


def test_finalize_with_blank_type_stops_before_any_side_effect(tmp_path):
    with pytest.raises(ValueError, match="project_type"):
        ef.process_estimate(_payload(""), tmp_path, mode="finalize")
    assert not list(tmp_path.iterdir())


# -- Placeholders never reach a customer ------------------------------------

def test_commercial_has_no_unfilled_placeholders_and_validates():
    assert ef._unfilled_placeholders(ef.COMMERCIAL_TERMS) == []
    ef.validate(_payload("commercial"))


def test_unfilled_residential_placeholders_block_finalize():
    terms = deepcopy(ef.RESIDENTIAL_TERMS)
    if not ef._unfilled_placeholders(terms):
        pytest.skip("residential fill-ins have been completed")
    with pytest.raises(ValueError, match="placeholders"):
        ef.validate(_payload("residential"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
