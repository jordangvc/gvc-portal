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
  GET  /ui/api/billing/ready-worksheets  — on-demand P5 costing (progressive)
  GET  /ui/api/billing/search?q=
  GET  /ui/api/billing/activity?limit=30
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

from adapters.monday import billing as monday_billing
from adapters.monday import payroll as monday_payroll
from adapters.monday.client import MondayClient
from adapters.monday import co as monday_co
from adapters.monday import estimate as monday_estimate
from orchestrators.invoice_ready_flow import build_item_worksheet
from subsystems.invoice import billing_queue as bq
from subsystems.invoice import ready_stage

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
    """
    notes: list[str] = []
    queues = {
        "ready_to_invoice": [],
        "accepted_bids": [],
        "projects_billing": [],
    }
    ready_err = False

    # Injected clients (tests) stay serial so fakes need not be thread-safe.
    # Live path fans the three Monday walks out — Hub + Billing Hub first paint.
    def _load_ready(client: Any) -> list:
        raw = monday_billing.fetch_ready_to_invoice(client)
        # Attach P5 staged worksheet summaries (soft-fail) so Ready cards
        # show proposed $ without another Monday round-trip.
        staged: dict = {}
        try:
            ids = [r.get("item_id") for r in (raw or []) if r.get("item_id")]
            staged = ready_stage.get_summaries(ids)
        except Exception as exc:  # noqa: BLE001
            print(f"[billing] ready_stage enrich skipped: {exc}", file=sys.stderr)
            staged = {}
        shaped = []
        for r in (raw or []):
            row = dict(r)
            key = str(int(row["item_id"])) if row.get("item_id") is not None else ""
            summary = staged.get(key) or {}
            if summary.get("proposed_total") is not None:
                row["proposed_total"] = summary["proposed_total"]
            if summary.get("model"):
                row["model"] = summary["model"]
            if summary.get("price_label"):
                row["price_label"] = summary["price_label"]
            shaped.append(bq.shape_ready_to_invoice(row))
        return shaped

    def _load_bids(client: Any) -> list:
        raw = monday_billing.fetch_accepted_bids(client)
        return [bq.shape_accepted_bid(r) for r in (raw or [])]

    def _load_projects(client: Any) -> list:
        raw = monday_billing.fetch_projects_billing(client)
        return [bq.shape_project_billing(r) for r in (raw or [])]

    note_by_key = {
        "ready_to_invoice": (
            "Couldn't load Ready to Invoice from Operations "
            "({err}). Check Monday token / board access."
        ),
        "accepted_bids": (
            "Couldn't load Accepted bids ({err}). "
            "Job Start's Bid Board fetch may be unavailable."
        ),
        "projects_billing": (
            "Couldn't load Projects invoice-status list "
            "({err}). Optional secondary queue skipped."
        ),
    }
    loaders = {
        "ready_to_invoice": _load_ready,
        "accepted_bids": _load_bids,
        "projects_billing": _load_projects,
    }

    def _run_one(key: str, client: Any) -> tuple[str, list, Optional[Exception]]:
        try:
            return key, loaders[key](client), None
        except Exception as exc:  # noqa: BLE001
            print(f"[billing] {key} failed: {exc}", file=sys.stderr)
            return key, [], exc

    results: list[tuple[str, list, Optional[Exception]]]
    if mc is not None:
        # Injected clients (tests) stay serial — fakes need not be thread-safe.
        client = _client(mc)
        results = [_run_one(k, client) for k in loaders]
    else:
        # Live: three Monday walks in parallel (own client each).
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [
                pool.submit(_run_one, key, MondayClient())
                for key in loaders
            ]
            results = [fut.result() for fut in as_completed(futs)]

    for key, rows, exc in results:
        queues[key] = rows
        if exc is None:
            continue
        err = f"{type(exc).__name__}"
        notes.append(note_by_key[key].format(err=err))
        if key == "ready_to_invoice":
            ready_err = True

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


def compute_ready_worksheets(
    ready_rows: Optional[list[dict]] = None,
    *,
    persist: bool = True,
    limit: int = 25,
    mc=None,
) -> dict:
    """
    On-demand P5 costing for Billing Ready cards (progressive paint).

    Prefer already-staged portal worksheets. For linked Ops rows still
    missing a proposed $, pull Payroll + build_item_worksheet. When
    persist=True, save so Invoice ``?ops_ready=`` can prefill lines.
    Never Stripe, never auto-send.

    ``ready_rows`` may be shaped hub cards or raw Monday ready rows.
    When None, fetches Ready-to-Invoice from Monday (cached SWR).
    """
    limit = max(1, min(int(limit or 25), 50))
    notes: list[str] = []
    worksheets: dict[str, dict] = {}
    reused = computed = skipped = 0
    errors: list[dict] = []

    if ready_rows is None:
        client = _client(mc)
        try:
            ready_rows = monday_billing.fetch_ready_to_invoice(client) or []
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "code": "MONDAY_FETCH_FAILED",
                "error": f"{type(exc).__name__}: {exc}",
                "worksheets": {},
                "reused": 0,
                "computed": 0,
                "skipped": 0,
                "errors": [],
                "notes": ["Couldn't load Ready to Invoice from Monday."],
                "auto_send": False,
            }

    candidates: list[dict] = []
    for row in ready_rows or []:
        ops_id = row.get("ops_item_id") or row.get("item_id")
        if ops_id is None:
            continue
        try:
            ops_id = int(ops_id)
        except (TypeError, ValueError):
            continue
        pid = row.get("project_item_id")
        if not pid:
            skipped += 1
            continue
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            skipped += 1
            continue
        candidates.append({
            "ops_item_id": ops_id,
            "project_item_id": pid,
            "name": row.get("name") or row.get("project_name") or "",
            "builder": row.get("builder"),
            "project_number": row.get("project_number"),
            "ready_date": row.get("ready_date"),
            "url": row.get("url") or row.get("monday_url"),
            "existing_total": row.get("proposed_total"),
            "existing_model": row.get("model"),
            "existing_label": row.get("price_label"),
        })
        if len(candidates) >= limit:
            break

    if not candidates:
        return {
            "ok": True,
            "worksheets": {},
            "reused": 0,
            "computed": 0,
            "skipped": skipped,
            "errors": [],
            "notes": notes or [
                "No linked Ready-to-Invoice rows to cost yet."
            ],
            "auto_send": False,
        }

    # Reuse staged (or already-shaped) proposed $ first — zero Monday calls.
    try:
        staged = ready_stage.get_summaries(
            [c["ops_item_id"] for c in candidates])
    except Exception as exc:  # noqa: BLE001
        print(f"[billing] ready_stage get_summaries: {exc}", file=sys.stderr)
        staged = {}

    need_compute: list[dict] = []
    for c in candidates:
        key = str(c["ops_item_id"])
        summary = staged.get(key) or {}
        if summary.get("proposed_total") is not None:
            worksheets[key] = {
                "ops_item_id": c["ops_item_id"],
                "project_item_id": c["project_item_id"],
                "proposed_total": summary["proposed_total"],
                "model": summary.get("model"),
                "price_label": summary.get("price_label"),
                "source": "staged",
                "status": summary.get("status") or "staged_worksheet",
            }
            reused += 1
            continue
        if c.get("existing_total") is not None:
            worksheets[key] = {
                "ops_item_id": c["ops_item_id"],
                "project_item_id": c["project_item_id"],
                "proposed_total": c["existing_total"],
                "model": c.get("existing_model"),
                "price_label": c.get("existing_label"),
                "source": "hub",
                "status": "hub_card",
            }
            reused += 1
            continue
        need_compute.append(c)

    def _one(c: dict, client: Any) -> tuple[str, Optional[dict], Optional[str]]:
        key = str(c["ops_item_id"])
        try:
            payroll = monday_payroll.fetch_payroll_for_project(
                client, c["project_item_id"])
            sheet = build_item_worksheet({
                "item_id": c["ops_item_id"],
                "name": c["name"],
                "builder": c.get("builder"),
                "project_item_id": c["project_item_id"],
                "project_number": c.get("project_number"),
                "ready_date": c.get("ready_date"),
                "url": c.get("url"),
            }, payroll)
            if persist:
                ready_stage.save_worksheet(
                    c["ops_item_id"], sheet,
                    actor="billing:ready-worksheets")
            summary = ready_stage.summary_from_sheet(sheet)
            return key, {
                "ops_item_id": c["ops_item_id"],
                "project_item_id": c["project_item_id"],
                "proposed_total": summary.get("proposed_total"),
                "model": summary.get("model"),
                "price_label": summary.get("price_label"),
                "source": "computed",
                "status": "staged_worksheet" if persist else "draft_worksheet",
                "payroll_labor_cost": sheet.get("payroll_labor_cost"),
            }, None
        except Exception as exc:  # noqa: BLE001
            return key, None, f"{type(exc).__name__}: {exc}"

    if need_compute:
        if mc is not None:
            for c in need_compute:
                key, hit, err = _one(c, mc)
                if hit:
                    worksheets[key] = hit
                    computed += 1
                elif err:
                    errors.append({"ops_item_id": c["ops_item_id"], "error": err})
        else:
            workers = min(6, len(need_compute))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {
                    pool.submit(_one, c, MondayClient()): c
                    for c in need_compute
                }
                for fut in as_completed(futs):
                    c = futs[fut]
                    try:
                        key, hit, err = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        errors.append({
                            "ops_item_id": c["ops_item_id"],
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                        continue
                    if hit:
                        worksheets[key] = hit
                        computed += 1
                    elif err:
                        errors.append({
                            "ops_item_id": c["ops_item_id"],
                            "error": err,
                        })

    if computed:
        notes.append(
            f"Costed {computed} Ready job(s) from Payroll "
            f"(company rates — human reviews before send)."
        )
    if reused and not computed:
        notes.append("Proposed amounts loaded from staged worksheets.")
    if skipped:
        notes.append(
            f"{skipped} Ready row(s) skipped — no Projects link yet "
            "(use Job Check → Link Projects)."
        )

    return {
        "ok": len(errors) == 0,
        "worksheets": worksheets,
        "reused": reused,
        "computed": computed,
        "skipped": skipped,
        "errors": errors,
        "notes": notes,
        "auto_send": False,
        "persist": persist,
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

    notes: list[str] = []
    projects: list[dict] = []
    bids: list[dict] = []
    used_rich = False

    # Injected clients (tests) stay serial. Live: projects ∥ bids on separate
    # MondayClients so wall time is max(leg) not sum(leg).
    parallel = mc is None

    if _rich_search_available():
        rich_ok = True

        def _rich_projects(client):
            raw = monday_search.search_projects_rich(client, query)
            return [bq.shape_search_project(r) for r in (raw or [])]

        def _rich_bids(client):
            raw = monday_search.search_bids_rich(client, query)
            return [bq.shape_search_bid(r) for r in (raw or [])]

        if parallel:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_p = pool.submit(_rich_projects, MondayClient())
                fut_b = pool.submit(_rich_bids, MondayClient())
                try:
                    projects = fut_p.result()
                except Exception as exc:  # noqa: BLE001
                    rich_ok = False
                    notes.append(
                        f"Rich project search failed ({type(exc).__name__}); "
                        "using fallback."
                    )
                    print(f"[billing] search_projects_rich failed: {exc}",
                          file=sys.stderr)
                try:
                    bids = fut_b.result()
                except Exception as exc:  # noqa: BLE001
                    rich_ok = False
                    notes.append(
                        f"Rich bid search failed ({type(exc).__name__}); "
                        "using fallback."
                    )
                    print(f"[billing] search_bids_rich failed: {exc}",
                          file=sys.stderr)
        else:
            client = _client(mc)
            try:
                projects = _rich_projects(client)
            except Exception as exc:  # noqa: BLE001
                rich_ok = False
                notes.append(
                    f"Rich project search failed ({type(exc).__name__}); "
                    "using fallback."
                )
                print(f"[billing] search_projects_rich failed: {exc}",
                      file=sys.stderr)
            try:
                bids = _rich_bids(client)
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
        client = _client(mc)
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
