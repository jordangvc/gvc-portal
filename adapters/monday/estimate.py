"""
Monday write-back for finalized estimates.
=========================================================================
Confirmed design (2026-06-10): at finalize (Gmail draft creation), the
Bid Board (renamed from "Opportunities" 2026-07-02, same board id) reflects
the estimate —

  1. Find the matching Bid Board item (name match on the project
     label / address / client); create one in "New Deals (For Estimate)"
     if none exists. An explicit `job.monday_item_id` skips matching.
  2. Backfill FILL-IF-EMPTY ONLY — existing Monday values are never
     overwritten (Monday stays canonical). Columns: scope long-text,
     estimate date, expiration date, rounded total, Project Type.
  3. Always set: `Estimate #` (bare YYYY-MMDD-NNN core; outbound docs use
     EST-{core}) and Stage -> "Sent to Client" (the office sends same-day
     or next-morning; corrections are handled manually at send time).
  4. Attach the PDF to the `Estimate PDF` files column.

Every step is graceful: write_back() returns a report dict and never
raises — a Monday outage must not block the Gmail draft. The numbering
audit trail self-heals on the next estimate (estimate_number.py also
scans the local output dir).

Column ids: see GVC_Estimate_System_Confirmed_Design.md (board 1918846027).
"""
from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from adapters.monday import cache as monday_cache
from adapters.monday.client import MondayClient
from shared.boards import BID_BOARD_ID
from shared.doc_number import is_spine_number
NEW_DEALS_GROUP_ID = "new_group__1"  # "New Deals (For Estimate)" — leads awaiting an estimate
OPEN_DEALS_GROUP_ID = "topics"       # "Open Deals" — estimate sent / deal open

COL_STAGE = "deal_stage"
STAGE_SENT_LABEL = "Sent to Client"
COL_PROJECT_TYPE = "status"
COL_SCOPE = "details"
COL_ESTIMATE_DATE = "date5"
COL_EXPIRY_DATE = "date1"
COL_TOTAL_ROUNDED = "number"
COL_ESTIMATE_NUMBER = "numbers18"
COL_ESTIMATE_PDF = "file_mkvk7hyz"

# Read-side columns for the "prefill from the Bid Board" lookup (board 1918846027).
COL_CUSTOMER_REL = "connect_boards5"   # board_relation -> Customers board (name)
COL_EMAIL_MIRROR = "mirror34"          # mirror -> customer contact_email
COL_CONTACT_MIRROR = "mirror6"         # mirror -> customer contact person
COL_PHONE_MIRROR = "dup__of_mirror"    # mirror -> customer contact_phone
COL_LOCATION = "location5"             # Job Location
COL_SALES_LEAD = "deal_owner"          # people -> salesperson
# Commission Recipient (status) — written on finalize = the selected salesperson
# / bid contact. "Green Valley Contractors" = the company / team-bonus-pool
# account. Created 2026-06-29 on the Bid Board; commission is earned
# when the related invoice is PAID (cash basis — payout report is phase 2).
COL_COMMISSION_RECIPIENT = "color_mm4sy4eq"

PROJECT_TYPE_LABELS = {"residential": "Residential", "commercial": "Commercial"}


def _item_label(client_name: str, job: dict) -> str:
    """Item-name convention: `Street, City, ST ZIP | Client` (shared naming)."""
    from subsystems.jobstart import naming as _naming
    location = (job.get("street_address") or job.get("location") or "").strip()
    return _naming.compose_job_name(
        location, client_name, raw_name=(job.get("name") or "").strip() or None)


def _find_item(mc, label: str, job: dict) -> Optional[int]:
    """Best-effort name match on the Bid Board. None if no match."""
    candidates = [label]
    for key in ("street_address", "name"):
        v = (job.get(key) or "").strip()
        if v and v not in candidates:
            candidates.append(v)
    query = """
    query ($boardId: [ID!], $value: CompareValue!) {
      boards(ids: $boardId) {
        items_page(limit: 10, query_params: {
          rules: [{column_id: "name", compare_value: $value,
                   operator: contains_text}]}) {
          items { id name }
        }
      }
    }
    """
    for needle in candidates:
        data = mc._query(query, {"boardId": [str(BID_BOARD_ID)], "value": needle})
        for board in data.get("boards") or []:
            items = (board.get("items_page") or {}).get("items") or []
            if items:
                return int(items[0]["id"])
    return None


