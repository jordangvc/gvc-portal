"""Pure tests for Change Order Projects-board prefill enrichment.

Covers enrich_project_context: builder → client_name and supervisor →
contact_name fallbacks when Customer-board fields are empty. No Monday I/O.
"""
from __future__ import annotations

import adapters.monday.co as monday_co


def test_builder_and_supervisor_column_ids_match_jobstart():
    assert monday_co.P_COL_BUILDER == "text"
    assert monday_co.P_COL_SUPERVISOR == "text5"
    assert monday_co.P_COL_PROJECT_TYPE == "status"
    assert monday_co.P_COL_SCOPE == "details"


def test_enrich_builder_fills_empty_client_name():
    out = monday_co.enrich_project_context({
        "client_name": None,
        "builder": "Jent Construction",
        "contact_name": "Dave",
        "supervisor": "Rob",
    })
    assert out["client_name"] == "Jent Construction"
    assert out["builder"] == "Jent Construction"
    assert out["contact_name"] == "Dave"  # already set — not overwritten


def test_enrich_supervisor_fills_empty_contact_name():
    out = monday_co.enrich_project_context({
        "client_name": "Acme Builders",
        "builder": "Someone Else",
        "contact_name": None,
        "supervisor": "Mark Faulkner",
    })
    assert out["client_name"] == "Acme Builders"  # customer wins
    assert out["contact_name"] == "Mark Faulkner"


def test_enrich_never_overwrites_populated_fields():
    out = monday_co.enrich_project_context({
        "client_name": "Linked Customer LLC",
        "builder": "Board Builder",
        "contact_name": "Customer Contact",
        "supervisor": "Site Super",
    })
    assert out["client_name"] == "Linked Customer LLC"
    assert out["contact_name"] == "Customer Contact"
    assert out["builder"] == "Board Builder"
    assert out["supervisor"] == "Site Super"


def test_enrich_whitespace_treated_as_empty():
    out = monday_co.enrich_project_context({
        "client_name": "   ",
        "builder": "  Greg Gavin Homes  ",
        "contact_name": "",
        "supervisor": "  Rob  ",
    })
    assert out["client_name"] == "Greg Gavin Homes"
    assert out["contact_name"] == "Rob"
    assert out["builder"] == "Greg Gavin Homes"
    assert out["supervisor"] == "Rob"


def test_enrich_no_fallback_when_builder_supervisor_missing():
    out = monday_co.enrich_project_context({
        "client_name": None,
        "contact_name": None,
        "builder": None,
        "supervisor": "",
        "project_type": "Residential",
        "scope_summary": "Hang and finish Level 4",
    })
    assert out["client_name"] is None
    assert out["contact_name"] is None
    assert out["project_type"] == "Residential"
    assert out["scope_summary"] == "Hang and finish Level 4"


def test_enrich_preserves_extra_keys():
    out = monday_co.enrich_project_context({
        "monday_item_id": 42,
        "job_name": "123 Main | Acme",
        "client_name": None,
        "builder": "Acme",
        "existing_cos": [],
    })
    assert out["monday_item_id"] == 42
    assert out["job_name"] == "123 Main | Acme"
    assert out["client_name"] == "Acme"
    assert out["existing_cos"] == []


def test_enrich_empty_or_none_ctx():
    assert monday_co.enrich_project_context({})["client_name"] is None
    assert monday_co.enrich_project_context(None)["client_name"] is None


if __name__ == "__main__":
    tests = [
        test_builder_and_supervisor_column_ids_match_jobstart,
        test_enrich_builder_fills_empty_client_name,
        test_enrich_supervisor_fills_empty_contact_name,
        test_enrich_never_overwrites_populated_fields,
        test_enrich_whitespace_treated_as_empty,
        test_enrich_no_fallback_when_builder_supervisor_missing,
        test_enrich_preserves_extra_keys,
        test_enrich_empty_or_none_ctx,
    ]
    for fn in tests:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(tests)} passed")
