"""
Multi-field Monday search — find Projects / Bid Board items without a Project #.
=============================================================================
Employees search by builder, supervisor, address/city/state (location5 text),
project name, project #, or estimate #. Each field is its own contains_text
leg (Monday ANDs rules, so OR needs separate calls); legs run in parallel and
merge with a `match_fields` list naming which legs hit.

Mirrors adapters/monday/co.search_projects + estimate.search_bids: ThreadPool
Executor, fresh MondayClient per leg (requests.Session is not thread-safe),
short-TTL cache via adapters.monday.cache.
"""
from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from adapters.monday import cache as monday_cache
from adapters.monday.client import MondayClient, is_auth_failure
from shared.boards import BID_BOARD_ID, PROJECTS_BOARD_ID
from shared.doc_number import search_needles

# ---------------------------------------------------------------------------
# Column IDs (env-overridable; defaults match live boards / JOBSTART contract)
# ---------------------------------------------------------------------------

P_COL_PROJECT_NUMBER = os.environ.get(
    "GVC_MONDAY_PROJECT_NUMBER_COL", "text_mm4fvj91"
)
P_COL_BUILDER = os.environ.get("GVC_MONDAY_PROJECT_BUILDER_COL", "text")
P_COL_SUPERVISOR = os.environ.get("GVC_MONDAY_PROJECT_SUPERVISOR_COL", "text5")
P_COL_LOCATION = os.environ.get("GVC_MONDAY_PROJECT_LOCATION_COL", "location5")
P_COL_INVOICE_STATUS = os.environ.get(
    "GVC_MONDAY_PROJECT_INVOICE_STATUS_COL", "status0"
)

B_COL_ESTIMATE_NUMBER = os.environ.get(
    "GVC_MONDAY_BID_ESTIMATE_NUMBER_COL", "numbers18"
)
B_COL_LOCATION = os.environ.get("GVC_MONDAY_BID_LOCATION_COL", "location5")
B_COL_STAGE = os.environ.get("GVC_MONDAY_BID_STAGE_COL", "deal_stage")
B_COL_CUSTOMER = os.environ.get("GVC_MONDAY_BID_CUSTOMER_COL", "connect_boards5")

# Semantic match_fields labels ↔ Monday column ids (name is the item name).
_PROJECT_LEG_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "name"),
    ("project_number", P_COL_PROJECT_NUMBER),
    ("builder", P_COL_BUILDER),
    ("supervisor", P_COL_SUPERVISOR),
    ("location", P_COL_LOCATION),
)

_BID_LEG_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "name"),
    ("estimate_number", B_COL_ESTIMATE_NUMBER),
    ("location", B_COL_LOCATION),
    ("customer", B_COL_CUSTOMER),
)

_MIN_QUERY_LEN = 2
_DEFAULT_LIMIT = 20
_PER_LEG_PAGE = 25


def _item_url(board_id: int, item_id: int) -> str:
    return (f"https://greenvalleycontractors.monday.com/boards/"
            f"{board_id}/pulses/{item_id}")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def tokenize_query(q: str) -> list[str]:
    """
    Split a search string into distinctive lowercase tokens (len ≥ 2).
    Whitespace / comma / slash / pipe / semicolon are separators — so
    "9761 Gertrude, Cincinnati OH" → ["9761", "gertrude", "cincinnati", "oh"].
    """
    raw = (q or "").strip().lower()
    if not raw:
        return []
    parts = re.split(r"[\s,;/|]+", raw)
    return [p for p in parts if len(p) >= 2]


def score_match(q: str, row: dict, *, id_field: str = "project_number") -> float:
    """
    Rank a search hit. Exact project# / estimate# wins; then exact name;
    then substring / token overlap; more match_fields is a small boost.
    Higher is better.
    """
    q_norm = (q or "").strip().lower()
    if not q_norm:
        return 0.0

    score = 0.0
    id_val = (row.get(id_field) or "").strip().lower()
    name = (row.get("name") or "").strip().lower()
    match_fields = list(row.get("match_fields") or [])

    if id_val:
        if id_val == q_norm:
            score += 1000.0
        elif q_norm in id_val or id_val in q_norm:
            score += 500.0

    if name:
        if name == q_norm:
            score += 200.0
        elif q_norm in name:
            score += 100.0

    # Prefer hits that came from the identifier leg.
    if id_field in match_fields:
        score += 80.0
    score += 10.0 * len(match_fields)

    tokens = tokenize_query(q_norm)
    if tokens:
        haystacks = [
            name,
            id_val,
            (row.get("builder") or "").strip().lower(),
            (row.get("supervisor") or "").strip().lower(),
            (row.get("location") or "").strip().lower(),
            (row.get("customer") or "").strip().lower(),
        ]
        blob = " ".join(h for h in haystacks if h)
        hits = sum(1 for t in tokens if t in blob)
        score += 5.0 * hits

    return score


def _column_texts(item: dict) -> dict[str, str]:
    return {
        cv["id"]: (cv.get("text") or "").strip()
        for cv in (item.get("column_values") or [])
        if cv.get("id")
    }


