"""Monday Payroll board reads — Rate × Count labor actuals per Projects item.

Board 1923506521. Projects links via board_relation_mkvpzg8y on Projects;
Payroll side relation is board_relation_mkvpgjme. P5 pulls labor cost as the
sum of Rate×Count rows linked to the job — never guessed.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

from shared.boards import PAYROLL_BOARD_ID, PROJECTS_BOARD_ID

# Projects → Payroll relation column (on Projects board).
P_COL_PAYROLL_LINK = "board_relation_mkvpzg8y"
# Payroll → Projects relation (on Payroll board).
PAY_COL_PROJECTS_LINK = "board_relation_mkvpgjme"
PAY_COL_COUNT = "numbers"
PAY_COL_RATE = "numbers9"
PAY_COL_TYPE = "text6"
PAY_COL_SF = "text5"

_VALUE_FRAGMENT = """
          id
          text
          value
          ... on MirrorValue { display_value }
          ... on BoardRelationValue { display_value linked_item_ids }
"""


def _num(raw: Any) -> float:
    if raw is None:
        return 0.0
    try:
        return float(str(raw).replace(",", "").replace("$", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _linked_ids(cv: Optional[dict]) -> list[int]:
    if not cv:
        return []
    out: list[int] = []
    for pid in cv.get("linked_item_ids") or []:
        try:
            out.append(int(pid))
        except (TypeError, ValueError):
            continue
    if out:
        return out
    try:
        parsed = json.loads(cv.get("value") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    for pid in parsed.get("linkedPulseIds") or parsed.get("linked_item_ids") or []:
        try:
            if isinstance(pid, dict):
                out.append(int(pid.get("linkedPulseId") or pid.get("id")))
            else:
                out.append(int(pid))
        except (TypeError, ValueError):
            continue
    return out


def _column_text(cv: dict) -> Optional[str]:
    for key in ("display_value", "text"):
        raw = cv.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def fetch_payroll_for_project(mc, project_item_id: int) -> dict:
    """
    Return labor actuals for a Projects item:
      {project_item_id, rows: [...], labor_cost, board_count_hint, row_count}
    Each row: {item_id, name, count, rate, total, type, square_footage}
    """
    if not project_item_id:
        return {
            "project_item_id": None,
            "rows": [],
            "labor_cost": 0.0,
            "board_count_hint": None,
            "row_count": 0,
        }

    # 1) Read Projects relation → payroll item ids
    q_proj = """
    query ($ids: [ID!]) {
      items(ids: $ids) {
        id
        name
        column_values(ids: %s) { %s }
      }
    }
    """ % (json.dumps([P_COL_PAYROLL_LINK, "board_counts"]), _VALUE_FRAGMENT)
    data = mc._query(q_proj, {"ids": [str(int(project_item_id))]})
    items = data.get("items") or []
    payroll_ids: list[int] = []
    board_count_hint = None
    if items:
        cvs = {cv["id"]: cv for cv in items[0].get("column_values") or []}
        payroll_ids = _linked_ids(cvs.get(P_COL_PAYROLL_LINK))
        bc = _column_text(cvs.get("board_counts") or {})
        if bc:
            board_count_hint = _num(bc) or None

    # 2) Fallback: scan Payroll board for rows linked back to this project
    if not payroll_ids:
        payroll_ids = _find_payroll_ids_by_scan(mc, int(project_item_id))

    if not payroll_ids:
        return {
            "project_item_id": int(project_item_id),
            "rows": [],
            "labor_cost": 0.0,
            "board_count_hint": board_count_hint,
            "row_count": 0,
        }

    q_pay = """
    query ($ids: [ID!]) {
      items(ids: $ids) {
        id
        name
        column_values(ids: %s) { %s }
      }
    }
    """ % (
        json.dumps([PAY_COL_COUNT, PAY_COL_RATE, PAY_COL_TYPE, PAY_COL_SF,
                    PAY_COL_PROJECTS_LINK]),
        _VALUE_FRAGMENT,
    )
    pay_data = mc._query(q_pay, {"ids": [str(i) for i in payroll_ids]})
    rows: list[dict] = []
    labor_cost = 0.0
    for item in pay_data.get("items") or []:
        cvs = {cv["id"]: cv for cv in item.get("column_values") or []}
        count = _num(_column_text(cvs.get(PAY_COL_COUNT) or {}) or
                     (cvs.get(PAY_COL_COUNT) or {}).get("text"))
        rate = _num(_column_text(cvs.get(PAY_COL_RATE) or {}) or
                    (cvs.get(PAY_COL_RATE) or {}).get("text"))
        # Prefer explicit text; fall back to value JSON number fields.
        if not count:
            try:
                count = _num(json.loads((cvs.get(PAY_COL_COUNT) or {}).get("value") or "null"))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if not rate:
            try:
                rate = _num(json.loads((cvs.get(PAY_COL_RATE) or {}).get("value") or "null"))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        total = round(count * rate, 2)
        labor_cost += total
        rows.append({
            "item_id": int(item["id"]),
            "name": (item.get("name") or "").strip(),
            "count": count,
            "rate": rate,
            "total": total,
            "type": _column_text(cvs.get(PAY_COL_TYPE) or {}),
            "square_footage": _column_text(cvs.get(PAY_COL_SF) or {}),
        })

    return {
        "project_item_id": int(project_item_id),
        "rows": rows,
        "labor_cost": round(labor_cost, 2),
        "board_count_hint": board_count_hint,
        "row_count": len(rows),
    }


def _find_payroll_ids_by_scan(mc, project_item_id: int) -> list[int]:
    """Fallback walk of Payroll board when Projects relation is empty."""
    col_ids = json.dumps([PAY_COL_PROJECTS_LINK])
    query = """
    query ($boardId: [ID!], $cursor: String) {
      boards(ids: $boardId) {
        items_page(limit: 200, cursor: $cursor) {
          cursor
          items {
            id
            column_values(ids: %s) { %s }
          }
        }
      }
    }
    """ % (col_ids, _VALUE_FRAGMENT)
    found: list[int] = []
    cursor: Optional[str] = None
    try:
        while True:
            data = mc._query(query, {
                "boardId": [str(PAYROLL_BOARD_ID)],
                "cursor": cursor,
            })
            board_list = data.get("boards") or []
            if not board_list:
                break
            page = board_list[0]["items_page"]
            for item in page.get("items") or []:
                cvs = {cv["id"]: cv for cv in item.get("column_values") or []}
                if project_item_id in _linked_ids(cvs.get(PAY_COL_PROJECTS_LINK)):
                    found.append(int(item["id"]))
            cursor = page.get("cursor")
            if not cursor:
                break
    except Exception as exc:  # noqa: BLE001
        print(
            f"[monday-payroll] scan failed ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
    return found


# Silence unused import lint for PROJECTS_BOARD_ID (documented for callers).
_ = PROJECTS_BOARD_ID
