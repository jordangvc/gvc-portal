"""Training page wiring — baseline feature, hub tile, route + HTML."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_training_is_baseline_feature():
    from shared import access

    assert "training" in access.FEATURES
    assert "training" in access.BASELINE
    assert "training" not in {
        feat for role in access.ROLE_PRESETS for feat in role["features"]
        if feat != access.WILDCARD
    }


def test_training_hub_nav_entry():
    from shared import hub_nav

    assert hub_nav.HOME_TOOL_LABELS["training"] == "Training"
    assert hub_nav.home_tool_href("training") == "/ui/training"
    company = next(g for g in hub_nav.TOOL_GROUPS if g[0] == "Company")
    tools = {key: href for _name, key, href, _ext in company[1]}
    assert tools["training"] == "/ui/training"


def test_training_route_and_html_are_wired():
    src = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    assert '@app.get("/ui/training"' in src
    assert 'require_feature(request, "training")' in src
    html = (ROOT / "web" / "training.html").read_text(encoding="utf-8")
    assert "{{EMAIL_JSON}}" in html
    assert "Builder | Job Title" in html
    assert "GM first week (Donnie)" in html
    assert "#operations" in html
