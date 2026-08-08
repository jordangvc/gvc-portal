"""
Billing Hub tests — pure queue shaping + search fallback with fakes.
=========================================================================
No Monday / network. Runs under pytest OR directly:
  python tests/test_billing_hub.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.monday import billing as mb  # noqa: E402
from orchestrators import billing_flow as bf  # noqa: E402
from subsystems.invoice import billing_queue as bq  # noqa: E402


# ---------------------------------------------------------------- deep links

def test_invoice_href_carries_all_known_keys():
    # Carry Project # AND Monday id so the invoice page can prefill even if
    # one lookup path fails. Free-text q is only added when Project # missing.
    assert mb.invoice_href(project_number="C-005", monday_item_id=99) == (
        "/ui/invoice?project_number=C-005&monday_item_id=99"
    )
    assert mb.invoice_href(monday_item_id=12345) == (
        "/ui/invoice?monday_item_id=12345"
    )
    assert mb.invoice_href(monday_item_id=12345, q="Gertrude") == (
        "/ui/invoice?monday_item_id=12345&q=Gertrude"
    )
    assert mb.invoice_href(project_number="C-005", q="noise") == (
        "/ui/invoice?project_number=C-005"
    )
    assert mb.invoice_href() == "/ui/invoice"


def test_estimate_and_jobstart_hrefs():
    assert mb.estimate_href(estimate_number="2026-0804-001") == (
        "/ui/estimate?q=2026-0804-001"
    )
    assert "Bryant" in mb.estimate_href(q="Bryant | Jent")
    assert mb.jobstart_href(bid_id=555) == "/ui/jobstart?bid=555"
    assert mb.jobstart_href() == "/ui/jobstart"


# ----------------------------------------------------------- queue shaping

def test_shape_ready_to_invoice_with_project_number():
    item = bq.shape_ready_to_invoice({
        "item_id": 10,
        "name": "9761 Gertrude | Zicka",
        "url": "https://monday.example/10",
        "project_item_id": 20,
        "project_name": "9761 Gertrude | Zicka",
        "project_number": "C-100",
        "builder": "Zicka",
        "supervisor": "Mark",
        "location": "9761 Gertrude Lane, Cincinnati OH",
        "ready_date": "2026-08-01",
        "billable": "Yes",
        "stage": "Complete",
        "project_status": "In Progress",
    })
    assert item["kind"] == "ready_to_invoice"
    assert item["project_number"] == "C-100"
    assert item["invoice_href"] == (
        "/ui/invoice?project_number=C-100&monday_item_id=20"
    )
    assert item["primary_href"] == item["invoice_href"]
    assert item["primary_label"] == "Open invoice"
    assert item["builder"] == "Zicka"
    assert any("Billable" in s for s in item["status_labels"])
    assert "Project # known" in item["note"]


def test_shape_ready_to_invoice_falls_back_to_monday_item_id():
    item = bq.shape_ready_to_invoice({
        "item_id": 10,
        "name": "No number yet",
        "url": "https://monday.example/10",
        "project_item_id": 20,
        "project_number": None,
    })
    assert item["invoice_href"] == (
        "/ui/invoice?monday_item_id=20"
    )
    assert "linked Projects item" in item["note"]


def test_shape_ready_to_invoice_ops_only_uses_search_q():
    """Ops pulse must NOT become monday_item_id — invoice lookup is Projects-only."""
    item = bq.shape_ready_to_invoice({
        "item_id": 10,
        "name": "Ops only job",
        "url": "https://monday.example/10",
        "project_item_id": None,
        "project_number": None,
    })
    assert item["invoice_href"] == "/ui/invoice?q=Ops%20only%20job"
    assert "monday_item_id" not in item["invoice_href"]
    assert "No Projects link" in item["note"]


def test_shape_accepted_bid_needs_handoff():
    item = bq.shape_accepted_bid({
        "item_id": 77,
        "name": "Bryant | Jent",
        "url": "https://monday.example/77",
        "stage": "Accepted",
        "estimate_number": "2026-0724-002",
        "estimate_total": "12000",
        "location": "Somewhere",
        "has_project": False,
        "has_ops": False,
        "handed_off": False,
        "accepted_date": "2026-07-29",
    })
    assert item["needs_handoff"] is True
    assert item["primary_href"] == "/ui/jobstart?bid=77"
    assert item["primary_label"] == "Open Job Start"
    assert item["estimate_href"] == "/ui/estimate?q=2026-0724-002"
    assert "handoff" in item["note"].lower()


def test_shape_accepted_bid_handed_off_with_project_number():
    item = bq.shape_accepted_bid({
        "item_id": 88,
        "name": "Done deal",
        "stage": "Accepted",
        "estimate_number": "2026-0801-001",
        "project_number": "MV-010",
        "has_project": True,
        "has_ops": True,
        "handed_off": True,
    })
    assert item["needs_handoff"] is False
    assert item["primary_href"] == "/ui/invoice?project_number=MV-010"
    assert item["primary_label"] == "Open invoice"


def test_shape_project_billing():
    item = bq.shape_project_billing({
        "item_id": 33,
        "name": "150 W Dorothy | Terraces",
        "url": "https://monday.example/33",
        "project_number": "C-005",
        "builder": "Danis",
        "supervisor": "Rob",
        "location": "150 W Dorothy Lane",
        "invoice_status": "Ready",
        "deal_stage": "In Progress",
        "group_title": "Active",
    })
    assert item["kind"] == "project_billing"
    assert item["invoice_href"] == (
        "/ui/invoice?project_number=C-005&monday_item_id=33"
    )
    assert "Ready" in item["status_labels"]


def test_shape_search_hits():
    proj = bq.shape_search_project({
        "item_id": 1,
        "name": "Job A",
        "project_number": "C-001",
        "builder": "Zicka",
        "group": "Active",
        "url": "https://monday.example/1",
    })
    assert "project_number=C-001" in proj["primary_href"]
    assert "monday_item_id=1" in proj["primary_href"]
    bid = bq.shape_search_bid({
        "item_id": 2,
        "name": "Bid B",
        "estimate_number": "2026-0804-003",
        "stage": "Accepted",
        "url": "https://monday.example/2",
    })
    assert bid["primary_label"] == "Open Job Start"
    assert bid["jobstart_href"] == "/ui/jobstart?bid=2"


def test_billing_queue_invoice_href_forwards_q():
    """Regression: wrapper used to drop q= and TypeError the Ready queue."""
    href = bq.invoice_href(monday_item_id=20, q="No number yet")
    assert href == "/ui/invoice?monday_item_id=20&q=No%20number%20yet"
    # With Project # present, free-text q stays off the URL.
    assert bq.invoice_href(project_number="C-1", q="noise") == (
        "/ui/invoice?project_number=C-1"
    )


def test_linked_ids_skips_garbage_pulse_ids():
    cv = {
        "linked_item_ids": ["abc", "200"],
        "value": '{"linkedPulseIds":[{"linkedPulseId":"not-int"},'
                 '{"linkedPulseId":201}]}',
    }
    assert mb._linked_ids(cv) == [200]  # typed field wins first
    cv2 = {
        "linked_item_ids": [],
        "value": '{"linkedPulseIds":[{"linkedPulseId":"x"},'
                 '{"linkedPulseId":201}]}',
    }
    assert mb._linked_ids(cv2) == [201]


# ------------------------------------------------------ adapter normalize

def test_normalize_ops_ready_extracts_linked_project():
    raw = {
        "id": "100",
        "name": "Ops task",
        "group": {"id": "group_mm3zq4q2", "title": "Ready to Invoice"},
        "column_values": [
            {
                "id": mb.OPS_COL_PROJECT_LINK,
                "display_value": "Linked Project",
                "linked_item_ids": ["200"],
                "value": None,
            },
            {"id": mb.OPS_COL_LOCATION, "text": "123 Main", "display_value": None},
            {"id": mb.OPS_COL_READY_DATE, "text": "2026-08-02"},
            {"id": mb.OPS_COL_BILLABLE, "text": "Yes"},
            {"id": mb.OPS_COL_STAGE, "text": "Done"},
        ],
    }
    row = mb._normalize_ops_ready(raw)
    assert row is not None
    assert row["item_id"] == 100
    assert row["project_item_id"] == 200
    assert row["project_name"] == "Linked Project"
    assert row["location"] == "123 Main"
    assert row["billable"] == "Yes"


def test_normalize_project_billing_skips_not_started_and_co_rows():
    base_cols = [
        {"id": mb.P_COL_PROJECT_NUMBER, "text": "C-009"},
        {"id": mb.P_COL_INVOICE_STATUS, "text": "Not Started"},
        {"id": mb.P_COL_BUILDER, "text": "X"},
    ]
    assert mb._normalize_project_billing({
        "id": "1", "name": "Normal", "group": {}, "column_values": base_cols,
    }) is None

    ready_cols = [
        {"id": mb.P_COL_PROJECT_NUMBER, "text": "C-009"},
        {"id": mb.P_COL_INVOICE_STATUS, "text": "Partially Billed"},
        {"id": mb.P_COL_BUILDER, "text": "X"},
        {"id": mb.P_COL_DEAL_STAGE, "text": "In Progress"},
    ]
    row = mb._normalize_project_billing({
        "id": "2", "name": "Billable job", "group": {"title": "Active"},
        "column_values": ready_cols,
    })
    assert row is not None
    assert row["invoice_status"] == "Partially Billed"

    assert mb._normalize_project_billing({
        "id": "3", "name": "CO.1 - parent", "group": {},
        "column_values": ready_cols,
    }) is None


def test_reshape_accepted_bid_sort_key_fields():
    row = mb._reshape_accepted_bid({
        "item_id": 9,
        "name": "Won",
        "url": "https://x/9",
        "stage": "Accepted",
        "stage_state": "accepted",
        "estimate_number": "E1",
        "has_project": True,
        "has_ops": False,
        "group_drift": True,
    })
    assert row["handed_off"] is False
    assert row["group_drift"] is True
    assert row["estimate_number"] == "E1"


# ---------------------------------------------------- search fallback flow

class _FakeMC:
    """Minimal Monday client stub — search path never calls _query here."""


def test_search_billing_short_query():
    out = bf.search_billing(_FakeMC(), "a")
    assert out["ok"] is True
    assert out["projects"] == []
    assert out["bids"] == []
    assert any("2 characters" in n for n in out["notes"])


def test_search_billing_fallback_to_co_and_estimate():
    """When rich search is absent, use co.search_projects + estimate.search_bids."""
    calls = {"projects": 0, "bids": 0}

    def fake_projects(mc, q, *, limit=15):
        calls["projects"] += 1
        return [{
            "item_id": 1,
            "name": "9761 Gertrude | Zicka",
            "group": "Active",
            "project_number": "C-100",
            "url": "https://monday.example/1",
        }]

    def fake_bids(mc, q, *, limit=15):
        calls["bids"] += 1
        return [{
            "item_id": 2,
            "name": "Bryant | Jent",
            "estimate_number": "2026-0724-002",
            "stage": "Accepted",
            "url": "https://monday.example/2",
        }]

    # Force fallback: pretend rich search module is missing.
    orig_search = bf.monday_search
    orig_co = bf.monday_co.search_projects
    orig_est = bf.monday_estimate.search_bids
    bf.monday_search = None
    bf.monday_co.search_projects = fake_projects
    bf.monday_estimate.search_bids = fake_bids
    try:
        out = bf.search_billing(_FakeMC(), "Gertrude")
        assert out["ok"] is True
        assert out["backend"] == "fallback"
        assert calls["projects"] == 1 and calls["bids"] == 1
        assert out["projects"][0]["project_number"] == "C-100"
        assert "project_number=C-100" in out["projects"][0]["invoice_href"]
        assert out["bids"][0]["estimate_number"] == "2026-0724-002"
        assert any("fallback" in n.lower() for n in out["notes"])
    finally:
        bf.monday_search = orig_search
        bf.monday_co.search_projects = orig_co
        bf.monday_estimate.search_bids = orig_est


def test_search_billing_uses_rich_when_available():
    rich_calls = {"p": 0, "b": 0}

    class _Rich:
        @staticmethod
        def search_projects_rich(mc, q, *, limit=15):
            rich_calls["p"] += 1
            return [{
                "item_id": 5,
                "name": "Rich Project",
                "project_number": "MV-050",
                "builder": "Builder Co",
                "supervisor": "Sue",
                "location": "Cincinnati OH",
                "url": "https://monday.example/5",
            }]

        @staticmethod
        def search_bids_rich(mc, q, *, limit=15):
            rich_calls["b"] += 1
            return [{
                "item_id": 6,
                "name": "Rich Bid",
                "estimate_number": "2026-0804-009",
                "stage": "Sent to Client",
                "url": "https://monday.example/6",
            }]

    fallback_hit = {"n": 0}

    def boom(*_a, **_k):
        fallback_hit["n"] += 1
        raise AssertionError("fallback should not run when rich succeeds")

    orig_search = bf.monday_search
    orig_co = bf.monday_co.search_projects
    orig_est = bf.monday_estimate.search_bids
    bf.monday_search = _Rich()
    bf.monday_co.search_projects = boom
    bf.monday_estimate.search_bids = boom
    try:
        out = bf.search_billing(_FakeMC(), "Cincinnati")
        assert out["backend"] == "rich"
        assert rich_calls == {"p": 1, "b": 1}
        assert fallback_hit["n"] == 0
        assert out["projects"][0]["builder"] == "Builder Co"
        assert out["bids"][0]["primary_label"] == "Open estimate"
    finally:
        bf.monday_search = orig_search
        bf.monday_co.search_projects = orig_co
        bf.monday_estimate.search_bids = orig_est


def test_search_billing_rich_failure_falls_back():
    class _BrokenRich:
        @staticmethod
        def search_projects_rich(mc, q, *, limit=15):
            raise RuntimeError("monday down")

        @staticmethod
        def search_bids_rich(mc, q, *, limit=15):
            raise RuntimeError("monday down")

    def fake_projects(mc, q, *, limit=15):
        return [{"item_id": 1, "name": "Fallback", "project_number": "C-1",
                 "group": "", "url": "u"}]

    def fake_bids(mc, q, *, limit=15):
        return []

    orig_search = bf.monday_search
    orig_co = bf.monday_co.search_projects
    orig_est = bf.monday_estimate.search_bids
    bf.monday_search = _BrokenRich()
    bf.monday_co.search_projects = fake_projects
    bf.monday_estimate.search_bids = fake_bids
    try:
        out = bf.search_billing(_FakeMC(), "Fallback")
        assert out["backend"] == "fallback"
        assert out["projects"][0]["name"] == "Fallback"
        assert any("Rich project search failed" in n for n in out["notes"])
    finally:
        bf.monday_search = orig_search
        bf.monday_co.search_projects = orig_co
        bf.monday_estimate.search_bids = orig_est


def test_billing_hub_payload_shapes_queues():
    ready = [{
        "item_id": 1, "name": "Ready job", "url": "u1",
        "project_item_id": 11, "project_number": "C-1",
        "builder": "B", "location": "L",
    }]
    bids = [{
        "item_id": 2, "name": "Accepted", "url": "u2",
        "stage": "Accepted", "estimate_number": "E-1",
        "has_project": False, "has_ops": False, "handed_off": False,
    }]
    projects = [{
        "item_id": 3, "name": "Proj", "url": "u3",
        "project_number": "C-3", "invoice_status": "Ready",
        "builder": "B2",
    }]

    orig_ready = mb.fetch_ready_to_invoice
    orig_bids = mb.fetch_accepted_bids
    orig_proj = mb.fetch_projects_billing
    mb.fetch_ready_to_invoice = lambda mc: ready
    mb.fetch_accepted_bids = lambda mc: bids
    mb.fetch_projects_billing = lambda mc: projects
    # billing_flow imports monday_billing at module level — patch there too.
    bf.monday_billing.fetch_ready_to_invoice = lambda mc: ready
    bf.monday_billing.fetch_accepted_bids = lambda mc: bids
    bf.monday_billing.fetch_projects_billing = lambda mc: projects
    try:
        out = bf.billing_hub_payload(mc=_FakeMC())
        assert out["ok"] is True
        assert out["counts"]["ready_to_invoice"] == 1
        assert out["counts"]["needs_handoff"] == 1
        assert "project_number=C-1" in out["queues"]["ready_to_invoice"][0]["invoice_href"]
        assert out["queues"]["accepted_bids"][0]["needs_handoff"] is True
        assert out["queues"]["projects_billing"][0]["invoice_status"] == "Ready"
        assert "generated_at" in out
    finally:
        mb.fetch_ready_to_invoice = orig_ready
        mb.fetch_accepted_bids = orig_bids
        mb.fetch_projects_billing = orig_proj
        bf.monday_billing.fetch_ready_to_invoice = orig_ready
        bf.monday_billing.fetch_accepted_bids = orig_bids
        bf.monday_billing.fetch_projects_billing = orig_proj


def test_billing_hub_payload_runs_queue_fetches_in_parallel():
    """Source contract: three queue legs submit via ThreadPoolExecutor."""
    src = (Path(__file__).resolve().parents[1] / "orchestrators" / "billing_flow.py").read_text()
    chunk = src.split("def billing_hub_payload")[1].split("def _rich_search_available")[0]
    assert "ThreadPoolExecutor(max_workers=3)" in chunk
    assert "as_completed" in chunk
    assert "loaders" in chunk
    assert '_run_one' in chunk


def test_warm_monday_includes_billing_keys():
    src = (Path(__file__).resolve().parents[1] / "app" / "service.py").read_text()
    chunk = src.split("def _warm_monday_caches")[1].split("def require_api_key")[0]
    assert "list:billing:ready_to_invoice" in chunk
    assert "list:billing:projects_billing:75" in chunk
    assert "list:billing:accepted_bids" in chunk
    # Accepted bids is DERIVED from jobstart L1 — not a parallel Bid walk.
    assert "_fetch_accepted_bids_uncached" in chunk
    assert '("list:billing:accepted_bids"' not in chunk.split("jobs =")[1].split(
        "def _refresh_one")[0]
    assert "ThreadPoolExecutor" in chunk
    assert "as_completed" in chunk


def test_search_billing_runs_rich_legs_in_parallel():
    src = (Path(__file__).resolve().parents[1] / "orchestrators" / "billing_flow.py").read_text()
    chunk = src.split("def search_billing")[1].split("backend = \"rich\"")[0]
    assert "ThreadPoolExecutor(max_workers=2)" in chunk
    assert "parallel = mc is None" in chunk
    assert "fut_p" in chunk and "fut_b" in chunk


def test_billing_hub_payload_survives_partial_failures():
    def boom(mc):
        raise RuntimeError("ops board unavailable")

    orig_ready = bf.monday_billing.fetch_ready_to_invoice
    orig_bids = bf.monday_billing.fetch_accepted_bids
    orig_proj = bf.monday_billing.fetch_projects_billing
    bf.monday_billing.fetch_ready_to_invoice = boom
    bf.monday_billing.fetch_accepted_bids = lambda mc: []
    bf.monday_billing.fetch_projects_billing = lambda mc: []
    try:
        out = bf.billing_hub_payload(mc=_FakeMC())
        assert out["ok"] is True
        assert out["queues"]["ready_to_invoice"] == []
        assert any("Ready to Invoice" in n for n in out["notes"])
    finally:
        bf.monday_billing.fetch_ready_to_invoice = orig_ready
        bf.monday_billing.fetch_accepted_bids = orig_bids
        bf.monday_billing.fetch_projects_billing = orig_proj


# ----------------------------------------------------------------- runner

def _run_all():
    tests = [v for k, v in globals().items()
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)


def test_invoice_page_boots_monday_item_id_deep_link():
    """Billing Hub emits ?monday_item_id= — Invoice must consume it on boot."""
    html = (Path(__file__).resolve().parents[1] / "web" / "invoice.html").read_text()
    assert "monday_item_id" in html
    assert "lookupProjectByItemId" in html
    # boot order: project_number first, then monday item id, then q
    boot_idx = html.index("function bootInvoiceFromUrl")
    # bootInvoiceFromUrl grew (project_number / q / monday paths) — don't truncate
    # before the monday_item_id branch.
    end = html.find("\nfunction ", boot_idx + 1)
    snippet = html[boot_idx: end if end != -1 else boot_idx + 4000]
    assert 'params.get("monday_item_id")' in snippet
    assert "lookupProjectByItemId(mondayItemId)" in snippet


def test_jobstart_page_boots_bid_deep_link():
    html = (Path(__file__).resolve().parents[1] / "web" / "jobstart.html").read_text()
    assert "function bootJobStartFromUrl" in html
    assert 'params.get("bid")' in html