def shape_project_item(item: dict, match_fields: list[str]) -> dict:
    """Map a Monday Projects item + match_fields into the rich search row."""
    texts = _column_texts(item)
    item_id = int(item["id"])
    return {
        "item_id": item_id,
        "name": (item.get("name") or "").strip(),
        "group": ((item.get("group") or {}).get("title") or "").strip(),
        "project_number": texts.get(P_COL_PROJECT_NUMBER, ""),
        "builder": texts.get(P_COL_BUILDER, ""),
        "supervisor": texts.get(P_COL_SUPERVISOR, ""),
        "location": texts.get(P_COL_LOCATION, ""),
        "invoice_status": texts.get(P_COL_INVOICE_STATUS, ""),
        "url": _item_url(PROJECTS_BOARD_ID, item_id),
        "match_fields": list(match_fields),
    }


def shape_bid_item(item: dict, match_fields: list[str]) -> dict:
    """Map a Monday Bid Board item + match_fields into the rich search row."""
    texts = _column_texts(item)
    item_id = int(item["id"])
    return {
        "item_id": item_id,
        "name": (item.get("name") or "").strip(),
        "estimate_number": texts.get(B_COL_ESTIMATE_NUMBER, ""),
        "stage": texts.get(B_COL_STAGE, ""),
        "location": texts.get(B_COL_LOCATION, ""),
        "customer": texts.get(B_COL_CUSTOMER, ""),
        "url": _item_url(BID_BOARD_ID, item_id),
        "match_fields": list(match_fields),
    }


def merge_leg_hits(
    results: dict[int, dict],
    items: list[dict],
    field_label: str,
    shaper: Callable[[dict, list[str]], dict],
) -> None:
    """
    Fold Monday items from one search leg into `results` (keyed by item_id).
    First sighting shapes the row; later legs append `field_label` to
    match_fields (no dupes).
    """
    for item in items:
        try:
            item_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        existing = results.get(item_id)
        if existing is None:
            results[item_id] = shaper(item, [field_label])
            continue
        fields = existing.setdefault("match_fields", [])
        if field_label not in fields:
            fields.append(field_label)


def rank_and_cap(
    rows: list[dict],
    q: str,
    *,
    id_field: str,
    limit: int,
) -> list[dict]:
    """Sort by score_match (desc), then name, and cap at `limit`."""
    ranked = sorted(
        rows,
        key=lambda r: (-score_match(q, r, id_field=id_field),
                       (r.get("name") or "").lower()),
    )
    return ranked[: max(0, int(limit))]


def _items_from_response(data: Optional[dict]) -> list[dict]:
    out: list[dict] = []
    if not data:
        return out
    for board in data.get("boards") or []:
        page = board.get("items_page") or {}
        for item in page.get("items") or []:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Parallel contains_text legs
# ---------------------------------------------------------------------------

def _client_for_leg(mc) -> MondayClient:
    """Fresh session per leg — requests.Session is not thread-safe."""
    token = None
    try:
        token = mc.session.headers.get("Authorization")
    except Exception:  # noqa: BLE001 — fall back to env-configured client
        token = None
    return MondayClient(token=token) if token else MondayClient()


