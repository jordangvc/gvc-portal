"""
Monday integration for the Change Order program.
=========================================================================
NEW CO MODEL (decided 2026-07-16, supersedes subitems — Jordan rejected them;
docs/portal-co-parity-design.md): a finalized CO creates/updates

  1. a TOP-LEVEL item on the Projects board (1918846405), named
     `CO.{n} - {parent item title}`, placed in the PARENT's group, carrying
     the full CO identifier `CO.{n}-{base}` in the "Project #" text column
     (text_mm4fvj91 — the linkage + numbering + revision-lookup key), the
     parent's customer/location/contact/GFolder/opportunity values, and the
     four dedicated CO columns (Amount / Status / PDF / Gmail Draft,
     created 2026-07-16);
  2. a task on the Operations board (1920364853), same name, group
     "Activities/Tasks (In-Progress)", Stage=Upcoming, BIllable=Yes,
     Start Date = the CO date, "Link to Projects Board" → the CO item + the
     parent project. Ops. Owner is left for Jordan to assign.

Revisions update BOTH in place (found via the Project # column / task name)
and reset CO Status → Drafted; a prior status of Billed is surfaced as a
warning (WARN + ALLOW, locked 2026-07-16) — the linked invoice is never
touched.

LEGACY: the subitem functions (create_co_subitem, list_billable_cos,
mark_billed / mark_billed_batch's monday_subitem_id path) are kept for
pre-2026-07 COs. Invoice CO-billing writeback prefers top-level item ids
(monday_item_id → mark_billed_item); subitem ids still work for old rows.
Subitem helpers are no longer called at CO create time.

Linking (unchanged): PREFER the Monday Project item (pasted URL or the new
text search); a pasted Drive folder URL is the backup — find_project_by_folder
matches the parent by its "GFolder Link".

write_back() NEVER raises — a Monday outage must not block the PDF / Gmail
draft (same posture as monday/estimate.write_back).
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from adapters.monday import cache as monday_cache
from adapters.monday.client import MondayClient
from shared.boards import BID_BOARD_ID, OPERATIONS_BOARD_ID, PROJECTS_BOARD_ID, SUBITEMS_BOARD_ID

# Projects board columns we read (see monday/client.py for the wider map).
P_COL_CUSTOMER_LINK = "connect_boards9"
P_COL_CONTACT_NAME = "customer_name"
P_COL_SITE = "location5"
# "Project #" — the OLD `numbers9` column was deleted from the board (found
# 2026-07-16; it silently broke base_number autofill). text_mm4fvj91 is the
# only Project # column now; it is ALSO where a CO item carries its full
# CO.{n}-{base} identifier.
P_COL_PROJECT_NUMBER = os.environ.get("GVC_MONDAY_PROJECT_NUMBER_COL", "text_mm4fvj91")
P_COL_GFOLDER = "link_mkwr6ef9"            # "GFolder Link"
P_COL_LINKED_OPPORTUNITY = "board_relation_mm40rg52"
P_COL_PROJECT_TYPE = "status"              # Residential / Commercial / …
P_COL_SCOPE = "details"                    # "Define Scope of Work"
# Same Projects-board ids Job Start writes (shared/boards.JOBSTART_FIELDS).
P_COL_BUILDER = "text"                     # Builder / GC
P_COL_SUPERVISOR = "text5"                 # Site supervisor / contact

# Bid Board: the estimate number lives here — the CO base fallback when the
# project's Project # is empty (it usually is).
BID_COL_ESTIMATE_NUMBER = "numbers18"

# Customers board columns (ids verified on board 1919766765, 2026-07-17).
C_COL_EMAIL = "contact_email"
C_COL_BILLING = "billing_address"
C_COL_PHONE = "contact_phone"
C_COL_CONTACT = "priority"                 # "Contact Name" (text)

# Dedicated CO columns on the PROJECTS board — created via API 2026-07-16.
CO_ITEM_COL_AMOUNT = os.environ.get("GVC_MONDAY_CO_AMOUNT_COL", "numeric_mm5ahj91")
CO_ITEM_COL_STATUS = os.environ.get("GVC_MONDAY_CO_STATUS_COL", "color_mm5akg2n")
CO_ITEM_COL_PDF = os.environ.get("GVC_MONDAY_CO_PDF_COL", "link_mm5apw93")
CO_ITEM_COL_GMAIL = os.environ.get("GVC_MONDAY_CO_GMAIL_COL", "link_mm5arpv1")

# Operations board columns (ids verified on board 1920364853, 2026-07-16).
OPS_GROUP_TASKS = os.environ.get("GVC_MONDAY_OPS_TASKS_GROUP", "topics")
OPS_COL_STAGE = "status"
OPS_COL_BILLABLE = "color_mm2xd40t"
OPS_COL_START_DATE = "date"
OPS_COL_LINK_TO_PROJECTS = "link_to_projects"
OPS_STAGE_LABEL = "Upcoming"
OPS_BILLABLE_LABEL = "Yes"

# Fallback group when the parent project can't be resolved at create time.
P_GROUP_NEW_PROJECTS = "new_group25317__1"

# CO subitem columns — LEGACY (Subitems of Projects 1918846408, added
# 2026-06-15). Kept for pre-2026-07 COs + the invoice billing writeback.
CO_COL_AMOUNT = "numeric_mm4cmamb"
CO_COL_STATUS = "color_mm4cva36"           # labels: Drafted, Sent, Approved, Billed, Void
CO_COL_ISSUE_DATE = "date_mm4cs2sf"
CO_COL_APPROVED_DATE = "date_mm4c92pe"
CO_COL_PDF_LINK = "link_mm4c8rys"
CO_COL_GMAIL_DRAFT = "link_mm4cwhs8"
CO_COL_SCOPE = "long_text_mm0w7pdx"        # reused "Define Scope of Work"
CO_COL_BILLED_INVOICE = "link_mm4cb2wm"    # "CO Billed Invoice" (added 2026-06-16)

CO_STATUS_DRAFTED = "Drafted"
CO_STATUS_BILLED = "Billed"
CO_STATUS_VOID = "Void"


def _link_url(raw_value: Optional[str]) -> Optional[str]:
    """Pull the .url out of a Monday link column's raw JSON value."""
    if not raw_value:
        return None
    try:
        return (json.loads(raw_value) or {}).get("url") or None
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_value(raw_value: Optional[str]) -> Optional[dict]:
    """Parse a column's raw JSON value into a dict (None on anything else)."""
    if not raw_value:
        return None
    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _item_url(board_id: int, item_id: int) -> str:
    return (f"https://greenvalleycontractors.monday.com/boards/"
            f"{board_id}/pulses/{item_id}")


