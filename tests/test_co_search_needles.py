"""CO Find-the-Project search probes EST-/PRO-/INV- + bare core."""
from __future__ import annotations

import adapters.monday.co as monday_co
from shared.doc_number import search_needles


def test_search_projects_probes_spine_needles_on_project_number(monkeypatch):
    probes: list[tuple[str, str]] = []

    class FakeLocal:
        def _query(self, query, variables=None):  # noqa: ARG002
            variables = variables or {}
            probes.append((
                str(variables.get("columnId") or ""),
                str(variables.get("value") or ""),
            ))
            return {"boards": [{"items_page": {"items": []}}]}

    monkeypatch.setattr(monday_co, "MondayClient", lambda **_kw: FakeLocal())

    monday_co._search_projects_uncached(object(), "EST-2026-0804-007", limit=10)
    needles = search_needles("EST-2026-0804-007")
    pn_vals = [v for c, v in probes if c == monday_co.P_COL_PROJECT_NUMBER]
    name_vals = [v for c, v in probes if c == "name"]
    assert name_vals == ["EST-2026-0804-007"]
    for needle in needles:
        assert needle in pn_vals
    assert "2026-0804-007" in pn_vals


def test_search_projects_short_q_skipped_by_wrapper():
    assert monday_co.search_projects(object(), "x") == []