def _run_legs(
    mc,
    *,
    board_id: int,
    query: str,
    q: str,
    legs: tuple[tuple[str, str], ...],
    shaper: Callable[[dict, list[str]], dict],
    log_prefix: str,
) -> dict[int, dict]:
    """
    Fire each (field_label, column_id) leg in parallel; merge into item_id map.
    One failed leg never kills the others.
    """
    results: dict[int, dict] = {}

    def _leg(column_id: str) -> dict:
        local = _client_for_leg(mc)
        return local._query(query, {
            "boardId": [str(board_id)],
            "columnId": column_id,
            "value": q,
        })

    workers = min(8, max(1, len(legs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_leg, col_id): field
                for field, col_id in legs}
        for fut in as_completed(futs):
            field_label = futs[fut]
            try:
                data = fut.result()
            except Exception as e:  # noqa: BLE001
                if is_auth_failure(e):
                    raise
                print(f"[{log_prefix}] search leg {field_label!r} failed: {e}",
                      file=sys.stderr)
                continue
            merge_leg_hits(results, _items_from_response(data), field_label, shaper)
    return results


# ---------------------------------------------------------------------------
# Projects board
# ---------------------------------------------------------------------------

_PROJECT_COL_IDS_GQL = ", ".join(
    f'"{c}"' for c in (
        P_COL_PROJECT_NUMBER,
        P_COL_BUILDER,
        P_COL_SUPERVISOR,
        P_COL_LOCATION,
        P_COL_INVOICE_STATUS,
    )
)

_PROJECT_SEARCH_QUERY = """
query ($boardId: [ID!], $columnId: ID!, $value: CompareValue!) {
  boards(ids: $boardId) {
    items_page(limit: %d, query_params: {
      rules: [{column_id: $columnId, compare_value: $value,
               operator: contains_text}]}) {
      items {
        id
        name
        group { title }
        column_values(ids: [%s]) { id text }
      }
    }
  }
}
""" % (_PER_LEG_PAGE, _PROJECT_COL_IDS_GQL)


def search_projects_rich(mc, q: str, *, limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """
    Search Projects board across name / project# / builder / supervisor /
    location. Returns list of:
      {item_id, name, group, project_number, builder, supervisor, location,
       invoice_status, url, match_fields: [str]}
    """
    q = (q or "").strip()
    if len(q) < _MIN_QUERY_LEN:
        return []
    lim = max(1, int(limit))
    cache_key = f"search:projects_rich:{q.lower()}:{lim}"
    return monday_cache.get_or_set(
        cache_key,
        lambda: _search_projects_rich_uncached(mc, q, limit=lim),
        ttl=monday_cache.search_ttl(),
    )


def _search_projects_rich_uncached(
    mc, q: str, *, limit: int = _DEFAULT_LIMIT
) -> list[dict]:
    merged = _run_legs(
        mc,
        board_id=PROJECTS_BOARD_ID,
        query=_PROJECT_SEARCH_QUERY,
        q=q,
        legs=_PROJECT_LEG_FIELDS,
        shaper=shape_project_item,
        log_prefix="monday-search",
    )
    # Prefixed EST-/PRO-/INV- paste: also probe Project # with the bare core
    # (and the other prefixed forms) so PRO-… cells still match.
    for needle in search_needles(q)[1:]:
        extra = _run_legs(
            mc,
            board_id=PROJECTS_BOARD_ID,
            query=_PROJECT_SEARCH_QUERY,
            q=needle,
            legs=(("project_number", P_COL_PROJECT_NUMBER),),
            shaper=shape_project_item,
            log_prefix="monday-search",
        )
        for item_id, row in extra.items():
            if item_id in merged:
                fields = list(merged[item_id].get("match_fields") or [])
                for f in row.get("match_fields") or []:
                    if f not in fields:
                        fields.append(f)
                merged[item_id]["match_fields"] = fields
            else:
                merged[item_id] = row
    return rank_and_cap(
        list(merged.values()), q, id_field="project_number", limit=limit
    )


# ---------------------------------------------------------------------------
# Bid Board
# ---------------------------------------------------------------------------

_BID_COL_IDS_GQL = ", ".join(
    f'"{c}"' for c in (
        B_COL_ESTIMATE_NUMBER,
        B_COL_STAGE,
        B_COL_LOCATION,
        B_COL_CUSTOMER,
    )
)

_BID_SEARCH_QUERY = """
query ($boardId: [ID!], $columnId: ID!, $value: CompareValue!) {
  boards(ids: $boardId) {
    items_page(limit: %d, query_params: {
      rules: [{column_id: $columnId, compare_value: $value,
               operator: contains_text}]}) {
      items {
        id
        name
        column_values(ids: [%s]) { id text }
      }
    }
  }
}
""" % (_PER_LEG_PAGE, _BID_COL_IDS_GQL)


def search_bids_rich(mc, q: str, *, limit: int = _DEFAULT_LIMIT) -> list[dict]:
    """
    Search Bid Board across name / estimate# / location / customer relation
    text. Returns list of:
      {item_id, name, estimate_number, stage, location, customer, url,
       match_fields: [str]}
    """
    q = (q or "").strip()
    if len(q) < _MIN_QUERY_LEN:
        return []
    lim = max(1, int(limit))
    cache_key = f"search:bids_rich:{q.lower()}:{lim}"
    return monday_cache.get_or_set(
        cache_key,
        lambda: _search_bids_rich_uncached(mc, q, limit=lim),
        ttl=monday_cache.search_ttl(),
    )


def _search_bids_rich_uncached(
    mc, q: str, *, limit: int = _DEFAULT_LIMIT
) -> list[dict]:
    merged = _run_legs(
        mc,
        board_id=BID_BOARD_ID,
        query=_BID_SEARCH_QUERY,
        q=q,
        legs=_BID_LEG_FIELDS,
        shaper=shape_bid_item,
        log_prefix="monday-search",
    )
    # Bid Board Estimate # stores bare core — strip EST-/PRO-/INV- for a
    # second probe so pasted outbound ids still hit.
    for needle in search_needles(q)[1:]:
        extra = _run_legs(
            mc,
            board_id=BID_BOARD_ID,
            query=_BID_SEARCH_QUERY,
            q=needle,
            legs=(("estimate_number", B_COL_ESTIMATE_NUMBER),),
            shaper=shape_bid_item,
            log_prefix="monday-search",
        )
        for item_id, row in extra.items():
            if item_id in merged:
                fields = list(merged[item_id].get("match_fields") or [])
                for f in row.get("match_fields") or []:
                    if f not in fields:
                        fields.append(f)
                merged[item_id]["match_fields"] = fields
            else:
                merged[item_id] = row
    return rank_and_cap(
        list(merged.values()), q, id_field="estimate_number", limit=limit
    )