# ---------------------------------------------------------------------------
# Reads — form prefill + parent context
# ---------------------------------------------------------------------------

def _estimate_number_from_bid(mc, bid_item_id: int) -> Optional[str]:
    """Read the Estimate # off a Bid Board item (the CO base fallback)."""
    query = """
    query ($itemId: [ID!], $cols: [String!]) {
      items(ids: $itemId) { column_values(ids: $cols) { id text } }
    }
    """
    data = mc._query(query, {"itemId": [str(bid_item_id)],
                             "cols": [BID_COL_ESTIMATE_NUMBER]})
    items = data.get("items") or []
    if not items:
        return None
    for cv in items[0].get("column_values") or []:
        if cv["id"] == BID_COL_ESTIMATE_NUMBER:
            return (cv.get("text") or "").strip() or None
    return None


def enrich_project_context(ctx: dict) -> dict:
    """
    PURE: fill gaps on a get_project_context-shaped dict using Projects-board
    fields that often carry the same facts as the linked Customer.

    - client_name empty → builder (GC/homeowner on the project)
    - contact_name empty → supervisor when it looks useful (non-empty text)

    Typed/customer values always win; this never overwrites a populated field.
    """
    out = dict(ctx or {})
    builder = (out.get("builder") or "").strip() or None
    supervisor = (out.get("supervisor") or "").strip() or None
    if builder is not None:
        out["builder"] = builder
    if supervisor is not None:
        out["supervisor"] = supervisor

    client_name = (out.get("client_name") or "").strip() or None
    contact_name = (out.get("contact_name") or "").strip() or None
    out["client_name"] = client_name
    out["contact_name"] = contact_name

    if not client_name and builder:
        out["client_name"] = builder
    if not contact_name and supervisor:
        out["contact_name"] = supervisor
    return out