def _create_item(mc, label: str) -> int:
    # The portal only creates a Bid Board item at estimate FINALIZE (the estimate
    # is already sent), so a freshly created item belongs in "Open Deals", not
    # "New Deals (For Estimate)". Existing items get promoted by _promote_to_open_deals.
    query = """
    mutation ($boardId: ID!, $groupId: String!, $name: String!) {
      create_item(board_id: $boardId, group_id: $groupId, item_name: $name) { id }
    }
    """
    data = mc._query(query, {
        "boardId": str(BID_BOARD_ID),
        "groupId": OPEN_DEALS_GROUP_ID,
        "name": label,
    })
    return int(data["create_item"]["id"])


def _get_column_texts(mc, item_id: int, column_ids: list[str]) -> dict:
    query = """
    query ($itemId: [ID!], $cols: [String!]) {
      items(ids: $itemId) { column_values(ids: $cols) { id text } }
    }
    """
    data = mc._query(query, {"itemId": [str(item_id)], "cols": column_ids})
    items = data.get("items") or []
    if not items:
        return {}
    return {cv["id"]: (cv.get("text") or "").strip()
            for cv in items[0].get("column_values") or []}


def _read_columns_full(mc, item_id: int, column_ids: list[str]) -> tuple[str, dict]:
    """
    Read a Bid Board item's name + a set of columns, resolving the three
    value shapes the prefill needs: plain text, mirror display_value, and
    board-relation linked item names. Returns (item_name, {col_id: {...}}).
    """
    query = """
    query ($itemId: [ID!], $cols: [String!]) {
      items(ids: $itemId) {
        id
        name
        column_values(ids: $cols) {
          id
          text
          ... on MirrorValue { display_value }
          ... on BoardRelationValue { linked_items { id name } }
        }
      }
    }
    """
    data = mc._query(query, {"itemId": [str(item_id)], "cols": column_ids})
    items = data.get("items") or []
    if not items:
        return "", {}
    item = items[0]
    cols: dict = {}
    for cv in item.get("column_values") or []:
        cols[cv["id"]] = {
            "text": (cv.get("text") or "").strip(),
            "display": (cv.get("display_value") or "").strip(),
            "linked": [li.get("name") for li in (cv.get("linked_items") or []) if li.get("name")],
        }
    return (item.get("name") or "").strip(), cols


def _project_type_from_label(label: str) -> Optional[str]:
    """Map the bid's 'Project Type' status label to the form's value.
    Only Residential/Commercial map; other labels (Standard/Specialty/N/A) -> None
    so the form keeps its default."""
    low = (label or "").lower()
    if "residential" in low:
        return "residential"
    if "commercial" in low:
        return "commercial"
    return None


def _first(*vals: Optional[str]) -> str:
    for v in vals:
        if v and v.strip():
            return v.strip()
    return ""


def _date_yyyy_mm_dd(raw: str) -> str:
    """Normalize Monday date-column text to YYYY-MM-DD when possible."""
    t = (raw or "").strip()
    if len(t) >= 10 and t[4] == "-" and t[7] == "-":
        return t[:10]
    return t


