"""
Monday reads for the Billing Hub (office invoicing queue).
=========================================================================
READ-ONLY lists that pre-populate work ready to bill without memorizing
Project #s (docs/plans/2026-08-04-estimate-qa-billing-hub.md):

  fetch_ready_to_invoice()  — Operations board group "Ready to Invoice"
                              (group_mm3zq4q2 — same id Job Check skips).
  fetch_accepted_bids()     — Bid Board Stage = Accepted, reshaped from
                              jobstart.fetch_bids (prefer not-yet-handed-off).
  fetch_projects_billing()  — optional Projects rows whose Invoice Status
                              (status0) is set and not "Not Started".

List paths use monday_cache stale-while-revalidate (same pattern as
jobcheck / jobstart / morning) so cold Cloud Run instances stay snappy.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Optional
from urllib.parse import quote

from adapters.monday import cache as monday_cache
from adapters.monday import jobstart as monday_jobstart
from shared import boards
from shared.boards import (
    BID_BOARD_ID,
    OPERATIONS_BOARD_ID,
    PROJECTS_BOARD_ID,
)

# Ops group the office owns once crew work is done (JOBCHECK_SKIP_GROUP_IDS).
READY_TO_INVOICE_GROUP_ID = "group_mm3zq4q2"

# Operations columns useful on the Ready-to-Invoice queue.
OPS_COL_PROJECT_LINK = "link_to_projects"
OPS_COL_LOCATION = "lookup_mknf1rdw"
OPS_COL_READY_DATE = "date_mm3zry96"       # "Ready for Invoice Date"
OPS_COL_BILLABLE = "color_mm2xd40t"        # "BIllable"
OPS_COL_STAGE = "status"
OPS_COL_PROJECT_STATUS = "mirror3"

OPS_READ_COLUMNS = (
    OPS_COL_PROJECT_LINK,
    OPS_COL_LOCATION,
    OPS_COL_READY_DATE,
    OPS_COL_BILLABLE,
    OPS_COL_STAGE,
    OPS_COL_PROJECT_STATUS,
)

# Projects board columns for the secondary billing list + project# enrichment.
P_COL_PROJECT_NUMBER = "text_mm4fvj91"
P_COL_BUILDER = "text"
P_COL_SUPERVISOR = "text5"
P_COL_LOCATION = "location5"
P_COL_INVOICE_STATUS = boards.JOBSTART_P_COL_INVOICE_STATUS  # status0
P_COL_DEAL_STAGE = boards.JOBSTART_P_COL_PROJECT_STATUS      # deal_stage

PROJECTS_BILLING_COLUMNS = (
    P_COL_PROJECT_NUMBER,
    P_COL_BUILDER,
    P_COL_SUPERVISOR,
    P_COL_LOCATION,
    P_COL_INVOICE_STATUS,
    P_COL_DEAL_STAGE,
)

# Invoice Status labels that mean "nothing for the billing hub to show".
_SKIP_INVOICE_STATUSES = frozenset({
    "",
    boards.JOBSTART_P_NOT_STARTED_LABEL.strip().lower(),
    "n/a",
    "na",
    "-",
})

_VALUE_FRAGMENT = """
          id
          text
          value
          ... on MirrorValue { display_value }
          ... on BoardRelationValue { display_value linked_item_ids }