def get_project_context(mc, item_id: int) -> dict:
    """
    Read a Projects item and assemble what the CO form needs. Returns:
      {monday_item_id, item_url, group_id, job_name, site_address,
       contact_name, project_number, gfolder_url, client_name, client_email,
       client_phone, client_billing_address, builder, supervisor,
       project_type, scope_summary, existing_co_identifiers: [...],
       existing_cos: [{identifier, status, item_id, url}]}
    Raises RuntimeError (via mc._query) only on a hard API error.

    project_number resolution (2026-07-16 fix): the Projects "Project #"
    column (text_mm4fvj91 — `numbers9` was deleted from the board), falling
    back to the Linked Opportunity's Estimate # on the Bid Board. The
    estimate number is the CO base spine, so the fallback is usually the one
    that fires.

    Prefill gaps (builder → client_name, supervisor → contact_name) are
    applied via enrich_project_context so Sales/Ops only types the change.
    """
    query = """
    query ($itemId: [ID!]) {
      items(ids: $itemId) {
        id
        name
        url
        group { id }
        column_values {
          id
          text
          value
          ... on BoardRelationValue { linked_item_ids linked_items { id name } }
        }
        subitems { id name }
      }
    }
    """
    data = mc._query(query, {"itemId": [str(item_id)]})
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"Monday Projects item {item_id} not found.")
    item = items[0]
    cols = {cv["id"]: cv for cv in item.get("column_values") or []}

    def text(cid: str) -> Optional[str]:
        v = (cols.get(cid, {}).get("text") or "").strip()
        return v or None

    # Customer (linked) → name + contact fields from the Customers board.
    client_name = None
    client_email = None
    client_phone = None
    client_billing = None
    contact_name = text(P_COL_CONTACT_NAME)
    cust_col = cols.get(P_COL_CUSTOMER_LINK, {})
    linked_ids = cust_col.get("linked_item_ids") or []
    linked_items = cust_col.get("linked_items") or []
    if linked_items:
        client_name = (linked_items[0].get("name") or "").strip() or None
    if linked_ids:
        try:
            cust = mc.get_customer(int(linked_ids[0]))
            ccols = {cv["id"]: cv for cv in cust.get("column_values") or []}

            def ctext(cid: str) -> Optional[str]:
                v = (ccols.get(cid, {}).get("text") or "").strip()
                return v or None

            client_name = client_name or (cust.get("name") or "").strip() or None
            client_email = ctext(C_COL_EMAIL)
            client_phone = ctext(C_COL_PHONE)
            client_billing = ctext(C_COL_BILLING)
            contact_name = contact_name or ctext(C_COL_CONTACT)
        except Exception as e:  # noqa: BLE001 — customer read is best-effort
            print(f"[monday-co] customer read failed: {e}", file=sys.stderr)

    # Project # → Linked Opportunity Estimate # fallback (the usual path).
    project_number = text(P_COL_PROJECT_NUMBER)
    if not project_number:
        opp_ids = (cols.get(P_COL_LINKED_OPPORTUNITY, {}) or {}).get("linked_item_ids") or []
        if opp_ids:
            try:
                project_number = _estimate_number_from_bid(mc, int(opp_ids[0]))
            except Exception as e:  # noqa: BLE001 — fallback is best-effort
                print(f"[monday-co] opportunity estimate-number read failed: {e}",
                      file=sys.stderr)

    # Existing COs: legacy subitem names + new-model top-level CO items.
    existing = [s.get("name", "") for s in item.get("subitems") or []]
    existing_cos: list[dict] = []
    if project_number:
        try:
            existing_cos = list_co_items(mc, project_number)
            existing.extend(c["identifier"] for c in existing_cos)
        except Exception as e:  # noqa: BLE001 — numbering falls back to Drive/local
            print(f"[monday-co] CO item scan failed: {e}", file=sys.stderr)

    return enrich_project_context({
        "monday_item_id": int(item["id"]),
        "item_url": item.get("url"),
        "group_id": ((item.get("group") or {}).get("id")) or None,
        "job_name": item.get("name"),
        "site_address": text(P_COL_SITE),
        "contact_name": contact_name,
        "project_number": project_number,
        "gfolder_url": _link_url(cols.get(P_COL_GFOLDER, {}).get("value")),
        "client_name": client_name,
        "client_email": client_email,
        "client_phone": client_phone,
        "client_billing_address": client_billing,
        "builder": text(P_COL_BUILDER),
        "supervisor": text(P_COL_SUPERVISOR),
        "project_type": text(P_COL_PROJECT_TYPE),
        "scope_summary": text(P_COL_SCOPE),
        "existing_co_identifiers": existing,
        "existing_cos": existing_cos,
    })