def build_prefill(item_id: int, item_name: str, cols: dict) -> dict:
    """
    PURE: map Bid Board column values -> the estimate-form prefill shape
    (the same {prepared_by, client, job, estimate} applyData() consumes).
    Fields stored on the bid are filled, including Estimate Date (date5) and
    Expiry (date1) when present. Line items / pricing are not on the bid —
    those stay for the estimator (or a revision sidecar load).

    `cols` is the {col_id: {"text","display","linked"}} map from _read_columns_full.
    """
    def c(col_id: str) -> dict:
        return cols.get(col_id) or {}

    customer_rel = c(COL_CUSTOMER_REL)
    client_name = _first(
        (customer_rel.get("linked") or [""])[0] if customer_rel.get("linked") else "",
        # fallback: the item name convention is "[Address] | [Client]"
        item_name.split("|")[-1] if "|" in item_name else "",
    )

    client = {}
    if client_name:
        client["name"] = client_name
    contact = _first(c(COL_CONTACT_MIRROR).get("display"), c(COL_CONTACT_MIRROR).get("text"))
    email = _first(c(COL_EMAIL_MIRROR).get("display"), c(COL_EMAIL_MIRROR).get("text"))
    phone = _first(c(COL_PHONE_MIRROR).get("display"), c(COL_PHONE_MIRROR).get("text"))
    if contact:
        client["contact_name"] = contact
    if email:
        client["email"] = email
    if phone:
        client["phone"] = phone

    job: dict = {"monday_item_id": item_id}
    if item_name:
        job["name"] = item_name
    location = _first(c(COL_LOCATION).get("text"))
    if location:
        job["location"] = location
    scope = _first(c(COL_SCOPE).get("text"))
    if scope:
        job["scope_summary"] = scope
    ptype = _project_type_from_label(c(COL_PROJECT_TYPE).get("text"))
    if ptype:
        job["project_type"] = ptype
    # Plan Folder # (Jake's numbered Drive folder) — digits only when present.
    plan_raw = _first(c(COL_PLAN_FOLDER).get("text"))
    if plan_raw:
        m = re.match(r"\s*(\d{1,5})", plan_raw)
        if m:
            job["plan_folder_number"] = m.group(1)

    prepared_by = {}
    sales = _first(c(COL_SALES_LEAD).get("text"))
    if sales:
        # people column text can be "First Last, Other Name" — take the first.
        prepared_by["name"] = sales.split(",")[0].strip()

    estimate: dict = {}
    est_date = _date_yyyy_mm_dd(_first(c(COL_ESTIMATE_DATE).get("text")))
    if est_date:
        estimate["date"] = est_date
    expiry = _date_yyyy_mm_dd(_first(c(COL_EXPIRY_DATE).get("text")))
    if expiry:
        estimate["expiry_date"] = expiry

    prefill = {"client": client, "job": job, "estimate": estimate}
    if prepared_by:
        prefill["prepared_by"] = prepared_by
    return prefill


def existing_estimate_number(cols: dict) -> str:
    """PURE: the bid's `Estimate #` column text ('' when never set).
    A non-empty value means an estimate has already been sent for this deal —
    the signal the revision flow keys on."""
    return ((cols.get(COL_ESTIMATE_NUMBER) or {}).get("text") or "").strip()


def lookup_bid(mc, item_id: int) -> dict:
    """
    Read a Bid Board item and return an estimate-form prefill (build_prefill
    shape) + a `_notes` list of what could not be auto-filled. Raises if the item
    doesn't exist so the caller can return a clean 404/422.

    When the deal already carries an `Estimate #`, the prefill includes
    `_existing_estimate = {"number": ...}` so the caller can offer the
    revision flow (load the as-sent sidecar from Drive, same outbound number).
    """
    col_ids = [COL_CUSTOMER_REL, COL_EMAIL_MIRROR, COL_CONTACT_MIRROR, COL_PHONE_MIRROR,
               COL_LOCATION, COL_SCOPE, COL_PROJECT_TYPE, COL_SALES_LEAD,
               COL_ESTIMATE_NUMBER, COL_PLAN_FOLDER,
               COL_ESTIMATE_DATE, COL_EXPIRY_DATE]
    item_name, cols = _read_columns_full(mc, item_id, col_ids)
    if not item_name and not cols:
        raise ValueError(f"No Bid Board item found for id {item_id}.")
    prefill = build_prefill(item_id, item_name, cols)
    prefill["_matched"] = {
        "item_id": item_id,
        "item_name": item_name,
        "url": (f"https://greenvalleycontractors.monday.com/boards/"
                f"{BID_BOARD_ID}/pulses/{item_id}"),
    }
    prefill["_notes"] = [
        "Line items and pricing aren't stored on the bid — add them below.",
    ]
    est_no = existing_estimate_number(cols)
    if est_no:
        prefill["_existing_estimate"] = {"number": est_no}
    return prefill


