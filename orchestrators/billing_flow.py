"""
Billing Hub flow — queues + multi-way search for office invoicing.
=========================================================================
READ-ONLY orchestration for /ui/billing (docs/plans/2026-08-04-estimate-qa-
billing-hub.md). Monday stays SoT; this flow only shapes lists and search
hits into deep-linked hub cards.

  billing_hub_payload()  — Ready to Invoice + Accepted bids + Projects billing
  search_billing()       — rich Monday search when adapters.monday.search is
                           present; else falls back to co.search_projects +
                           estimate.search_bids (search.py owned by another
                           agent — import if present, document the fallback).

Routes (wired by integrator in app/service.py, not this module):
  GET  /ui/billing
  GET  /ui/api/billing/hub
  GET  /ui/api/billing/search?q=
  GET  /ui/api/billing/activity?limit=30
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from adapters.monday import billing as monday_billing
from adapters.monday.client import MondayClient
from adapters.monday import co as monday_co
from adapters.monday import estimate as monday_estimate
from subsystems.invoice import billing_queue as bq

# Optional rich search — another agent owns adapters/monday/search.py.
# When missing, we fall back to co.search_projects + estimate.search_bids.
try:
    from adapters.monday import search as monday_search
except ImportError:  # pragma: no cover — expected until search.py lands
    monday_search = None  # type: ignore[assignment]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _client(mc: Optional[Any] = None) -> Any:
    return mc if mc is not None else MondayClient()


def billing_hub_payload(mc=None) -> dict:
    """
    Full Billing Hub payload:

      { ok, queues: {
            ready_to_invoice: [...],
            accepted_bids: [...],
            projects_billing: [...],
        },
        generated_at, notes: [...],
        search_backend: "rich"|"fallback" }

    Each queue item includes name, ids, builder/supervisor/location when
    available, status_labels, monday_url, and portal deep links
    (invoice_href / estimate_href / jobstart_href / primary_href).

    The three Monday list fetches run in parallel (separate clients —
    requests.Session is not thread-safe). Wall-clock ≈ max(queue) on cold.
    """
    notes: list[str] = []
    queues = {
        "ready_to_invoice": [],
        "accepted_bids": [],
        "projects_billing": [],
    }
    ready_err = False

    # Injected `mc` (tests) is reused on every leg; live path gets one client
    # per leg so parallel HTTP doesn't share a Session.
    c_ready = _client(mc)
    c_bids = mc if mc is not None else MondayClient()
    c_proj = mc if mc is not None else MondayClient()

    def _load_ready():
        raw = monday_billing.fetch_ready_to_invoice(c_ready)
        return [bq.shape_ready_to_invoice(r) for r in (raw or [])]

    def _load_bids():
        raw = monday_billing.fetch_accepted_bids(c_bids)
        return [bq.shape_accepted_bid(r) for r in (raw or [])]

    def _load_projects():
        raw = monday_billing.fetch_projects_billing(c_proj)
        return [bq.shape_project_billing(r) for r in (raw or [])]

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_ready = pool.submit(_load_ready)
        fut_bids = pool.submit(_load_bids)
        fut_proj = pool.submit(_load_projects)

        try:
            queues["ready_to_invoice"] = fut_ready.result()
        except Exception as exc:  # noqa: BLE001 — hub still loads other queues
            ready_err = True
            notes.append(
                f"Couldn't load Ready to Invoice from Operations "
                f"({type(exc).__name__}). Check Monday token / board access."
            )
            print(f"[billing] ready_to_invoice failed: {exc}", file=sys.stderr)

        try:
            queues["accepted_bids"] = fut_bids.result()
        except Exception as exc:  # noqa: BLE001
            notes.append(
                f"Couldn't load Accepted bids ({type(exc).__name__}). "
                "Job Start's Bid Board fetch may be unavailable."
            )
            print(f"[billing] accepted_bids failed: {exc}", file=sys.stderr)

        try:
            queues["projects_billing"] = fut_proj.result()
        except Exception as exc:  # noqa: BLE001
            notes.append(
                f"Couldn't load Projects invoice-status list "
                f"({type(exc).__name__}). Optional secondary queue skipped."
            )
            print(f"[billing] projects_billing failed: {exc}", file=sys.stderr)

    if not queues["ready_to_invoice"] and not ready_err:
        notes.append(
            "No Operations tasks in Ready to Invoice right now. When crew "
            "moves a job into that group, it shows up here."
        )
    needs_handoff = sum(
        1 for b in queues["accepted_bids"] if b.get("needs_handoff")
    )
    if needs_handoff:
        notes.append(
            f"{needs_handoff} accepted bid(s) still need a Job Start handoff "
            "before they have a Projects item to invoice."
        )

    return {
        "ok": True,
        "queues": queues,
        "generated_at": _now_iso(),
        "notes": notes,
        "search_backend": (
            "rich" if _rich_search_available() else "fallback"
        ),
        "counts": {
            "ready_to_invoice": len(queues["ready_to_invoice"]),
            "accepted_bids": len(queues["accepted_bids"]),
            "projects_billing": len(queues["projects_billing"]),
            "needs_handoff": needs_handoff,
        },
    }


def _rich_search_available() -> bool:
    if monday_search is None:
        return False
    return (
        hasattr(monday_search, "search_projects_rich")
        and hasattr(monday_search, "search_bids_rich")
    )


def search_billing(mc, q: str) -> dict:
    """
    Multi-way billing search.

    Uses adapters.monday.search.search_projects_rich + search_bids_rich when
    importable; otherwise falls back to:
      - adapters.monday.co.search_projects
      - adapters.monday.estimate.search_bids

    Returns { ok, q, projects: [...], bids: [...], backend: "rich"|"fallback",
              notes: [...] }.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {
            "ok": True,
            "q": query,
            "projects": [],
            "bids": [],
            "backend": "rich" if _rich_search_available() else "fallback",
            "notes": ["Type at least 2 characters to search."],
        }

    client = _client(mc)
    notes: list[str] = []
    projects: list[dict] = []
    bids: list[dict] = []
    used_rich = False

    if _rich_search_available():
        rich_ok = True
        try:
            raw_projects = monday_search.search_projects_rich(client, query)
            projects = [bq.shape_search_project(r) for r in (raw_projects or [])]
        except Exception as exc:  # noqa: BLE001
            rich_ok = False
            notes.append(
                f"Rich project search failed ({type(exc).__name__}); "
                "using fallback."
            )
            print(f"[billing] search_projects_rich failed: {exc}",
                  file=sys.stderr)
        try:
            raw_bids = monday_search.search_bids_rich(client, query)
            bids = [bq.shape_search_bid(r) for r in (raw_bids or [])]
        except Exception as exc:  # noqa: BLE001
            rich_ok = False
            notes.append(
                f"Rich bid search failed ({type(exc).__name__}); "
                "using fallback."
            )
            print(f"[billing] search_bids_rich failed: {exc}",
                  file=sys.stderr)
        used_rich = rich_ok

    if not used_rich:
        # Fallback path: co.search_projects + estimate.search_bids.
        # Documented: adapters/monday/search.py is owned by another agent —
        # import when present; until then this is the production path.
        if not projects:
            try:
                raw_projects = monday_co.search_projects(client, query)
                projects = [
                    bq.shape_search_project(r) for r in (raw_projects or [])
                ]
            except Exception as exc:  # noqa: BLE001
                notes.append(
                    f"Project search failed ({type(exc).__name__}). "
                    "Confirm Monday is configured."
                )
                print(f"[billing] co.search_projects failed: {exc}",
                      file=sys.stderr)
        if not bids:
            try:
                raw_bids = monday_estimate.search_bids(client, query)
                bids = [bq.shape_search_bid(r) for r in (raw_bids or [])]
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Bid search failed ({type(exc).__name__}).")
                print(f"[billing] estimate.search_bids failed: {exc}",
                      file=sys.stderr)
        if not _rich_search_available():
            notes.append(
                "Using search fallback (adapters.monday.search not present "
                "yet — co.search_projects + estimate.search_bids). Rich "
                "multi-field search lands when search.py ships."
            )

    backend = "rich" if used_rich else "fallback"
    if not projects and not bids:
        notes.append(
            "No Projects or Bid Board matches. Try a builder name, street "
            "fragment, city, supervisor, Project #, or Estimate #."
        )

    return {
        "ok": True,
        "q": query,
        "projects": projects,
        "bids": bids,
        "backend": backend,
        "notes": notes,
    }
