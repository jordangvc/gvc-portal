"""
Monday reads + writes for the Job Start handoff (Sales → Operations).
=========================================================================
The portal's second write surface into Monday (docs/portal-job-start-design.md,
2026-07-29) and the first that CREATES items. Where Job Check updates columns on
one existing item, Job Start is the sanctioned path a won bid becomes an
operations job:

  fetch_bids()           — live Bid Board rows, OPEN and accepted, paged, each
                           carrying its stage plus the handoff-state flags the
                           picker needs (does a Projects item already exist? an
                           Operations one?). Was fetch_accepted_bids(), which
                           hard-filtered to Stage = Accepted and so hid a bid
                           until somebody flipped the stage in Monday by hand.
  mark_bid_accepted()    — write Stage = Accepted + move to Won Deals, so that
                           flip happens from inside Job Start.
  get_bid_detail()       — ONE bid's full prefill payload: the read-only context
                           header plus every JOBSTART_FIELDS prefill source.
  get_field_labels()     — live status-label sets for the packet's status
                           fields, so the form renders real chips and the
                           validator can reject an invented label.
  hand_off()             — THE write: adopt-or-create the Projects item,
                           adopt-or-create the Operations item, stamp the bid.

ADOPT-OR-CREATE is the load-bearing property. The legacy Bid Board automation
(workflow 1939926355) still fires on Accepted and creates a Projects item; this
module must never race it into a duplicate. Resolution order, per board:
  Projects    — the bid's existing `connect_boards4` link → match by name → create
  Operations  — match by name (the _find_ops_task pattern from monday/co.py) → create
A second handoff of the same bid therefore UPDATES both items in place.

Guardrail: this module trusts orchestrators/jobstart_flow to have validated the
packet against shared/boards.JOBSTART_FIELDS (required-field gate + hard
exclusions), but it never invents a Customers record and never writes a column
the caller didn't hand it.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

from shared import boards
from shared.boards import (
    BID_BOARD_ID,
    OPERATIONS_BOARD_ID,
    PROJECTS_BOARD_ID,
)

# Optional: Monday list cache may land from a parallel change-set. Job Start
# must still import and serve open bids if that module isn't present yet.
try:
    from adapters.monday import cache as monday_cache
except ImportError:  # pragma: no cover — cold path when cache.py isn't shipped
    class _NoMondayCache:
        @staticmethod
        def list_ttl() -> float:
            return 0.0

        @staticmethod
        def get_or_set(key, factory, *, ttl=None):
            return factory()

        @staticmethod
        def get_or_set_swr(key, factory, *, ttl=None, stale_ttl=None):
            return factory()

        @staticmethod
        def stale_ttl() -> float:
            return 0.0

        @staticmethod
        def invalidate(*_keys) -> None:
            return None

        @staticmethod
        def refresh(key, factory, *, ttl=None, stale_ttl=None):
            return factory()

    monday_cache = _NoMondayCache()  # type: ignore[assignment]

# Mirrors/relations return text = NULL; their readable value is display_value
# (same finding as adapters/monday/jobcheck.py, verified 2026-07-28). `value`
# carries the raw JSON we need to copy a location column across verbatim.
_VALUE_FRAGMENT = """
          id
          text
          value
          ... on MirrorValue { display_value }
          ... on BoardRelationValue { display_value }