def search_projects(mc, q: str, *, limit: int = 15) -> list[dict]:
    """
    "Find the Project" text search: Projects board by item name OR by the
    Project # column (which also holds CO identifiers, so searching a CO id
    finds its CO item). Two contains_text legs (Monday ANDs rules), deduped,
    capped. Returns [{item_id, name, group, project_number, url}].
    Mirrors monday/estimate.search_bids. Legs run in parallel; results are
    short-TTL cached.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []
    cache_key = f"search:projects:{q.lower()}:{int(limit)}"
    return monday_cache.get_or_set(
        cache_key,
        lambda: _search_projects_uncached(mc, q, limit=limit),
        ttl=monday_cache.search_ttl(),
    )


def _search_projects_uncached(mc, q: str, *, limit: int = 15) -> list[dict]:
    query = """
    query ($boardId: [ID!], $columnId: ID!, $value: CompareValue!) {
      boards(ids: $boardId) {
        items_page(limit: 25, query_params: {
          rules: [{column_id: $columnId, compare_value: $value,
                   operator: contains_text}]}) {
          items {
            id
            name
            group { title }
            column_values(ids: ["%s"]) { id text }
          }
        }
      }
    }
    """ % P_COL_PROJECT_NUMBER

    def _leg(column_id: str):
        # Fresh session per leg — requests.Session is not thread-safe.
        token = None
        try:
            token = mc.session.headers.get("Authorization")
        except Exception:  # noqa: BLE001 — fall back to env-configured client
            token = None
        local = MondayClient(token=token) if token else MondayClient()
        return local._query(query, {
            "boardId": [str(PROJECTS_BOARD_ID)],
            "columnId": column_id,
            "value": q,
        })

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {pool.submit(_leg, col): col
                for col in ("name", P_COL_PROJECT_NUMBER)}
        for fut in as_completed(futs):
            column_id = futs[fut]
            try:
                data = fut.result()
            except Exception as e:  # noqa: BLE001 — a failed leg shouldn't kill the other
                print(f"[monday-co] search leg {column_id!r} failed: {e}", file=sys.stderr)
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
                        "group": ((item.get("group") or {}).get("title") or "").strip(),
                        "project_number": texts.get(P_COL_PROJECT_NUMBER, ""),
                        "url": _item_url(PROJECTS_BOARD_ID, item_id),
                    }
    return list(results.values())[:limit]


def list_co_items(mc, base_number: str) -> list[dict]:
    """
    Top-level CO items for a base (estimate) number: Projects items whose
    Project # column contains `-{base}` and parses as CO.{n}-{base}.
    Returns [{item_id, identifier, status, amount, name, url}].
    """
    from subsystems.change_order.number import parse_co_number

    base = (base_number or "").strip()
    if not base:
        return []
    query = """
    query ($boardId: [ID!], $value: CompareValue!) {
      boards(ids: $boardId) {
        items_page(limit: 50, query_params: {
          rules: [{column_id: "%s", compare_value: $value,
                   operator: contains_text}]}) {
          items {
            id
            name
            column_values(ids: ["%s", "%s", "%s"]) { id text }
          }
        }
      }
    }
    """ % (P_COL_PROJECT_NUMBER, P_COL_PROJECT_NUMBER, CO_ITEM_COL_STATUS,
           CO_ITEM_COL_AMOUNT)
    data = mc._query(query, {"boardId": [str(PROJECTS_BOARD_ID)],
                             "value": f"-{base}"})
    out: list[dict] = []
    for board in data.get("boards") or []:
        for item in (board.get("items_page") or {}).get("items") or []:
            texts = {cv["id"]: (cv.get("text") or "").strip()
                     for cv in item.get("column_values") or []}
            ident = texts.get(P_COL_PROJECT_NUMBER, "")
            parsed = parse_co_number(ident)
            if not parsed or parsed[1] != base:
                continue
            amount = None
            raw_amt = texts.get(CO_ITEM_COL_AMOUNT) or ""
            if raw_amt:
                try:
                    amount = float(raw_amt.replace(",", "").replace("$", ""))
                except ValueError:
                    amount = None
            out.append({
                "item_id": int(item["id"]),
                "identifier": ident,
                "status": texts.get(CO_ITEM_COL_STATUS) or None,
                "amount": amount,
                "name": (item.get("name") or "").strip(),
                "url": _item_url(PROJECTS_BOARD_ID, int(item["id"])),
            })
    out.sort(key=lambda c: c["identifier"])
    return out


def _is_unbilled_co_status(status: Optional[str]) -> bool:
    """True when a top-level CO status is eligible for the invoice picker."""
    label = (status or "").strip().lower()
    if not label:
        return True
    return label not in {CO_STATUS_BILLED.lower(), CO_STATUS_VOID.lower()}


def list_unbilled_co_items(mc, base_number: str) -> list[dict]:
    """
    Top-level CO items for a base number that are not yet Billed (and not Void).
    Shape matches list_co_items, plus co_number (= identifier) for the invoice
    picker / billed_change_orders payload.
    """
    out: list[dict] = []
    for c in list_co_items(mc, base_number):
        if not _is_unbilled_co_status(c.get("status")):
            continue
        row = dict(c)
        row["co_number"] = c.get("identifier")
        out.append(row)
    return out


def find_co_item(mc, co_identifier: str) -> Optional[dict]:
    """
    Find the top-level CO item whose Project # column holds exactly
    `co_identifier`. Returns {item_id, name, group_id, status} or None.
    """
    ident = (co_identifier or "").strip()
    if not ident:
        return None
    query = """
    query ($boardId: [ID!], $value: CompareValue!) {
      boards(ids: $boardId) {
        items_page(limit: 10, query_params: {
          rules: [{column_id: "%s", compare_value: $value,
                   operator: contains_text}]}) {
          items {
            id
            name
            group { id }
            column_values(ids: ["%s", "%s"]) { id text }
          }
        }
      }
    }
    """ % (P_COL_PROJECT_NUMBER, P_COL_PROJECT_NUMBER, CO_ITEM_COL_STATUS)
    data = mc._query(query, {"boardId": [str(PROJECTS_BOARD_ID)], "value": ident})
    for board in data.get("boards") or []:
        for item in (board.get("items_page") or {}).get("items") or []:
            texts = {cv["id"]: (cv.get("text") or "").strip()
                     for cv in item.get("column_values") or []}
            if texts.get(P_COL_PROJECT_NUMBER, "") != ident:
                continue
            return {
                "item_id": int(item["id"]),
                "name": (item.get("name") or "").strip(),
                "group_id": ((item.get("group") or {}).get("id")) or None,
                "status": texts.get(CO_ITEM_COL_STATUS) or None,
            }
    return None


def find_project_by_folder(mc, folder_url_or_id: str) -> Optional[dict]:
    """
    Backup parent resolution: scan the Projects board for the item whose
    "GFolder Link" points at the given Drive folder. Returns the project
    context (via get_project_context) or None. Matches on the folder ID
    substring so URL query-string noise (?usp=sharing) doesn't defeat it.
    """
    from adapters.drive import folder_id_from_url

    target = folder_id_from_url(folder_url_or_id) or (folder_url_or_id or "").strip()
    if not target:
        return None

    query = """
    query ($boardId: [ID!], $cursor: String) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            column_values(ids: ["link_mkwr6ef9"]) { id value text }
          }
        }
      }
    }
    """
    cursor: Optional[str] = None
    while True:
        data = mc._query(query, {"boardId": [str(PROJECTS_BOARD_ID)], "cursor": cursor})
        boards = data.get("boards") or []
        if not boards:
            return None
        page = boards[0]["items_page"]
        for it in page["items"]:
            cv = (it.get("column_values") or [{}])[0]
            url = _link_url(cv.get("value")) or (cv.get("text") or "")
            if target and target in (url or ""):
                return get_project_context(mc, int(it["id"]))
        cursor = page.get("cursor")
        if not cursor:
            return None


# ---------------------------------------------------------------------------
# New-model writes — top-level CO item + Operations task
# ---------------------------------------------------------------------------

def _read_parent_for_copy(mc, parent_item_id: int) -> dict:
    """
    Read the parent Projects item's group + the raw values the CO item copies.
    Returns {name, group_id, customer_ids, opportunity_ids, contact_name,
    location_value, project_type_label, gfolder_url}.
    """
    query = """
    query ($itemId: [ID!], $cols: [String!]) {
      items(ids: $itemId) {
        id
        name
        group { id }
        column_values(ids: $cols) {
          id
          text
          value
          ... on BoardRelationValue { linked_item_ids }
        }
      }
    }
    """
    cols = [P_COL_CUSTOMER_LINK, P_COL_LINKED_OPPORTUNITY, P_COL_CONTACT_NAME,
            P_COL_SITE, P_COL_PROJECT_TYPE, P_COL_GFOLDER]
    data = mc._query(query, {"itemId": [str(parent_item_id)], "cols": cols})
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"Monday Projects item {parent_item_id} not found.")
    item = items[0]
    cmap = {cv["id"]: cv for cv in item.get("column_values") or []}

    def text(cid: str) -> str:
        return (cmap.get(cid, {}).get("text") or "").strip()

    return {
        "name": (item.get("name") or "").strip(),
        "group_id": ((item.get("group") or {}).get("id")) or None,
        "customer_ids": [int(i) for i in (cmap.get(P_COL_CUSTOMER_LINK, {})
                                          .get("linked_item_ids") or [])],
        "opportunity_ids": [int(i) for i in (cmap.get(P_COL_LINKED_OPPORTUNITY, {})
                                             .get("linked_item_ids") or [])],
        "contact_name": text(P_COL_CONTACT_NAME),
        "location_value": _parse_value(cmap.get(P_COL_SITE, {}).get("value")),
        "project_type_label": text(P_COL_PROJECT_TYPE),
        "gfolder_url": _link_url(cmap.get(P_COL_GFOLDER, {}).get("value")),
    }


def co_item_name(parent_name: str, co_identifier: str) -> str:
    """PURE: the CO item/task title — `CO.{n} - {parent title}` (per the original design: the
    exact original title, prefixed). Falls back to the identifier alone."""
    from subsystems.change_order.number import parse_co_number

    parsed = parse_co_number(co_identifier)
    prefix = f"CO.{parsed[0]}" if parsed else (co_identifier or "CO")
    parent_name = (parent_name or "").strip()
    return f"{prefix} - {parent_name}" if parent_name else (co_identifier or prefix)


def build_co_item_values(parent: dict, *, co_identifier: str,
                         amount: Optional[float], issue_date: Optional[str],
                         drive_url: Optional[str], gmail_url: Optional[str],
                         scope_notes: Optional[str],
                         status_label: str = CO_STATUS_DRAFTED) -> dict:
    """
    PURE: the change_multiple_column_values / create_item payload for the
    top-level CO item. `parent` is _read_parent_for_copy's shape (pass {} to
    skip the copied columns). Only non-empty values are included.
    """
    values: dict = {
        P_COL_PROJECT_NUMBER: co_identifier,
        CO_ITEM_COL_STATUS: {"label": status_label},
    }
    if amount is not None:
        values[CO_ITEM_COL_AMOUNT] = str(round(float(amount), 2))
    if drive_url:
        values[CO_ITEM_COL_PDF] = {"url": drive_url, "text": "Change Order PDF"}
    if gmail_url:
        values[CO_ITEM_COL_GMAIL] = {"url": gmail_url, "text": "hello@ draft"}
    if issue_date:
        values["date"] = {"date": issue_date}          # Start Date
    if scope_notes:
        values[P_COL_SCOPE] = scope_notes

    if parent.get("customer_ids"):
        values[P_COL_CUSTOMER_LINK] = {"item_ids": parent["customer_ids"]}
    if parent.get("opportunity_ids"):
        values[P_COL_LINKED_OPPORTUNITY] = {"item_ids": parent["opportunity_ids"]}
    if parent.get("contact_name"):
        values[P_COL_CONTACT_NAME] = parent["contact_name"]
    if parent.get("location_value"):
        values[P_COL_SITE] = parent["location_value"]
    if parent.get("project_type_label"):
        values[P_COL_PROJECT_TYPE] = {"label": parent["project_type_label"]}
    if parent.get("gfolder_url"):
        values[P_COL_GFOLDER] = {"url": parent["gfolder_url"], "text": "GFolder"}
    return values


def build_ops_task_values(*, co_item_id: Optional[int], parent_item_id: Optional[int],
                          issue_date: Optional[str]) -> dict:
    """PURE: the Operations-task column payload. Links BOTH the CO item and
    the parent project so the task lands next to the job."""
    values: dict = {
        OPS_COL_STAGE: {"label": OPS_STAGE_LABEL},
        OPS_COL_BILLABLE: {"label": OPS_BILLABLE_LABEL},
    }
    if issue_date:
        values[OPS_COL_START_DATE] = {"date": issue_date}
    link_ids = [i for i in (co_item_id, parent_item_id) if i]
    if link_ids:
        values[OPS_COL_LINK_TO_PROJECTS] = {"item_ids": link_ids}
    return values


def _create_item(mc, board_id: int, group_id: Optional[str], name: str,
                 values: dict) -> int:
    query = """
    mutation ($boardId: ID!, $groupId: String, $name: String!, $values: JSON!) {
      create_item(board_id: $boardId, group_id: $groupId, item_name: $name,
                  column_values: $values, create_labels_if_missing: true) { id }
    }
    """
    data = mc._query(query, {
        "boardId": str(board_id),
        "groupId": group_id,
        "name": name,
        "values": json.dumps(values),
    })
    return int(data["create_item"]["id"])


def _update_item(mc, board_id: int, item_id: int, values: dict,
                 *, name: Optional[str] = None) -> None:
    if name:
        values = {**values, "name": name}
    if not values:
        return
    query = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(board_id: $boardId, item_id: $itemId,
                                    column_values: $values,
                                    create_labels_if_missing: true) { id }
    }
    """
    mc._query(query, {
        "boardId": str(board_id),
        "itemId": str(item_id),
        "values": json.dumps(values),
    })