def search_bids(mc, q: str, *, limit: int = 15) -> list[dict]:
    """
    Search the Bid Board by item name OR `Estimate #` (two
    contains_text queries — Monday ANDs rules, so OR needs two calls).
    Returns [{item_id, name, estimate_number, stage, url}] deduped, capped at
    `limit`. Lets office staff find a previously sent estimate without
    hunting down the Monday URL.

    Legs run in parallel; identical queries are short-TTL cached so a retry
    or a second form doesn't pay Monday again.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []
    cache_key = f"search:bids:{q.lower()}:{int(limit)}"
    return monday_cache.get_or_set(
        cache_key,
        lambda: _search_bids_uncached(mc, q, limit=limit),
        ttl=monday_cache.search_ttl(),
    )


def _search_bids_uncached(mc, q: str, *, limit: int = 15) -> list[dict]:
    query = """
    query ($boardId: [ID!], $columnId: ID!, $value: CompareValue!) {
      boards(ids: $boardId) {
        items_page(limit: 25, query_params: {
          rules: [{column_id: $columnId, compare_value: $value,
                   operator: contains_text}]}) {
          items {
            id
            name
            column_values(ids: ["numbers18", "deal_stage"]) { id text }
          }
        }
      }
    }
    """

    def _leg(column_id: str):
        # Fresh session per leg — requests.Session is not thread-safe.
        token = None
        try:
            token = mc.session.headers.get("Authorization")
        except Exception:  # noqa: BLE001 — fall back to env-configured client
            token = None
        local = MondayClient(token=token) if token else MondayClient()
        return local._query(query, {
            "boardId": [str(BID_BOARD_ID)],
            "columnId": column_id,
            "value": q,
        })

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {pool.submit(_leg, col): col
                for col in ("name", COL_ESTIMATE_NUMBER)}
        for fut in as_completed(futs):
            column_id = futs[fut]
            try:
                data = fut.result()
            except Exception as e:  # noqa: BLE001 — a failed leg shouldn't kill the other
                print(f"[monday-estimate] search leg {column_id!r} failed: {e}",
                      file=sys.stderr)
                continue
            for board in data.get("boards") or []:
                for item in (board.get("items_page") or {}).get("items") or []:
                    item_id = int(item["id"])
                    if item_id in results:
                        continue
                    texts = {cv["id"]: (cv.get("text") or "").strip()
                             for cv in item.get("column_values") or []}
                    results[item_id] = {
                        "item_id": item_id,
                        "name": (item.get("name") or "").strip(),
                        "estimate_number": texts.get(COL_ESTIMATE_NUMBER, ""),
                        "stage": texts.get(COL_STAGE, ""),
                        "url": (f"https://greenvalleycontractors.monday.com/boards/"
                                f"{BID_BOARD_ID}/pulses/{item_id}"),
                    }
    return list(results.values())[:limit]


def _set_columns(mc, item_id: int, values: dict) -> None:
    if not values:
        return
    query = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(board_id: $boardId, item_id: $itemId,
                                    column_values: $values) { id }
    }
    """
    mc._query(query, {
        "boardId": str(BID_BOARD_ID),
        "itemId": str(item_id),
        "values": json.dumps(values),
    })