"""


def _item_url(board_id: int, item_id) -> str:
    return (f"https://greenvalleycontractors.monday.com/boards/"
            f"{board_id}/pulses/{item_id}")


def _column_text(cv: dict) -> Optional[str]:
    """Readable value of one column_value: display_value (mirrors/relations)
    falling back to text. None when empty."""
    for key in ("display_value", "text"):
        raw = cv.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _linked_ids(cv: Optional[dict]) -> list[int]:
    """Item ids out of a board_relation column's raw JSON value."""
    if not cv:
        return []
    try:
        parsed = json.loads(cv.get("value") or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for entry in parsed.get("linkedPulseIds") or []:
        pid = entry.get("linkedPulseId")
        if pid:
            out.append(int(pid))
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _picker_read_columns() -> list[str]:
    """Slim Bid Board projection for the picker list only (not detail/prefill)."""
    return [
        boards.JOBSTART_BID_STAGE_COL,
        boards.JOBSTART_BID_ACCEPTED_DATE_COL,
        boards.JOBSTART_BID_PROJECT_LINK_COL,
        boards.JOBSTART_BID_OPS_LINK_COL,
        boards.JOBSTART_BID_LOCATION_COL,
        boards.JOBSTART_BID_ESTIMATE_NUM_COL,
        boards.JOBSTART_BID_ESTIMATE_TOTAL_COL,
    ]


def _bid_read_columns() -> list[str]:
    """Every Bid Board column the detail/prefill path needs, deduped."""
    ids = list(_picker_read_columns())
    ids += [
        boards.JOBSTART_BID_CUSTOMER_COL,
        boards.JOBSTART_BID_SERVICES_COL,
        boards.JOBSTART_BID_ESTIMATE_PDF_COL,
    ]
    ids += [f["prefill"] for f in boards.JOBSTART_FIELDS if f.get("prefill")]
    return list(dict.fromkeys([i for i in ids if i]))


def fetch_bids(mc) -> list[dict]:
    """
    Every LIVE Bid Board row — open AND accepted — normalized for the picker:
      {item_id, name, url, stage, stage_state, group_id, group_title,
       estimate_number, estimate_total, location, accepted_date,
       has_project, has_ops, group_drift}
    Paged at 200, read-only. `has_project`/`has_ops` drive the "already handed
    off" badge — they are the honest state, not an assumption.

    ⚠ This used to filter hard to Stage = Accepted, which meant a bid was
    INVISIBLE to Job Start until somebody went to Monday and flipped the stage
    by hand — backwards for a tool that is meant to be where Sales works
    (Jordan, 2026-07-29: "this should have access to the open bids, not just the
    accepted bids"). Job Start now shows open bids and can accept one in place.

    Dead bids are excluded by STAGE, never by group, because stage and group
    have already drifted apart in the live data: the Bryant/Jent bid is stage
    Accepted while sitting in "Open Deals", and two accepted bids sit in the
    Lost Deals group. Both are maintained by hand in two places, and the stage
    is the one this tool keys off — so the stage decides, and a disagreement is
    surfaced as `group_drift` rather than quietly hiding a won job.

    Picker uses a slim column projection + short-TTL cache so search/reload
    isn't re-paying a full-board Monday walk every keystroke/page open.
    """
    return monday_cache.get_or_set_swr(
        "list:jobstart:bids",
        lambda: _fetch_bids_uncached(mc),
        ttl=monday_cache.list_ttl(),
        stale_ttl=monday_cache.stale_ttl(),
    )


def _fetch_bids_uncached(mc) -> list[dict]:
    col_ids = json.dumps(_picker_read_columns())
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
        data = mc._query(query, {"boardId": [str(BID_BOARD_ID)], "cursor": cursor})
        board_list = data.get("boards") or []
        if not board_list:
            break
        page = board_list[0]["items_page"]
        for item in page.get("items") or []:
            row = _normalize_bid(item)
            if row is not None:
                rows.append(row)
        cursor = page.get("cursor")
        if not cursor:
            break
    return rows


def stage_state(stage: Optional[str]) -> str:
    """
    PURE. One Bid Board stage label → how Job Start treats it.
      "accepted" — won; ready to hand off
      "dead"     — lost or cancelled; keep it out of the picker entirely
      "open"     — anything else, including a blank stage: still sellable, and
                   Sales can now accept it from inside Job Start
    Matched on substrings rather than an exact label list on purpose: the Bid
    Board's stage labels are edited in Monday by hand, and a picker that hides
    every bid whose label it doesn't recognise is worse than one that shows a
    stale label.
    """
    text = (stage or "").strip().lower()
    if text == boards.JOBSTART_ACCEPTED_STAGE.strip().lower():
        return "accepted"
    if any(word in text for word in boards.JOBSTART_DEAD_STAGE_WORDS):
        return "dead"
    return "open"


def _normalize_bid(item: dict) -> Optional[dict]:
    """One raw Bid Board item → picker row, or None when the bid is dead."""
    cvs = {cv["id"]: cv for cv in item.get("column_values") or []}
    stage = _column_text(cvs.get(boards.JOBSTART_BID_STAGE_COL) or {})
    state = stage_state(stage)
    if state == "dead":
        return None
    group = item.get("group") or {}
    group_id = group.get("id")
    return {
        "item_id": int(item["id"]),
        "name": (item.get("name") or "").strip(),
        "url": _item_url(BID_BOARD_ID, item["id"]),
        "stage": stage,
        "stage_state": state,
        "group_id": group_id,
        # Stage says won, the board says lost/still-open — worth showing rather
        # than resolving silently. Two hand-maintained fields, one job.
        "group_drift": bool(
            state == "accepted" and group_id
            and group_id != boards.JOBSTART_BID_WON_GROUP),
        "group_title": group.get("title"),
        "estimate_number": _column_text(cvs.get(boards.JOBSTART_BID_ESTIMATE_NUM_COL) or {}),
        "estimate_total": _column_text(cvs.get(boards.JOBSTART_BID_ESTIMATE_TOTAL_COL) or {}),
        "location": _column_text(cvs.get(boards.JOBSTART_BID_LOCATION_COL) or {}),
        "accepted_date": _column_text(cvs.get(boards.JOBSTART_BID_ACCEPTED_DATE_COL) or {}),
        "has_project": bool(_linked_ids(cvs.get(boards.JOBSTART_BID_PROJECT_LINK_COL))),
        "has_ops": bool(_linked_ids(cvs.get(boards.JOBSTART_BID_OPS_LINK_COL))),
    }


def get_bid_detail(mc, item_id: int) -> Optional[dict]:
    """
    ONE bid: the read-only context header, the prefill values keyed by
    JOBSTART_FIELDS key, and the raw location/customer values the flow copies
    across verbatim. None when the item doesn't exist.
    """
    query = """
    query ($itemId: [ID!], $cols: [String!]) {
      items(ids: $itemId) {
        id
        name
        group { id title }
        column_values(ids: $cols) { %s }
      }
    }
    """ % _VALUE_FRAGMENT
    data = mc._query(query, {"itemId": [str(item_id)],
                             "cols": _bid_read_columns()})
    items = data.get("items") or []
    if not items:
        return None
    item = items[0]
    cvs = {cv["id"]: cv for cv in item.get("column_values") or []}

    prefill: dict[str, str] = {}
    for field in boards.JOBSTART_FIELDS:
        src = field.get("prefill")
        if not src:
            continue
        text = _column_text(cvs.get(src) or {})
        if text:
            prefill[field["key"]] = text

    stage = _column_text(cvs.get(boards.JOBSTART_BID_STAGE_COL) or {})
    return {
        "item_id": int(item["id"]),
        "name": (item.get("name") or "").strip(),
        "url": _item_url(BID_BOARD_ID, item["id"]),
        "group_id": (item.get("group") or {}).get("id"),
        "group_title": (item.get("group") or {}).get("title"),
        # Carried so send_to_ops can mark the bid accepted idempotently, and so
        # the form can warn Sales that sending will also flip the stage.
        "stage": stage,
        "stage_state": stage_state(stage),
        "context": {
            "estimate_number": _column_text(cvs.get(boards.JOBSTART_BID_ESTIMATE_NUM_COL) or {}),
            "estimate_total": _column_text(cvs.get(boards.JOBSTART_BID_ESTIMATE_TOTAL_COL) or {}),
            "customer": _column_text(cvs.get(boards.JOBSTART_BID_CUSTOMER_COL) or {}),
            "location": _column_text(cvs.get(boards.JOBSTART_BID_LOCATION_COL) or {}),
            "services": _column_text(cvs.get(boards.JOBSTART_BID_SERVICES_COL) or {}),
            "accepted_date": _column_text(cvs.get(boards.JOBSTART_BID_ACCEPTED_DATE_COL) or {}),
        },
        "prefill": prefill,
        # Copied across verbatim by the flow — never retyped by a human.
        "copy": {
            "location_raw": (cvs.get(boards.JOBSTART_BID_LOCATION_COL) or {}).get("value"),
            "customer_ids": _linked_ids(cvs.get(boards.JOBSTART_BID_CUSTOMER_COL)),
        },
        "existing_project_ids": _linked_ids(cvs.get(boards.JOBSTART_BID_PROJECT_LINK_COL)),
        "existing_ops_ids": _linked_ids(cvs.get(boards.JOBSTART_BID_OPS_LINK_COL)),
    }


def fetch_item_updates(mc, item_ids: list, *, limit: int = 25) -> list[str]:
    """
    Update bodies for the given Projects/Operations items, newest first.

    This is what keeps a handoff CURRENT instead of a snapshot: Jake writes
    "lock box is 4417" as a Project-board update, and the packet picks it up
    without anyone re-keying it (Jordan, 2026-07-29 — "it will check the updates
    there and then keep the handoff up to date essentially"). Read-only.
    """
    if not item_ids:
        return []
    query = """
    query ($itemIds: [ID!], $limit: Int!) {
      items(ids: $itemIds) {
        id
        updates(limit: $limit) { id body created_at }
      }
    }
    """
    data = mc._query(query, {"itemIds": [str(int(i)) for i in item_ids],
                             "limit": int(limit)})
    rows: list[tuple[str, str]] = []
    for item in data.get("items") or []:
        for upd in item.get("updates") or []:
            body = (upd.get("body") or "").strip()
            if body:
                rows.append((upd.get("created_at") or "", body))
    # Newest first so the freshest value wins in ingest.from_updates().
    rows.sort(key=lambda r: r[0], reverse=True)
    return [body for _, body in rows]


def get_field_labels(mc) -> dict[str, list[dict]]:
    """
    Live status-label sets for every status field in JOBSTART_FIELDS, keyed by
    field key: {field_key: [{label, hex}, ...]} in board display order.
    Fetched live so the form can't offer — and the validator can't accept — a
    label the board doesn't actually have.
    """
    from adapters.monday.jobcheck import parse_status_labels

    wanted: dict[str, list[str]] = {"projects": [], "operations": []}
    key_of: dict[tuple[str, str], str] = {}
    for field in boards.JOBSTART_FIELDS:
        if field["type"] != "status":
            continue
        for board_name, col_id in field["targets"]:
            wanted.setdefault(board_name, []).append(col_id)
            key_of[(board_name, col_id)] = field["key"]

    query = """
    query ($boardId: [ID!], $cols: [String!]) {
      boards(ids: $boardId) {
        columns(ids: $cols) { id title type settings_str }
      }
    }
    """
    out: dict[str, list[dict]] = {}
    for board_name, col_ids in wanted.items():
        if not col_ids:
            continue
        board_id = (PROJECTS_BOARD_ID if board_name == "projects"
                    else OPERATIONS_BOARD_ID)
        data = mc._query(query, {"boardId": [str(board_id)],
                                 "cols": list(dict.fromkeys(col_ids))})
        for board in data.get("boards") or []:
            for col in board.get("columns") or []:
                key = key_of.get((board_name, col["id"]))
                if key and col.get("type") == "status":
                    out[key] = parse_status_labels(col.get("settings_str"))
    return out


# ---------------------------------------------------------------------------
# Writes — adopt-or-create, never duplicate
# ---------------------------------------------------------------------------

_CREATE = """
mutation ($boardId: ID!, $groupId: String, $name: String!, $values: JSON!) {
  create_item(board_id: $boardId, group_id: $groupId, item_name: $name,
              column_values: $values, create_labels_if_missing: true) { id }
}
"""

_UPDATE = """
mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
  change_multiple_column_values(board_id: $boardId, item_id: $itemId,
                                column_values: $values,
                                create_labels_if_missing: true) { id }
}
"""


def _create_item(mc, board_id: int, group_id: Optional[str], name: str,
                 values: dict) -> int:
    data = mc._query(_CREATE, {
        "boardId": str(board_id), "groupId": group_id,
        "name": name, "values": json.dumps(values),
    })
    return int(data["create_item"]["id"])


def _update_item(mc, board_id: int, item_id: int, values: dict) -> None:
    if not values:
        return
    mc._query(_UPDATE, {
        "boardId": str(board_id), "itemId": str(int(item_id)),
        "values": json.dumps(values),
    })


# Columns Monday's API is known to reject on write. From Jake's Estimating
# Pipelines Reference ("Make Opp"): Job Location `location5` is "API-blocked;
# use text23 as address-text workaround, flag for manual map pin", and the
# customer link `connect_boards5` is "API-blocked, attempt anyway, flag for
# manual entry".
#
# Everything below goes out in ONE change_multiple_column_values mutation, so a
# single rejected column would fail the entire handoff. These are therefore
# attempted, and on failure dropped and retried — the job still lands, and the
# caller surfaces "set these by hand" instead of losing the write.
FRAGILE_COLUMNS = frozenset({"location5", "connect_boards9", "connect_boards5"})

# Relation / link columns always write on adopt — connecting the boards IS the
# handoff's job. Everything else must not silently overwrite a filled Monday
# cell that disagrees with the packet (master-plan: flag conflicts).
ALWAYS_WRITE_COLUMNS = frozenset({
    boards.JOBSTART_P_COL_OPPORTUNITY,
    boards.JOBSTART_P_COL_CUSTOMER,
    boards.JOBSTART_OPS_COL_LINK_PROJECTS,
    boards.JOBSTART_OPS_COL_LINK_OPPORTUNITY,
    boards.JOBSTART_BID_PROJECT_LINK_COL,
    boards.JOBSTART_BID_OPS_LINK_COL,
    boards.JOBSTART_BID_ACCEPTED_DATE_COL,
})


def _proposed_display(value: Any) -> str:
    """Human-readable form of a Monday write payload, for conflict compare."""
    if value is None:
        return ""
    if isinstance(value, dict):
        if "item_ids" in value:
            return ""  # relations compared by always-write, not text
        if "label" in value:
            return str(value.get("label") or "").strip()
        if "date" in value:
            return str(value.get("date") or "").strip()
        if "url" in value:
            return str(value.get("url") or value.get("text") or "").strip()
        if "text" in value:
            return str(value.get("text") or "").strip()
        # number columns often ship as {"number": "340"} or bare stringified
        if "number" in value:
            return str(value.get("number") or "").strip()
        return ""
    return str(value).strip()


def _norm_compare(a: str, b: str) -> bool:
    """True when two column displays mean the same thing for conflict checks."""
    left = (a or "").strip()
    right = (b or "").strip()
    if not left and not right:
        return True
    if left.casefold() == right.casefold():
        return True
    # Numbers: "340" vs "340.0"
    try:
        if float(left) == float(right):
            return True
    except (TypeError, ValueError):
        pass
    # Dates: Monday text is often YYYY-MM-DD already; also tolerate ISO prefixes
    if len(left) >= 10 and len(right) >= 10 and left[:10] == right[:10]:
        return True
    return False


def filter_conflicting_writes(
    proposed: dict,
    existing_text: dict,
    *,
    always_write: Optional[frozenset] = None,
) -> tuple[dict, list[dict]]:
    """
    PURE. On adopt, never silently overwrite a filled Monday column that
    disagrees with the packet.

      empty existing  → write (fill the gap)
      matching value  → skip (no-op)
      differing value → conflict: leave Monday alone, report it

    Relation/link columns in `always_write` always pass through.
    Returns (safe_writes, conflicts) where each conflict is
    {column_id, existing, proposed}.
    """
    always = always_write if always_write is not None else ALWAYS_WRITE_COLUMNS
    safe: dict = {}
    conflicts: list[dict] = []
    for col_id, payload in (proposed or {}).items():
        if col_id in always:
            safe[col_id] = payload
            continue
        existing = str((existing_text or {}).get(col_id) or "").strip()
        want = _proposed_display(payload)
        if not existing:
            safe[col_id] = payload
            continue
        if not want or _norm_compare(existing, want):
            continue
        conflicts.append({
            "column_id": col_id,
            "existing": existing,
            "proposed": want,
        })
    return safe, conflicts


def fetch_item_column_texts(mc, item_id: int, column_ids: list[str]) -> dict[str, str]:
    """Readable text for the given columns on one item. Missing/empty → omitted."""
    cols = [c for c in dict.fromkeys(column_ids or []) if c]
    if not cols or not item_id:
        return {}
    query = """
    query ($itemId: [ID!], $cols: [String!]) {
      items(ids: $itemId) {
        column_values(ids: $cols) { %s }
      }
    }
    """ % _VALUE_FRAGMENT
    data = mc._query(query, {"itemId": [str(int(item_id))], "cols": cols})
    items = data.get("items") or []
    if not items:
        return {}
    out: dict[str, str] = {}
    for cv in items[0].get("column_values") or []:
        text = _column_text(cv)
        if text:
            out[cv["id"]] = text
    return out


def _write_with_fallback(mc, board_id: int, group_id, name: str, values: dict,
                         *, item_id: Optional[int] = None) -> tuple[int, list[str]]:
    """
    Create-or-update, retrying without the known-fragile columns if the first
    attempt is rejected. Returns (item_id, dropped_column_ids).
    """
    def _go(payload: dict) -> int:
        if item_id:
            _update_item(mc, board_id, item_id, payload)
            return int(item_id)
        return _create_item(mc, board_id, group_id, name, payload)

    try:
        return _go(values), []
    except Exception as first_err:  # noqa: BLE001 — retry without the fragile bits
        fragile = [c for c in values if c in FRAGILE_COLUMNS]
        if not fragile:
            raise
        reduced = {k: v for k, v in values.items() if k not in FRAGILE_COLUMNS}
        print(f"[jobstart] write rejected on board {board_id} "
              f"({type(first_err).__name__}); retrying without {fragile}",
              file=sys.stderr)
        return _go(reduced), fragile


_MOVE_GROUP = """
mutation ($itemId: ID!, $groupId: String!) {
  move_item_to_group(item_id: $itemId, group_id: $groupId) { id }
}
"""


def mark_bid_accepted(mc, bid_id: int, *, current_stage: Optional[str] = None,
                      current_group: Optional[str] = None,
                      current_accepted_date: Optional[str] = None,
                      accepted_date: Optional[str] = None) -> dict:
    """
    Set the bid's Stage to Accepted, stamp Accepted Date when blank, and move
    it into the Won Deals group, so Sales never has to leave Job Start to flip
    a stage by hand.

    Returns {stage_written, date_written, group_moved, errors} — a report, never
    an exception. The three halves are INDEPENDENT and IDEMPOTENT: pass the
    values already read from the board and each is skipped when it's already
    right, so re-sending a packet doesn't rewrite a stage somebody else set or
    clobber an Accepted Date a human typed.

    Accepted Date (`date6`) was null on every won deal before the portal — the
    legacy automation never wrote it. Stamping it here (when Sales marks the
    bid won) is the truthful moment; hand_off() only fills the date if it is
    still blank, so an earlier stamp survives ops acceptance days later.

    SAFETY NOTE (checked against the live board 2026-07-30 before this was
    written, per the standing "confirm, don't assume" rule): no ACTIVE Bid Board
    automation triggers on Stage → Accepted. The two workflows that did —
    1939926355 and 1939926362, the one that created a Projects item and posted
    the misleading "and Operations Dashboard" Slack line — are both is_active
    false. The only live deal_stage triggers are "Cleanup for Lost Bids" and the
    notice pointing at the pre-portal Workforms Job Start form, neither of which
    fires on Accepted. If someone re-enables 1939926362, this write will start
    racing it — and adopt-or-create in hand_off() is what absorbs that.
    """
    from datetime import date as _date

    report: dict = {"stage_written": False, "date_written": False,
                    "group_moved": False, "errors": []}
    bid_id = int(bid_id)

    already_accepted = ((current_stage or "").strip().lower()
                        == boards.JOBSTART_ACCEPTED_STAGE.strip().lower())
    # Fill-if-empty. When the caller omits current_accepted_date: assume blank
    # only while flipping an open bid (Accepted Date is blank in practice on
    # those rows). When stage is already Accepted and no date was passed, leave
    # the column alone — preserves idempotent re-sends that don't re-read it.
    if current_accepted_date is None:
        date_empty = not already_accepted
    else:
        date_empty = not str(current_accepted_date).strip()
    today = (accepted_date or _date.today().isoformat()).strip()[:10]

    values: dict = {}
    if not already_accepted:
        values[boards.JOBSTART_BID_STAGE_COL] = {
            "label": boards.JOBSTART_ACCEPTED_STAGE}
    if date_empty and today:
        values[boards.JOBSTART_BID_ACCEPTED_DATE_COL] = {"date": today}

    if values:
        try:
            _update_item(mc, BID_BOARD_ID, bid_id, values)
            if boards.JOBSTART_BID_STAGE_COL in values:
                report["stage_written"] = True
            if boards.JOBSTART_BID_ACCEPTED_DATE_COL in values:
                report["date_written"] = True
        except Exception as e:  # noqa: BLE001 — never lose the packet over this
            report["errors"].append(f"stage: {type(e).__name__}: {e}")

    won = boards.JOBSTART_BID_WON_GROUP
    if won and (current_group or "") != won:
        try:
            mc._query(_MOVE_GROUP, {"itemId": str(bid_id), "groupId": won})
            report["group_moved"] = True
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"group: {type(e).__name__}: {e}")

    if report["stage_written"] or report["group_moved"] or report["date_written"]:
        monday_cache.invalidate("list:jobstart:bids")
    return report