def _find_ops_task(mc, name: str) -> Optional[int]:
    """Find an Operations task by exact name (contains_text then exact match)."""
    name = (name or "").strip()
    if not name:
        return None
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
    data = mc._query(query, {"boardId": [str(OPERATIONS_BOARD_ID)], "value": name})
    for board in data.get("boards") or []:
        for item in (board.get("items_page") or {}).get("items") or []:
            if (item.get("name") or "").strip() == name:
                return int(item["id"])
    return None


def write_back(
    *,
    parent_item_id: Optional[int],
    folder_url: Optional[str],
    co_identifier: str,
    amount: Optional[float],
    issue_date: Optional[str],
    drive_url: Optional[str],
    gmail_url: Optional[str],
    scope_notes: Optional[str],
    revise: bool = False,
) -> dict:
    """
    Graceful Monday write for a finalized CO (NEW MODEL, 2026-07-16). NEVER
    raises — returns a report dict the flow merges into its writeback.

    Parent resolution (unchanged): explicit parent_item_id → GFolder match
    from the pasted Drive folder → none found = skip Monday entirely.

    Create-or-update: an existing CO item (Project # == co_identifier) is
    UPDATED in place — this makes finalize retries idempotent and is the
    revision path (CO Status resets → Drafted; a prior Billed status is
    surfaced as monday_billed_warning, never blocked). Otherwise a new item
    is created in the parent's group. The Operations task follows the same
    find-by-name-or-create rule and NEVER fails the project write.
    """
    report: dict = {"monday_synced": False}
    try:
        from adapters.monday.client import MondayClient, MondayNotConfigured
        try:
            mc = MondayClient()
        except MondayNotConfigured as e:
            report["monday_status"] = f"SKIPPED — {e}"
            return report

        resolved = parent_item_id
        if not resolved and folder_url:
            ctx = find_project_by_folder(mc, folder_url)
            if ctx:
                resolved = ctx["monday_item_id"]

        # ---- The top-level CO item on Projects ----
        existing = None
        try:
            existing = find_co_item(mc, co_identifier)
        except Exception as e:  # noqa: BLE001 — a failed lookup falls through to create
            print(f"[monday-co] CO item lookup failed: {e}", file=sys.stderr)

        parent_copy: dict = {}
        if resolved:
            try:
                parent_copy = _read_parent_for_copy(mc, int(resolved))
                report["monday_parent_item_id"] = int(resolved)
            except Exception as e:  # noqa: BLE001 — copy is best-effort
                print(f"[monday-co] parent read failed: {e}", file=sys.stderr)

        if not resolved and not existing:
            report["monday_status"] = (
                "SKIPPED — no parent Project item resolved (no Monday item id, "
                "and no Projects row matched the Drive folder). The CO PDF, "
                "Drive copy, and hello@ draft were still created."
            )
            return report

        if existing and (existing.get("status") or "").strip().lower() == CO_STATUS_BILLED.lower():
            report["monday_billed_warning"] = (
                f"This CO's Monday status was '{existing['status']}' — it has been "
                "billed. The revision updated the board row and reset it to "
                "Drafted, but the invoice was NOT touched; re-bill or correct "
                "the invoice separately if the amount changed."
            )

        # Title resolution: if we have a FRESH parent read, always (re)compute
        # the title from its current name (a revision loaded straight from a
        # CO-id search — no parent resolved — must NOT re-wrap the CO item's
        # OWN existing name through co_item_name() again, which would
        # double-prefix it to "CO.1 - CO.1 - …" and orphan the Ops-task
        # lookup by name). Only rename the CO item when we actually have a
        # fresh parent title to rename it FROM.
        if parent_copy.get("name"):
            name = co_item_name(parent_copy["name"], co_identifier)
            rename_to = name
        elif existing:
            name = existing.get("name") or co_item_name("", co_identifier)
            rename_to = None
        else:
            name = co_item_name("", co_identifier)
            rename_to = None

        values = build_co_item_values(
            parent_copy, co_identifier=co_identifier, amount=amount,
            issue_date=issue_date, drive_url=drive_url, gmail_url=gmail_url,
            scope_notes=scope_notes, status_label=CO_STATUS_DRAFTED,
        )
        if existing:
            _update_item(mc, PROJECTS_BOARD_ID, existing["item_id"], values,
                         name=rename_to)
            co_item_id = existing["item_id"]
            report["monday_co_item_updated"] = True
        else:
            group_id = parent_copy.get("group_id") or P_GROUP_NEW_PROJECTS
            co_item_id = _create_item(mc, PROJECTS_BOARD_ID, group_id, name, values)
            report["monday_co_item_created"] = True
        report["monday_synced"] = True
        report["monday_co_item_id"] = co_item_id
        report["monday_co_item_url"] = _item_url(PROJECTS_BOARD_ID, co_item_id)
        # Back-compat alias used by older UI/result rendering.
        report["monday_item_url"] = report["monday_co_item_url"]

        # ---- The Operations-board task (best-effort; never fails the CO) ----
        try:
            ops_values = build_ops_task_values(
                co_item_id=co_item_id,
                parent_item_id=int(resolved) if resolved else None,
                issue_date=issue_date,
            )
            ops_id = _find_ops_task(mc, name)
            if ops_id:
                _update_item(mc, OPERATIONS_BOARD_ID, ops_id, ops_values)
                report["monday_ops_task_updated"] = True
            else:
                ops_id = _create_item(mc, OPERATIONS_BOARD_ID, OPS_GROUP_TASKS,
                                      name, ops_values)
                report["monday_ops_task_created"] = True
            report["monday_ops_task_id"] = ops_id
            report["monday_ops_task_url"] = _item_url(OPERATIONS_BOARD_ID, ops_id)
        except Exception as e:  # noqa: BLE001 — the CO item write already succeeded
            report["monday_ops_status"] = f"FAILED — {type(e).__name__}: {e}"
            print(f"[monday-co] Operations task write failed: {e}", file=sys.stderr)

        return report
    except Exception as e:  # noqa: BLE001 — a Monday failure never blocks the draft
        report["monday_status"] = f"FAILED — {type(e).__name__}: {e}"
        print(f"[monday-co] write-back failed: {e}", file=sys.stderr)
        return report