"""


def _item_url(board_id: int, item_id) -> str:
    return (f"https://greenvalleycontractors.monday.com/boards/"
            f"{board_id}/pulses/{item_id}")


def _column_text(cv: dict) -> Optional[str]:
    for key in ("display_value", "text"):
        raw = cv.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _linked_ids(cv: Optional[dict]) -> list[int]:
    if not cv:
        return []
    # Prefer typed GraphQL field when present.
    typed = cv.get("linked_item_ids") or []
    out: list[int] = []
    for pid in typed:
        try:
            out.append(int(pid))
        except (TypeError, ValueError):
            continue
    if out:
        return out
    try:
        parsed = json.loads(cv.get("value") or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    for entry in parsed.get("linkedPulseIds") or []:
        pid = entry.get("linkedPulseId") if isinstance(entry, dict) else None
        if not pid:
            continue
        try:
            out.append(int(pid))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# Ready to Invoice (Operations)
# ---------------------------------------------------------------------------

def fetch_ready_to_invoice(mc) -> list[dict]:
    """
    Operations items in the Ready to Invoice group, normalized:
      {item_id, name, url, group_id, group_title, project_item_id,
       project_name, project_number, location, ready_date, billable,
       stage, project_status}
    Cached short-TTL SWR. project_number is filled when the linked Projects
    item can be resolved in one follow-up batch read.
    """
    return monday_cache.get_or_set_swr(
        "list:billing:ready_to_invoice",
        lambda: _fetch_ready_to_invoice_uncached(mc),
        ttl=monday_cache.list_ttl(),
        stale_ttl=monday_cache.stale_ttl(),
    )


def _fetch_ready_to_invoice_uncached(mc) -> list[dict]:
    col_ids = json.dumps(list(OPS_READ_COLUMNS))
    # Prefer Monday's group filter so we don't walk the whole Ops board.
    query = """
    query ($boardId: [ID!], $cursor: String, $groupIds: CompareValue!) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor, query_params: {
          rules: [{column_id: "group", compare_value: $groupIds,
                   operator: any_of}]}) {
          cursor
          items {
            id
            name
            group { id title }
            column_values(ids: %s) { %s }
          }
        }
      }
    }
    """ % (col_ids, _VALUE_FRAGMENT)

    rows: list[dict] = []
    cursor: Optional[str] = None
    try:
        while True:
            data = mc._query(query, {
                "boardId": [str(OPERATIONS_BOARD_ID)],
                "cursor": cursor,
                "groupIds": [READY_TO_INVOICE_GROUP_ID],
            })
            board_list = data.get("boards") or []
            if not board_list:
                break
            page = board_list[0]["items_page"]
            for item in page.get("items") or []:
                row = _normalize_ops_ready(item)
                if row is not None:
                    rows.append(row)
            cursor = page.get("cursor")
            if not cursor:
                break
    except Exception as exc:  # noqa: BLE001 — fall back to group-scoped page
        print(
            f"[monday-billing] group-filtered Ready-to-Invoice fetch failed "
            f"({type(exc).__name__}: {exc}); falling back to groups(ids:) page",
            file=sys.stderr,
        )
        try:
            rows = _fetch_ready_to_invoice_by_group(mc)
        except Exception as exc2:  # noqa: BLE001 — never walk the full ~2k board
            print(
                f"[monday-billing] Ready-to-Invoice group page also failed "
                f"({type(exc2).__name__}: {exc2}); returning empty queue",
                file=sys.stderr,
            )
            rows = []

    _enrich_project_numbers(mc, rows)
    return rows


def _fetch_ready_to_invoice_by_group(mc) -> list[dict]:
    """Fallback: page ONLY the Ready-to-Invoice group (never the whole board).

    Same anti-pattern fix as Morning Ops (r82): full-board walk + Python
    filter costs tens of seconds on ~2k Completed Tasks. Prefer boards →
    groups(ids:) → items_page when the items_page query_params filter fails.
    """
    col_ids = json.dumps(list(OPS_READ_COLUMNS))
    query = """
    query ($boardId: [ID!], $groupIds: [String], $cursor: String) {
      boards(ids: $boardId) {
        groups(ids: $groupIds) {
          id
          items_page(limit: 200, cursor: $cursor) {
            cursor
            items {
              id
              name
              group { id title }
              column_values(ids: %s) { %s }
            }
          }
        }
      }
    }
    """ % (col_ids, _VALUE_FRAGMENT)
    rows: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {
            "boardId": [str(OPERATIONS_BOARD_ID)],
            "groupIds": [READY_TO_INVOICE_GROUP_ID],
            "cursor": cursor,
        })
        board_list = data.get("boards") or []
        if not board_list:
            break
        groups = board_list[0].get("groups") or []
        if not groups:
            break
        page = groups[0].get("items_page") or {}
        for item in page.get("items") or []:
            row = _normalize_ops_ready(item)
            if row is not None:
                rows.append(row)
        cursor = page.get("cursor")
        if not cursor:
            break
    return rows


def _normalize_ops_ready(item: dict) -> Optional[dict]:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    group = item.get("group") or {}
    cvs = {cv["id"]: cv for cv in item.get("column_values") or []}
    link_cv = cvs.get(OPS_COL_PROJECT_LINK) or {}
    project_ids = _linked_ids(link_cv)
    return {
        "item_id": int(item["id"]),
        "name": name,
        "url": _item_url(OPERATIONS_BOARD_ID, item["id"]),
        "board_id": OPERATIONS_BOARD_ID,
        "group_id": group.get("id"),
        "group_title": group.get("title"),
        "project_item_id": project_ids[0] if project_ids else None,
        "project_name": _column_text(link_cv),
        "project_number": None,  # filled by _enrich_project_numbers
        "location": _column_text(cvs.get(OPS_COL_LOCATION) or {}),
        "ready_date": _column_text(cvs.get(OPS_COL_READY_DATE) or {}),
        "billable": _column_text(cvs.get(OPS_COL_BILLABLE) or {}),
        "stage": _column_text(cvs.get(OPS_COL_STAGE) or {}),
        "project_status": _column_text(cvs.get(OPS_COL_PROJECT_STATUS) or {}),
        "builder": None,
        "supervisor": None,
    }


def _enrich_project_numbers(mc, rows: list[dict]) -> None:
    """Batch-read Project # (+ builder/supervisor) for linked Projects items."""
    id_map: dict[int, list[dict]] = {}
    for row in rows:
        pid = row.get("project_item_id")
        if not pid:
            continue
        id_map.setdefault(int(pid), []).append(row)
    if not id_map:
        return
    query = """
    query ($ids: [ID!], $cols: [String!]) {
      items(ids: $ids) {
        id
        column_values(ids: $cols) { id text
          ... on MirrorValue { display_value }
          ... on BoardRelationValue { display_value }
        }
      }
    }
    """
    try:
        data = mc._query(query, {
            "ids": [str(i) for i in id_map],
            "cols": [P_COL_PROJECT_NUMBER, P_COL_BUILDER, P_COL_SUPERVISOR,
                     P_COL_LOCATION],
        })
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        print(f"[monday-billing] project# enrich failed: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return
    for item in data.get("items") or []:
        try:
            pid = int(item["id"])
        except (TypeError, ValueError, KeyError):
            continue
        texts = {cv["id"]: _column_text(cv)
                 for cv in item.get("column_values") or []}
        for row in id_map.get(pid) or []:
            row["project_number"] = texts.get(P_COL_PROJECT_NUMBER)
            row["builder"] = texts.get(P_COL_BUILDER)
            row["supervisor"] = texts.get(P_COL_SUPERVISOR)
            if not row.get("location"):
                row["location"] = texts.get(P_COL_LOCATION)


# ---------------------------------------------------------------------------
# Accepted bids (Bid Board)
# ---------------------------------------------------------------------------

def fetch_accepted_bids(mc) -> list[dict]:
    """
    Accepted Bid Board rows reshaped for the billing hub.

    Reuses adapters.monday.jobstart.fetch_bids (cached) and keeps
    stage_state == "accepted". Prefer rows that still need a handoff
    (missing Projects and/or Ops link) — those are the office next-step.
    Fully linked accepted bids are still returned (tagged handed_off=True)
    so billing can open estimate/invoice from the estimate #.
    """
    return monday_cache.get_or_set_swr(
        "list:billing:accepted_bids",
        lambda: _fetch_accepted_bids_uncached(mc),
        ttl=monday_cache.list_ttl(),
        stale_ttl=monday_cache.stale_ttl(),
    )


def _fetch_accepted_bids_uncached(mc) -> list[dict]:
    try:
        bids = monday_jobstart.fetch_bids(mc)
    except Exception as exc:  # noqa: BLE001
        print(f"[monday-billing] jobstart.fetch_bids failed: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return []

    out: list[dict] = []
    for bid in bids or []:
        if (bid.get("stage_state") or "") != "accepted":
            continue
        out.append(_reshape_accepted_bid(bid))
    # Needs-handoff first, then by name.
    out.sort(key=lambda r: (1 if r.get("handed_off") else 0,
                            (r.get("name") or "").lower()))
    return out


def _reshape_accepted_bid(bid: dict) -> dict:
    has_project = bool(bid.get("has_project"))
    has_ops = bool(bid.get("has_ops"))
    handed_off = has_project and has_ops
    item_id = int(bid["item_id"])
    return {
        "item_id": item_id,
        "name": (bid.get("name") or "").strip(),
        "url": bid.get("url") or _item_url(BID_BOARD_ID, item_id),
        "board_id": BID_BOARD_ID,
        "stage": bid.get("stage"),
        "stage_state": bid.get("stage_state"),
        "group_id": bid.get("group_id"),
        "group_title": bid.get("group_title"),
        "estimate_number": bid.get("estimate_number"),
        "estimate_total": bid.get("estimate_total"),
        "location": bid.get("location"),
        "accepted_date": bid.get("accepted_date"),
        "has_project": has_project,
        "has_ops": has_ops,
        "handed_off": handed_off,
        "group_drift": bool(bid.get("group_drift")),
        # Project # is not on the picker projection; left None unless a
        # later enrich path fills it. Deep links fall back to estimate # /
        # jobstart.
        "project_number": bid.get("project_number"),
        "builder": bid.get("builder") or bid.get("customer"),
        "supervisor": bid.get("supervisor"),
    }


# ---------------------------------------------------------------------------
# Projects with invoice-oriented status (optional secondary)
# ---------------------------------------------------------------------------

def fetch_projects_billing(mc, *, limit: int = 75) -> list[dict]:
    """
    Projects rows whose Invoice Status (status0) is set and not "Not Started".
    Secondary list — helpful when Ops hasn't moved the task yet but the
    Projects invoice column already says something invoice-relevant.

    Caps at `limit` after a bounded board walk. Cached SWR.
    """
    return monday_cache.get_or_set_swr(
        f"list:billing:projects_billing:{int(limit)}",
        lambda: _fetch_projects_billing_uncached(mc, limit=limit),
        ttl=monday_cache.list_ttl(),
        stale_ttl=monday_cache.stale_ttl(),
    )


def _fetch_projects_billing_uncached(mc, *, limit: int = 75) -> list[dict]:
    col_ids = json.dumps(list(PROJECTS_BILLING_COLUMNS))
    query = """
    query ($boardId: [ID!], $cursor: String) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            name
            group { id title }
            column_values(ids: %s) { %s }
          }
        }
      }
    }
    """ % (col_ids, _VALUE_FRAGMENT)
    rows: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {"boardId": [str(PROJECTS_BOARD_ID)],
                                 "cursor": cursor})
        board_list = data.get("boards") or []
        if not board_list:
            break
        page = board_list[0]["items_page"]
        for item in page.get("items") or []:
            row = _normalize_project_billing(item)
            if row is not None:
                rows.append(row)
                if len(rows) >= int(limit):
                    return rows
        cursor = page.get("cursor")
        if not cursor:
            break
    return rows


def _normalize_project_billing(item: dict) -> Optional[dict]:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    # Skip top-level CO items (same convention as lien/jobcheck).
    if name.upper().startswith("CO."):
        return None
    group = item.get("group") or {}
    cvs = {cv["id"]: cv for cv in item.get("column_values") or []}
    invoice_status = _column_text(cvs.get(P_COL_INVOICE_STATUS) or {})
    if (invoice_status or "").strip().lower() in _SKIP_INVOICE_STATUSES:
        return None
    deal_stage = _column_text(cvs.get(P_COL_DEAL_STAGE) or {})
    if (deal_stage or "").strip().lower() in {"project lost/canceled"}:
        return None
    return {
        "item_id": int(item["id"]),
        "name": name,
        "url": _item_url(PROJECTS_BOARD_ID, item["id"]),
        "board_id": PROJECTS_BOARD_ID,
        "group_id": group.get("id"),
        "group_title": group.get("title"),
        "project_number": _column_text(cvs.get(P_COL_PROJECT_NUMBER) or {}),
        "builder": _column_text(cvs.get(P_COL_BUILDER) or {}),
        "supervisor": _column_text(cvs.get(P_COL_SUPERVISOR) or {}),
        "location": _column_text(cvs.get(P_COL_LOCATION) or {}),
        "invoice_status": invoice_status,
        "deal_stage": deal_stage,
    }


# ---------------------------------------------------------------------------
# Deep-link helpers (also used by pure billing_queue shaper)
# ---------------------------------------------------------------------------

def invoice_href(*, project_number: Optional[str] = None,
                 monday_item_id: Optional[Any] = None,
                 q: Optional[str] = None) -> str:
    """
    Portal deep link into the invoice generator.

    Pass every known key so the invoice page can prefill without the crew
    copying Project # / Monday ids between screens:
      - project_number (preferred lookup)
      - monday_item_id (Projects item id when Project # missing)
      - q (name/builder/address fallback search)
    """
    params: list[str] = []
    pn = (project_number or "").strip()
    if pn:
        params.append(f"project_number={quote(pn)}")
    if monday_item_id is not None and str(monday_item_id).strip():
        params.append(f"monday_item_id={quote(str(monday_item_id))}")
    needle = (q or "").strip()
    # Only add free-text when Project # is unknown — otherwise the form
    # already has a precise key and q just adds noise.
    if needle and not pn:
        params.append(f"q={quote(needle)}")
    if not params:
        return "/ui/invoice"
    return "/ui/invoice?" + "&".join(params)


def estimate_href(*, estimate_number: Optional[str] = None,
                  q: Optional[str] = None) -> str:
    needle = (estimate_number or q or "").strip()
    if needle:
        return f"/ui/estimate?q={quote(needle)}"
    return "/ui/estimate"


def jobstart_href(*, bid_id: Optional[Any] = None) -> str:
    """Accepted bids that still need a Sales→Ops handoff land on Job Start."""
    if bid_id is not None and str(bid_id).strip():
        return f"/ui/jobstart?bid={quote(str(bid_id))}"
    return "/ui/jobstart"