def find_item_by_name(mc, board_id: int, name: str) -> Optional[int]:
    """
    Find the existing item for this job — ACROSS naming conventions.

    Jordan adopted Jake's pipe standard ("9761 Gertrude | Jent Construction") on
    2026-07-29, but every item created before that is named some older way
    ("9761 Gertrude Lane, Cincinnati OH 45231 - Bryant - Jent Construction - New
    House", "…_Warwick_Commercial"). An exact-name-only lookup would miss those
    and CREATE A DUPLICATE — the exact failure mode Joe's copy-pasted automation
    caused and that Jordan asked to be safeguarded against.

    So: search Monday on the most distinctive token (the street number, when
    there is one), then let subsystems/jobstart/naming.best_match pick the same
    job by token similarity. Exact matches always win; an AMBIGUOUS match returns
    None, because adopting the wrong job is worse than creating a new item.
    """
    from subsystems.jobstart import naming

    name = (name or "").strip()
    if not name:
        return None

    # Monday's contains_text can't do fuzzy, so query on the strongest single
    # token and score the candidates locally. A street number is near-unique per
    # job; failing that, the longest word.
    toks = naming.tokens(name)
    nums = sorted((t for t in toks if t.isdigit()), key=len, reverse=True)
    probe = nums[0] if nums else (max(toks, key=len) if toks else name)

    query = """
    query ($boardId: [ID!], $value: CompareValue!) {
      boards(ids: $boardId) {
        items_page(limit: 50, query_params: {
          rules: [{column_id: "name", compare_value: $value,
                   operator: contains_text}]}) {
          items { id name }
        }
      }
    }
    """
    candidates: list[dict] = []
    seen: set[int] = set()
    for value in dict.fromkeys([probe, name]):
        try:
            data = mc._query(query, {"boardId": [str(board_id)], "value": value})
        except Exception as e:  # noqa: BLE001 — a failed probe just narrows us
            print(f"[jobstart] name probe {value!r} failed: {e}", file=sys.stderr)
            continue
        for board in data.get("boards") or []:
            for item in (board.get("items_page") or {}).get("items") or []:
                iid = int(item["id"])
                if iid not in seen:
                    seen.add(iid)
                    candidates.append({"id": iid, "name": item.get("name") or ""})

    hit = naming.best_match(name, candidates)
    if hit:
        if hit.get("how") != "exact":
            print(f"[jobstart] adopted '{hit['name']}' for '{name}' "
                  f"(score {hit['score']:.2f}) — naming-convention change",
                  file=sys.stderr)
        return int(hit["id"])
    return None