# ---------------------------------------------------------------------------
# LEGACY subitem model (pre-2026-07 COs + the invoice CO-billing writeback).
# No longer called at CO create time; kept until CO billing moves to the new
# model (rides the future CO-billing front-end).
# ---------------------------------------------------------------------------

def create_co_subitem(
    mc,
    parent_item_id: int,
    co_identifier: str,
    *,
    amount: Optional[float] = None,
    issue_date: Optional[str] = None,
    drive_url: Optional[str] = None,
    gmail_url: Optional[str] = None,
    scope_notes: Optional[str] = None,
    status: str = CO_STATUS_DRAFTED,
) -> dict:
    """
    LEGACY: create a CO subitem under its parent Project item and set the CO
    columns. Returns the Monday create_subitem payload {id, board:{id}}.
    """
    column_values: dict = {CO_COL_STATUS: {"label": status}}
    if amount is not None:
        column_values[CO_COL_AMOUNT] = str(round(float(amount), 2))
    if issue_date:
        column_values[CO_COL_ISSUE_DATE] = {"date": issue_date}
    if drive_url:
        column_values[CO_COL_PDF_LINK] = {"url": drive_url, "text": "Change Order PDF"}
    if gmail_url:
        column_values[CO_COL_GMAIL_DRAFT] = {"url": gmail_url, "text": "hello@ draft"}
    if scope_notes:
        column_values[CO_COL_SCOPE] = scope_notes

    query = """
    mutation ($parentId: ID!, $name: String!, $values: JSON!) {
      create_subitem(parent_item_id: $parentId, item_name: $name,
                     column_values: $values) { id board { id } }
    }
    """
    data = mc._query(query, {
        "parentId": str(parent_item_id),
        "name": co_identifier,
        "values": json.dumps(column_values),
    })
    return data["create_subitem"]