def _set_status_create_labels(mc, item_id: int, col_id: str, label: str) -> None:
    """Set a single status column to `label`, creating the label if it doesn't
    exist yet. Used for Commission Recipient so a new salesperson's label is
    added automatically (the roster lives in the portal, not on the board)."""
    query = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(board_id: $boardId, item_id: $itemId,
                                    column_values: $values,
                                    create_labels_if_missing: true) { id }
    }
    """
    mc._query(query, {
        "boardId": str(BID_BOARD_ID),
        "itemId": str(item_id),
        "values": json.dumps({col_id: {"label": label}}),
    })


def _item_group_id(mc, item_id: int) -> Optional[str]:
    """Return the item's current group id (None if it can't be read)."""
    query = "query ($itemId: [ID!]) { items(ids: $itemId) { group { id } } }"
    data = mc._query(query, {"itemId": [str(item_id)]})
    items = data.get("items") or []
    if not items:
        return None
    return ((items[0] or {}).get("group") or {}).get("id")


def _promote_to_open_deals(mc, item_id: int) -> bool:
    """Move a bid from "New Deals (For Estimate)" to "Open Deals" on
    finalize — restoring the behavior the retired Monday automation used to do
    on stage→Sent. Only moves FROM New Deals, so Won/Lost/already-Open deals are
    never disturbed. Returns True if a move happened."""
    if _item_group_id(mc, item_id) != NEW_DEALS_GROUP_ID:
        return False
    query = """
    mutation ($itemId: ID!, $groupId: String!) {
      move_item_to_group(item_id: $itemId, group_id: $groupId) { id }
    }
    """
    mc._query(query, {"itemId": str(item_id), "groupId": OPEN_DEALS_GROUP_ID})
    return True


def _attach_pdf(mc, item_id: int, pdf_path: Path) -> None:
    """Upload the PDF to the Estimate PDF files column (multipart endpoint)."""
    query = (
        f"mutation ($file: File!) {{ add_file_to_column "
        f"(item_id: {item_id}, column_id: \"{COL_ESTIMATE_PDF}\", file: $file) {{ id }} }}"
    )
    with open(pdf_path, "rb") as fh:
        resp = mc.session.post(
            "https://api.monday.com/v2/file",
            headers={"Content-Type": None},  # let requests set multipart boundary
            data={"query": query, "map": json.dumps({"f": ["variables.file"]})},
            files={"f": (pdf_path.name, fh, "application/pdf")},
            timeout=60,
        )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"Monday file upload error: {body['errors']}")


def build_column_updates(current: dict, est: dict, job: dict,
                         *, estimate_number: str, revise: bool = False) -> dict:
    """
    PURE: assemble the change_multiple_column_values payload for a finalize.

    Default (new estimate): FILL-IF-EMPTY — existing Monday values are never
    overwritten (Monday stays canonical for manual edits).

    revise=True (explicit "Update this Estimate", decided 2026-07-02): the
    revision IS the new truth — scope, dates, rounded total, and project
    type are OVERWRITTEN so the board reflects the estimate the client is
    actually looking at. Estimate # + Stage are always set in both modes.
    Commission Recipient is handled separately and stays first-attribution-
    wins in BOTH modes (a revision never re-routes commission).
    """
    values: dict = {}

    def want(col: str) -> bool:
        return revise or not current.get(col)

    if want(COL_SCOPE) and est.get("scope_summary"):
        values[COL_SCOPE] = est["scope_summary"]
    if want(COL_ESTIMATE_DATE) and est.get("date"):
        values[COL_ESTIMATE_DATE] = {"date": est["date"]}
    if want(COL_EXPIRY_DATE) and est.get("expiry_date"):
        values[COL_EXPIRY_DATE] = {"date": est["expiry_date"]}
    if want(COL_TOTAL_ROUNDED) and est.get("main_total"):
        values[COL_TOTAL_ROUNDED] = str(round(est["main_total"], 2))
    ptype = PROJECT_TYPE_LABELS.get((job.get("project_type") or "").lower())
    if want(COL_PROJECT_TYPE) and ptype:
        values[COL_PROJECT_TYPE] = {"label": ptype}

    # Always set: Estimate # + Stage.
    values[COL_ESTIMATE_NUMBER] = estimate_number
    values[COL_STAGE] = {"label": STAGE_SENT_LABEL}
    return values


def write_back(enriched: dict, *, pdf_path: Path, estimate_number: str,
               revise: bool = False) -> dict:
    """
    Sync a finalized estimate to Monday. Returns a report dict; NEVER raises.
    `revise=True` switches the column sync from fill-if-empty to overwrite
    (see build_column_updates) — used by the estimate revision flow.
    """
    report: dict = {"monday_synced": False}
    try:
        from adapters.monday.client import MondayClient, MondayNotConfigured
        try:
            mc = MondayClient()
        except MondayNotConfigured as e:
            report["monday_status"] = f"SKIPPED — {e}"
            return report

        client_name = (enriched.get("client") or {}).get("name") or ""
        job = enriched.get("job") or {}
        est = enriched.get("estimate") or {}
        label = _item_label(client_name, job)

        explicit = job.get("monday_item_id")
        if explicit:
            item_id, created = int(explicit), False
        else:
            found = _find_item(mc, label, job)
            if found is None:
                item_id, created = _create_item(mc, label), True
            else:
                item_id, created = found, False
        report["monday_item_id"] = item_id
        report["monday_item_created"] = created

        # ---- Column sync (fill-if-empty; overwrite on revise) ----
        backfill_cols = [COL_SCOPE, COL_ESTIMATE_DATE, COL_EXPIRY_DATE,
                         COL_TOTAL_ROUNDED, COL_PROJECT_TYPE, COL_COMMISSION_RECIPIENT]
        current = _get_column_texts(mc, item_id, backfill_cols)
        values = build_column_updates(current, est, job,
                                      estimate_number=estimate_number, revise=revise)
        _set_columns(mc, item_id, values)
        key = "monday_overwrote_columns" if revise else "monday_backfilled_columns"
        report[key] = sorted(values.keys())

        # ---- Commission Recipient (fill-if-empty) = the salesperson/bid contact ----
        # First attribution wins; we never clobber an already-set recipient.
        # "Green Valley Contractors" (the company account) routes to the team
        # bonus pool. Labels auto-create as new salespeople appear.
        recipient = ((enriched.get("prepared_by") or {}).get("name") or "").strip()
        if recipient and not current.get(COL_COMMISSION_RECIPIENT):
            try:
                _set_status_create_labels(mc, item_id, COL_COMMISSION_RECIPIENT, recipient)
                report["monday_commission_recipient"] = recipient
            except Exception as e:  # noqa: BLE001 — non-fatal attribution write
                report["monday_commission_recipient_error"] = f"{type(e).__name__}: {e}"

        # ---- Plan Folder # (fill / correct from the estimate form) ----
        # Soft-fail: a Monday hiccup here must never block the Gmail draft or
        # the Projects/.../Estimate/ Drive save.
        plan_no = (job.get("plan_folder_number") or "").strip()
        m_plan = re.match(r"\s*(\d{1,5})", plan_no)
        if m_plan:
            try:
                set_plan_folder_number(mc, item_id, m_plan.group(1))
                report["monday_plan_folder_number"] = m_plan.group(1)
            except Exception as e:  # noqa: BLE001 — non-fatal
                report["monday_plan_folder_error"] = f"{type(e).__name__}: {e}"

        # ---- Promote New Deals → Open Deals on finalize (retired-automation parity) ----
        try:
            if _promote_to_open_deals(mc, item_id):
                report["monday_group_moved"] = "New Deals → Open Deals"
        except Exception as e:  # noqa: BLE001 — non-fatal group move
            report["monday_group_move_error"] = f"{type(e).__name__}: {e}"

        # ---- Attach PDF ----
        try:
            _attach_pdf(mc, item_id, Path(pdf_path))
            report["monday_pdf_attached"] = True
        except Exception as e:  # noqa: BLE001 — non-fatal
            report["monday_pdf_attached"] = False
            report["monday_pdf_error"] = f"{type(e).__name__}: {e}"

        report["monday_synced"] = True
        report["monday_item_url"] = (
            f"https://greenvalleycontractors.monday.com/boards/"
            f"{BID_BOARD_ID}/pulses/{item_id}"
        )
        return report
    except Exception as e:  # noqa: BLE001 — a Monday failure never blocks the draft
        report["monday_status"] = f"FAILED — {type(e).__name__}: {e}"
        print(f"[monday-estimate] write-back failed: {e}", file=sys.stderr)
        return report


# ---------------------------------------------------------------------------
# Sent-watcher support (2026-07-24)
# ---------------------------------------------------------------------------
# "Emailed on" (date) on the Bid Board — stamped by the sent-watcher when the
# estimate email is detected as ACTUALLY sent from hello@ (empty = draft still
# waiting). Created 2026-07-24 via the Monday MCP. Deliberately separate from
# Stage: write_back sets Stage "Sent to Client" at DRAFT time (historical
# semantics other automations depend on) — this column is the truthful send
# signal.
COL_EMAILED_ON = os.environ.get("GVC_MONDAY_BID_EMAILED_ON_COL", "date_mm5kn8d2")


def fetch_pending_estimates(mc, *, max_pages: int = 5) -> list[dict]:
    """
    Bid Board rows with an Estimate # but no 'Emailed on' yet — the
    sent-watcher's work list. Returns [{item_id, item_name, estimate_number,
    estimate_date}]; recency bounding (estimate_date within N days) is the
    caller's job. Paged at 200/page, capped at max_pages as a runaway guard.
    """
    cols = ", ".join(f'"{c}"' for c in (COL_ESTIMATE_NUMBER, COL_EMAILED_ON,
                                        COL_ESTIMATE_DATE))
    query = """
    query ($boardId: [ID!], $cursor: String) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            name
            column_values(ids: [%s]) { id text }
          }
        }
      }
    }
    """ % cols
    out: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        data = mc._query(query, {"boardId": [str(BID_BOARD_ID)], "cursor": cursor})
        boards = data.get("boards") or []
        if not boards:
            break
        page = boards[0].get("items_page") or {}
        for item in page.get("items") or []:
            vals = {cv["id"]: (cv.get("text") or "").strip()
                    for cv in item.get("column_values") or []}
            number = vals.get(COL_ESTIMATE_NUMBER, "")
            if not number or vals.get(COL_EMAILED_ON, ""):
                continue
            out.append({
                "item_id": int(item["id"]),
                "item_name": (item.get("name") or "").strip(),
                "estimate_number": number,
                "estimate_date": vals.get(COL_ESTIMATE_DATE, ""),
            })
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


def stamp_estimate_emailed(mc, item_id: int, date_str: str) -> None:
    """Set the Bid Board row's 'Emailed on' date (sent-watcher writeback)."""
    mutation = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(board_id: $boardId, item_id: $itemId,
                                    column_values: $values) { id }
    }
    """
    mc._query(mutation, {
        "boardId": str(BID_BOARD_ID),
        "itemId": str(item_id),
        "values": json.dumps({COL_EMAILED_ON: {"date": date_str}}),
    })


# ---------------------------------------------------------------------------
# Jake's plan folder link (2026-07-29)
# ---------------------------------------------------------------------------
# "Plan Folder #" (text) on the Bid Board — the leading number of Jake's Drive
# plan folder, e.g. 341 for "341 - Obara Office Renovation - Sent". Created
# 2026-07-29 via the Monday MCP. This is the ONLY reliable key between the
# portal and Jake's folders: a July-2026 reconciliation of 247 folders had to
# fall back on fuzzy name matching, and 43 board rows carry a project name in
# the Estimate # field instead of a number.
COL_PLAN_FOLDER = os.environ.get("GVC_MONDAY_BID_PLAN_FOLDER_COL", "text_mm5rjq00")

# Root of Jake's plan folder in Drive (parent of the numbered job folders).
JAKE_PLAN_FOLDER_ROOT = os.environ.get(
    "GVC_JAKE_PLAN_FOLDER_ID", "1X1vuutnTuCN0hxTZSANmm3QC6SQ41Gc0")

def is_portal_estimate_number(value) -> bool:
    """True when the Estimate # cell already holds a portal spine number
    (bare YYYY-MMDD-NNN or EST-/PRO-/INV- prefixed). Anything else — blank,
    or a project name like 'Hickey Residence' — is treated as unset so
    finalize can write the real number over it."""
    return is_spine_number(str(value or "").strip())


def read_plan_folder_number(mc, item_id: int) -> Optional[str]:
    """The Bid Board item's Plan Folder # (digits only), or None."""
    texts = _get_column_texts(mc, int(item_id), [COL_PLAN_FOLDER])
    raw = (texts.get(COL_PLAN_FOLDER) or "").strip()
    m = re.match(r"\s*(\d{1,5})", raw)
    return m.group(1) if m else None


def set_plan_folder_number(mc, item_id: int, number: str) -> None:
    """Persist the Plan Folder # on the Bid Board (fill-in / correction from
    the estimate form) so it only ever has to be typed once."""
    mutation = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(board_id: $boardId, item_id: $itemId,
                                    column_values: $values) { id }
    }
    """
    mc._query(mutation, {
        "boardId": str(BID_BOARD_ID), "itemId": str(int(item_id)),
        "values": json.dumps({COL_PLAN_FOLDER: str(number)}),
    })
