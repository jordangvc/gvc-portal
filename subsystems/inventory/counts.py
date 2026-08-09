"""Count sessions — quick counts and blind audits. PURE ops on the counts
doc; the resulting adjustments are ledger transactions built here and
posted by the orchestrator (counts never edit balances directly —
invariant 11: counts produce auditable adjustments, not overwrites).

Doc shape:
{"schema": 1, "sessions": {sid: {id, kind ("quick"|"blind"), location_id,
  status ("open"|"submitted"|"approved"|"rejected"), assignee, created_by,
  created_at, submitted_at, approved_by, lines: [{item_id, item_name,
  expected ("" until reveal on blind), counted (str|None), skipped bool,
  skip_reason, note}], auto_post_threshold}}}
"""
from __future__ import annotations

import copy
from decimal import Decimal

from subsystems.inventory.model import (
    InventoryError, new_uuid, now_iso, require,
)


def ensure_shape(doc: dict) -> dict:
    doc.setdefault("schema", 1)
    doc.setdefault("sessions", {})
    return doc


def _get(doc: dict, sid: str) -> dict:
    s = (doc.get("sessions") or {}).get(sid)
    require(s is not None, "UNKNOWN_SESSION",
            f"Count session '{sid}' does not exist.", field="session_id")
    return s


def create_session(doc: dict, *, kind: str, location_id: str,
                   ledger_balances_at_loc: dict, item_names: dict,
                   assignee: str, actor: str,
                   extra_item_ids: list[str] | None = None) -> tuple[dict, dict]:
    """Snapshot expected quantities for every quantity-item at a location.
    Blind sessions keep `expected` hidden from the assignee until submit —
    the API strips it; it is stored so approval can compute variance."""
    require(kind in ("quick", "blind"), "INVALID_INPUT",
            "kind must be quick or blind.", field="kind")
    new = copy.deepcopy(ensure_shape(doc))
    sid = f"cnt-{new_uuid()[:10]}"
    lines = []
    ids = list(ledger_balances_at_loc.keys())
    for extra in (extra_item_ids or []):
        if extra not in ids:
            ids.append(extra)  # expected 0 — count should notice surplus
    for item_id in sorted(ids):
        lines.append({
            "item_id": item_id,
            "item_name": item_names.get(item_id, item_id),
            "expected": str(ledger_balances_at_loc.get(item_id, "0")),
            "counted": None, "skipped": False, "skip_reason": "",
            "note": "",
        })
    require(bool(lines), "INVALID_INPUT",
            "Nothing to count at that location.", field="location_id",
            advice="Add the items you expect there, or count a different "
                   "location.")
    session = {"id": sid, "kind": kind, "location_id": location_id,
               "status": "open", "assignee": (assignee or "").lower(),
               "created_by": actor, "created_at": now_iso(),
               "submitted_at": "", "approved_by": "", "lines": lines}
    new["sessions"][sid] = session
    return new, session


def record_line(doc: dict, sid: str, item_id: str, *, counted=None,
                skipped: bool = False, skip_reason: str = "",
                note: str = "", actor: str = "") -> tuple[dict, dict]:
    new = copy.deepcopy(ensure_shape(doc))
    s = _get(new, sid)
    require(s["status"] == "open", "SESSION_CLOSED",
            f"Session is {s['status']} — no more edits.", field="session_id")
    line = next((ln for ln in s["lines"] if ln["item_id"] == item_id), None)
    require(line is not None, "UNKNOWN_ITEM",
            f"'{item_id}' is not on this count.", field="item_id")
    if skipped:
        require(bool(skip_reason.strip()), "INVALID_INPUT",
                "Skipping a line needs a reason.", field="skip_reason")
        line.update({"skipped": True, "skip_reason": skip_reason.strip(),
                     "counted": None})
    else:
        try:
            q = Decimal(str(counted))
        except Exception:
            raise InventoryError("INVALID_QTY",
                                 f"'{counted}' is not a number.",
                                 field="counted")
        require(q >= 0, "INVALID_QTY",
                "Counted quantity can't be negative — zero is a real "
                "answer, enter 0.", field="counted")
        line.update({"counted": str(q.normalize()), "skipped": False,
                     "skip_reason": ""})
    if note:
        line["note"] = note.strip()
    return new, s


def submit(doc: dict, sid: str, *, actor: str) -> tuple[dict, dict]:
    new = copy.deepcopy(ensure_shape(doc))
    s = _get(new, sid)
    require(s["status"] == "open", "SESSION_CLOSED",
            f"Session is already {s['status']}.", field="session_id")
    unanswered = [ln["item_id"] for ln in s["lines"]
                  if ln["counted"] is None and not ln["skipped"]]
    require(not unanswered, "COUNT_INCOMPLETE",
            f"{len(unanswered)} line(s) have no count and no skip reason.",
            field="lines",
            advice="Mark zero explicitly or skip with a reason.")
    s["status"] = "submitted"
    s["submitted_at"] = now_iso()
    s["submitted_by"] = actor
    return new, s


def variances(session: dict) -> list[dict]:
    out = []
    for ln in session["lines"]:
        if ln["skipped"] or ln["counted"] is None:
            continue
        exp = Decimal(str(ln["expected"] or "0"))
        got = Decimal(str(ln["counted"]))
        if exp != got:
            out.append({"item_id": ln["item_id"],
                        "item_name": ln["item_name"],
                        "expected": str(exp.normalize()),
                        "counted": str(got.normalize()),
                        "variance": str((got - exp).normalize()),
                        "note": ln.get("note", "")})
    return out


def approve(doc: dict, sid: str, *, actor: str,
            reject: bool = False) -> tuple[dict, dict, list[dict]]:
    """Close a submitted session. Returns (doc, session, adjustment_txns)
    — one COUNT_ADJUSTMENT transaction (possibly many lines) for the
    orchestrator to post. Rejection posts nothing."""
    new = copy.deepcopy(ensure_shape(doc))
    s = _get(new, sid)
    require(s["status"] == "submitted", "SESSION_NOT_SUBMITTED",
            f"Session is {s['status']}, not submitted.", field="session_id")
    require(actor.lower() != s.get("submitted_by", "").lower()
            or s["kind"] == "quick", "SELF_APPROVAL",
            "A blind audit can't be approved by the person who counted it.",
            field="session_id")
    if reject:
        s["status"] = "rejected"
        s["approved_by"] = actor
        return new, s, []
    s["status"] = "approved"
    s["approved_by"] = actor
    s["approved_at"] = now_iso()
    txns = []
    lines = []
    for v in variances(s):
        delta = Decimal(v["variance"])
        lines.append({"item_id": v["item_id"],
                      "qty": str(abs(delta)),
                      "unit": "",  # orchestrator fills base unit
                      "sign": 1 if delta > 0 else -1,
                      "note": f"count {v['counted']} vs expected "
                              f"{v['expected']}"})
    if lines:
        txns.append({"type": "COUNT_ADJUSTMENT",
                     "dst": s["location_id"],
                     "reason": f"Count session {sid} "
                               f"({s['kind']}, approved by {actor})",
                     "lines": lines,
                     "count_session_id": sid})
    return new, s, txns


def strip_expected(session: dict) -> dict:
    """Blind view for the assignee: same session minus expected values."""
    out = copy.deepcopy(session)
    if out.get("kind") == "blind" and out.get("status") == "open":
        for ln in out["lines"]:
            ln["expected"] = ""
    return out