def _set_subitem_columns(mc, subitem_id: int, values: dict) -> None:
    """change_multiple_column_values on the SUBITEMS board (not Projects)."""
    if not values:
        return
    query = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(board_id: $boardId, item_id: $itemId,
                                    column_values: $values) { id }
    }
    """
    mc._query(query, {
        "boardId": str(SUBITEMS_BOARD_ID),
        "itemId": str(subitem_id),
        "values": json.dumps(values),
    })


def list_billable_cos(mc, parent_item_id: int) -> list[dict]:
    """
    LEGACY: list the CO subitems under a Project, for the invoice-tool CO
    picker. Returns [{subitem_id, co_number, status, amount}] for subitems
    whose name starts with "CO." (and have a CO Status).
    """
    query = """
    query ($itemId: [ID!]) {
      items(ids: $itemId) {
        subitems {
          id
          name
          column_values(ids: ["color_mm4cva36", "numeric_mm4cmamb"]) { id text }
        }
      }
    }
    """
    data = mc._query(query, {"itemId": [str(parent_item_id)]})
    items = data.get("items") or []
    if not items:
        return []
    out: list[dict] = []
    for sub in items[0].get("subitems") or []:
        name = sub.get("name") or ""
        if not name.startswith("CO."):
            continue
        cols = {cv["id"]: (cv.get("text") or "").strip() for cv in sub.get("column_values") or []}
        amount = None
        raw = cols.get(CO_COL_AMOUNT)
        if raw:
            try:
                amount = float(raw.replace(",", "").replace("$", ""))
            except ValueError:
                amount = None
        out.append({
            "subitem_id": int(sub["id"]),
            "co_number": name,
            "status": cols.get(CO_COL_STATUS) or None,
            "amount": amount,
        })
    return out


def mark_billed(mc, subitem_id: int, *, invoice_identifier: str,
                invoice_url: Optional[str] = None) -> None:
    """
    LEGACY: flip one CO subitem to Status=Billed and record the billing
    invoice. Idempotent: re-running writes the same values.
    """
    values: dict = {CO_COL_STATUS: {"label": CO_STATUS_BILLED}}
    if invoice_url:
        values[CO_COL_BILLED_INVOICE] = {"url": invoice_url, "text": invoice_identifier}
    else:
        # No hosted URL (e.g. preflight-only oddity): still record the identifier
        # as link text so the column isn't empty. Monday link cols accept this.
        values[CO_COL_BILLED_INVOICE] = {"url": "", "text": invoice_identifier}
    _set_subitem_columns(mc, subitem_id, values)


def mark_billed_item(mc, item_id: int, *, invoice_identifier: str,
                     invoice_url: Optional[str] = None) -> None:
    """
    NEW MODEL: flip one top-level CO item to Status=Billed.
    Idempotent: re-running writes the same Status=Billed value.
    `invoice_identifier` / `invoice_url` are accepted for call-site parity with
    the legacy helper (and future link-column support); the current top-level
    CO columns only carry Status/Amount/PDF/Gmail, so status is what we write.
    """
    _ = (invoice_identifier, invoice_url)  # reserved for forward-compat / call parity
    _update_item(
        mc,
        PROJECTS_BOARD_ID,
        int(item_id),
        {CO_ITEM_COL_STATUS: {"label": CO_STATUS_BILLED}},
    )


def mark_billed_batch(
    billed_change_orders: list[dict],
    *,
    invoice_identifier: str,
    invoice_url: Optional[str] = None,
) -> dict:
    """
    Graceful batch writeback called from the invoice LIVE step.

    Prefers the NEW top-level CO model when a ref carries `monday_item_id`
    (mark_billed_item). Falls back to LEGACY subitem ids via
    `monday_subitem_id` (mark_billed) for pre-2026-07 COs. NEVER raises;
    returns {co_billed: [...], co_billing_errors: [...]}.
    Idempotent: re-running writes the same Billed values.
    """
    report: dict = {"co_billed": [], "co_billing_errors": []}
    refs = [
        r for r in (billed_change_orders or [])
        if (r or {}).get("monday_item_id") or (r or {}).get("monday_subitem_id")
    ]
    if not refs:
        return report
    try:
        from adapters.monday.client import MondayClient, MondayNotConfigured
        try:
            mc = MondayClient()
        except MondayNotConfigured as e:
            report["co_billing_status"] = f"SKIPPED — {e}"
            return report
        for r in refs:
            item_id = r.get("monday_item_id")
            sub_id = r.get("monday_subitem_id")
            co_no = r.get("co_number") or str(item_id or sub_id)
            try:
                if item_id:
                    mark_billed_item(
                        mc, int(item_id),
                        invoice_identifier=invoice_identifier,
                        invoice_url=invoice_url,
                    )
                else:
                    mark_billed(
                        mc, int(sub_id),
                        invoice_identifier=invoice_identifier,
                        invoice_url=invoice_url,
                    )
                report["co_billed"].append(co_no)
            except Exception as e:  # noqa: BLE001 — one CO failing must not strand the others
                report["co_billing_errors"].append(
                    {"co_number": co_no, "error": f"{type(e).__name__}: {e}"}
                )
                print(f"[monday-co] mark_billed failed for {co_no}: {e}", file=sys.stderr)
        return report
    except Exception as e:  # noqa: BLE001
        report["co_billing_status"] = f"FAILED — {type(e).__name__}: {e}"
        print(f"[monday-co] mark_billed_batch failed: {e}", file=sys.stderr)
        return report