def _apply_conflicts(mc, *, item_id: Optional[int], values: dict,
                     board_label: str) -> tuple[dict, list[dict]]:
    """
    On adopt (item_id set): drop columns that would silently overwrite a
    disagreeing Monday value. On create (no item): pass values through.
    """
    if not item_id or not values:
        return dict(values or {}), []
    try:
        existing = fetch_item_column_texts(mc, int(item_id), list(values.keys()))
    except Exception as e:  # noqa: BLE001 — prefer a careful write over a hard fail
        print(f"[jobstart] conflict read failed on {board_label} "
              f"{item_id}: {type(e).__name__}: {e}", file=sys.stderr)
        return dict(values), []
    safe, conflicts = filter_conflicting_writes(values, existing)
    for row in conflicts:
        print(f"[jobstart] kept Monday {board_label} {item_id} "
              f"{row['column_id']}={row['existing']!r}; "
              f"packet wanted {row['proposed']!r}", file=sys.stderr)
    return safe, conflicts


def hand_off(mc, *, bid: dict, job_name: str, projects_values: dict,
             ops_values: dict, accepted_date: str) -> dict:
    """
    THE handoff write. Order matters: Projects first (Operations links to it),
    then Operations, then the Bid Board stamp that records both.

    `bid` is a get_bid_detail() payload. `projects_values` / `ops_values` are
    already-shaped, already-validated column dicts from the flow.

    Returns a report dict. Projects failure is fatal to the handoff (raises);
    Operations and the bid stamp are reported as failures without unwinding the
    Projects write — an ops task the flow couldn't create is visible and
    retryable, whereas a half-rolled-back Monday state is not.

    Adopt path never silently overwrites a filled Monday column that disagrees
    with the packet — those land in report["conflicts"] and stay as-is on the
    board. Empty Monday cells still get filled from the packet.
    """
    report: dict[str, Any] = {"conflicts": []}
    bid_id = int(bid["item_id"])
    # Handoff mutates bid/project/ops links the picker shows — drop the list
    # cache so the next open doesn't serve a pre-handoff badge state.
    monday_cache.invalidate("list:jobstart:bids")

    # ---- Projects: adopt the linked item, else by name, else create --------
    project_id: Optional[int] = None
    for candidate in bid.get("existing_project_ids") or []:
        project_id = int(candidate)
        report["project_source"] = "adopted-link"
        break
    if project_id is None:
        found = find_item_by_name(mc, PROJECTS_BOARD_ID, job_name)
        if found:
            project_id = found
            report["project_source"] = "adopted-name"

    p_values = dict(projects_values)
    p_values[boards.JOBSTART_P_COL_OPPORTUNITY] = {"item_ids": [bid_id]}
    customer_ids = (bid.get("copy") or {}).get("customer_ids") or []
    if customer_ids:
        p_values[boards.JOBSTART_P_COL_CUSTOMER] = {"item_ids": customer_ids}
    location_raw = (bid.get("copy") or {}).get("location_raw")
    if location_raw:
        # Copied verbatim — a location column round-trips its own JSON, and
        # reconstructing lat/lng from a typed address would be a downgrade.
        try:
            p_values[boards.JOBSTART_P_COL_LOCATION] = json.loads(location_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    p_values, p_conflicts = _apply_conflicts(
        mc, item_id=project_id, values=p_values, board_label="Projects")
    for row in p_conflicts:
        row = dict(row, board="projects")
        report["conflicts"].append(row)

    project_id, dropped = _write_with_fallback(
        mc, PROJECTS_BOARD_ID, boards.JOBSTART_PROJECTS_GROUP, job_name,
        p_values, item_id=project_id)
    if report.get("project_source") is None:
        report["project_source"] = "created"
    if dropped:
        report["manual_columns"] = dropped
    report["project_id"] = project_id
    report["project_url"] = _item_url(PROJECTS_BOARD_ID, project_id)

    # ---- Operations: the item that was never being created ----------------
    o_values = dict(ops_values)
    o_values[boards.JOBSTART_OPS_COL_LINK_PROJECTS] = {"item_ids": [project_id]}
    o_values[boards.JOBSTART_OPS_COL_LINK_OPPORTUNITY] = {"item_ids": [bid_id]}
    try:
        ops_id: Optional[int] = None
        for candidate in bid.get("existing_ops_ids") or []:
            ops_id = int(candidate)
            report["ops_source"] = "adopted-link"
            break
        if ops_id is None:
            found = find_item_by_name(mc, OPERATIONS_BOARD_ID, job_name)
            if found:
                ops_id = found
                report["ops_source"] = "adopted-name"
        o_values, o_conflicts = _apply_conflicts(
            mc, item_id=ops_id, values=o_values, board_label="Operations")
        for row in o_conflicts:
            report["conflicts"].append(dict(row, board="operations"))
        if ops_id is None:
            ops_id = _create_item(mc, OPERATIONS_BOARD_ID,
                                  boards.JOBSTART_OPS_GROUP, job_name, o_values)
            report["ops_source"] = "created"
        else:
            _update_item(mc, OPERATIONS_BOARD_ID, ops_id, o_values)
        report["ops_id"] = ops_id
        report["ops_url"] = _item_url(OPERATIONS_BOARD_ID, ops_id)
    except Exception as e:  # noqa: BLE001 — Projects already landed; report it
        report["ops_error"] = f"{type(e).__name__}: {e}"
        print(f"[jobstart] Operations write failed for bid {bid_id}: {e}",
              file=sys.stderr)

    # ---- Bid Board stamp: the columns the legacy automation never wrote ----
    # Accepted Date is fill-if-empty: mark_bid_accepted() already stamps it when
    # Sales marks the bid won. Overwriting here with ops-accept day would lie
    # about when the bid was Accepted. Project/ops links always write — linking
    # the boards IS the handoff's job.
    stamp: dict[str, Any] = {
        boards.JOBSTART_BID_PROJECT_LINK_COL: {"item_ids": [project_id]},
    }
    if not (bid.get("accepted_date") or "").strip():
        stamp[boards.JOBSTART_BID_ACCEPTED_DATE_COL] = {"date": accepted_date}
    if report.get("ops_id"):
        stamp[boards.JOBSTART_BID_OPS_LINK_COL] = {"item_ids": [report["ops_id"]]}
    try:
        _update_item(mc, BID_BOARD_ID, bid_id, stamp)
        report["bid_stamped"] = True
    except Exception as e:  # noqa: BLE001 — the job exists; the stamp can retry
        report["bid_stamp_error"] = f"{type(e).__name__}: {e}"
        print(f"[jobstart] Bid stamp failed for bid {bid_id}: {e}",
              file=sys.stderr)

    return report
