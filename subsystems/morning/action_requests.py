"""
Action Requests — the dedicated request/acknowledgment board that retires
`Needs from Jordan` (docs/MORNING_BRIEF_BUILD_SPEC.md "Action Requests").
=========================================================================
GCS is the source of truth in this slice. A Monday board is optional and
wires in later (shared.boards.ACTION_REQUESTS_BOARD_ID; `0` = GCS-only) —
the model here does not depend on one existing, per the spec's own framing
("Create a dedicated Action Requests board" describes the FEATURE, not
necessarily a literal Monday board on day one).

Categories (spec):

    Crew setup       -> trade_subtype: framing | hanging | scrapping |
                         finishing | ceilings_act | service | other
    Customer/GC scheduling
    Materials
    Equipment
    Information
    Decision/approval
    Help needed
    Other

Rules:
  - Recipient must acknowledge within 30 minutes during scheduled work hours
    (7:00-17:00 America/New_York, Mon-Fri). The 30-minute clock is evaluated
    against wall-clock elapsed time while `now` itself falls inside work
    hours — a request that lands at 4:50 PM does not silently escalate at
    5:20 the same evening if nobody's watching Slack past 5.
  - Passing `due_at` without completion raises the OVERDUE escalation.
  - `evaluate_escalations` only ever RETURNS signals (which requests need an
    ack-reminder DM or have gone overdue) — sending the Slack DM and pinging
    the GM is the caller's job (later slice), same principle as this
    package's route-override signal.
  - Migration of old `Needs from Jordan` values into `needs_triage` status
    is available via `migrate_needs_from_jordan` (GM/owner-triggered; does
    NOT clear the Monday column). Spec: "Migrated requests begin as Needs
    triage, not overdue."

Storage — one object, default `portal/morning/action-requests.json`:

    {
      "version": 1,
      "requests": {
        "<id>": {
          "id": "...", "requester_email": "...", "needed_from_email": "...",
          "category": "materials", "trade_subtype": null,
          "need": "...", "project_item_id": 123, "project_name": "...",
          "due_at": "...", "created_at": "...",
          "acknowledged_at": null, "completed_at": null,
          "escalation": "none", "status": "open"
        }
      }
    }
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from shared import portal_store as portal_store
from subsystems.morning import prep as morning_prep
from subsystems.morning import store as morning_store

PortalStoreNotConfigured = morning_store.PortalStoreNotConfigured

DOC_VERSION = 1
DEFAULT_OBJECT = f"{morning_store.PREFIX}action-requests.json"

_ET = ZoneInfo("America/New_York")

CATEGORY_CREW_SETUP = "crew_setup"
CATEGORIES: tuple[str, ...] = (
    CATEGORY_CREW_SETUP,
    "customer_gc_scheduling",
    "materials",
    "equipment",
    "information",
    "decision_approval",
    "help_needed",
    "other",
)

TRADE_SUBTYPES: tuple[str, ...] = (
    "framing", "hanging", "scrapping", "finishing", "ceilings_act", "service", "other",
)

STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_COMPLETED = "completed"
STATUS_NEEDS_TRIAGE = "needs_triage"
STATUSES = (STATUS_OPEN, STATUS_ACKNOWLEDGED, STATUS_COMPLETED, STATUS_NEEDS_TRIAGE)

ESCALATION_NONE = "none"
ESCALATION_ACK_REMINDER = "ack_reminder"
ESCALATION_OVERDUE = "overdue"
ESCALATION_NEEDS_TRIAGE = "needs_triage"
ESCALATIONS = (ESCALATION_NONE, ESCALATION_ACK_REMINDER, ESCALATION_OVERDUE, ESCALATION_NEEDS_TRIAGE)

ACK_WINDOW_MINUTES = 30
WORK_HOURS_START = 7   # 7:00 AM ET
WORK_HOURS_END = 17    # 5:00 PM ET

# Migration tag — open ARs with this source + same project_item_id skip re-import.
SOURCE_NEEDS_FROM_JORDAN = "needs_from_jordan"
_OPEN_STATUSES = frozenset({STATUS_OPEN, STATUS_ACKNOWLEDGED, STATUS_NEEDS_TRIAGE})


class ActionRequestValidationError(ValueError):
    """Structurally invalid Action Request input (caller maps to HTTP 422)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_email(email: str) -> str:
    return portal_store.normalize_email(email)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def within_work_hours(when: datetime) -> bool:
    """PURE. True when `when` falls on a scheduled workday between 7 AM and
    5 PM America/New_York."""
    local = when.astimezone(_ET)
    if not morning_prep.is_scheduled_workday(local.date()):
        return False
    return WORK_HOURS_START <= local.hour < WORK_HOURS_END


def new_request_id() -> str:
    return uuid.uuid4().hex


def validate_category(category: str, trade_subtype: Optional[str]) -> None:
    if category not in CATEGORIES:
        raise ActionRequestValidationError(f"Unknown category {category!r} (expected one of {CATEGORIES}).")
    if category == CATEGORY_CREW_SETUP:
        if trade_subtype not in TRADE_SUBTYPES:
            raise ActionRequestValidationError(
                f"crew_setup requires trade_subtype in {TRADE_SUBTYPES}, got {trade_subtype!r}.")
    elif trade_subtype is not None:
        raise ActionRequestValidationError(
            f"trade_subtype is only valid for category={CATEGORY_CREW_SETUP!r}.")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def apply_create(doc: dict, *, requester_email: str, needed_from_email: str,
                  category: str, need: str, trade_subtype: Optional[str],
                  project_item_id: Optional[Any], project_name: Optional[str],
                  due_at: Optional[str], request_id: Optional[str] = None,
                  status: Optional[str] = None, source: Optional[str] = None,
                  allow_empty_needed_from: bool = False) -> tuple[dict, dict]:
    """PURE. Create one Action Request. Returns (new_doc, record)."""
    validate_category(category, trade_subtype)
    if not requester_email:
        raise ActionRequestValidationError("requester_email is required.")
    if not needed_from_email and not allow_empty_needed_from:
        raise ActionRequestValidationError("requester_email and needed_from_email are required.")
    if not (need or "").strip():
        raise ActionRequestValidationError("need is required.")
    status_v = status or STATUS_OPEN
    if status_v not in STATUSES:
        raise ActionRequestValidationError(f"Unknown status {status_v!r}.")

    rid = request_id or new_request_id()
    record = {
        "id": rid,
        "requester_email": requester_email,
        "needed_from_email": needed_from_email or "",
        "category": category,
        "trade_subtype": trade_subtype,
        "need": need.strip(),
        "project_item_id": project_item_id,
        "project_name": project_name,
        "due_at": due_at,
        "created_at": _now_iso(),
        "acknowledged_at": None,
        "acknowledged_by": None,
        "completed_at": None,
        "completed_by": None,
        "escalation": (ESCALATION_NEEDS_TRIAGE if status_v == STATUS_NEEDS_TRIAGE
                       else ESCALATION_NONE),
        "status": status_v,
    }
    if source:
        record["source"] = source
    requests = dict(doc.get("requests") or {})
    requests[rid] = record
    return {**doc, "version": DOC_VERSION, "requests": requests}, record


def nfj_need_text(*, project_name: Optional[str], item_name: Optional[str],
                   label: str) -> str:
    """PURE. Plain-language need line for a migrated NFJ item."""
    title = (project_name or item_name or "Ops item").strip()
    label_s = (label or "").strip()
    return f"{title} — Needs from Jordan: {label_s}"


def existing_open_nfj(requests: dict, *, project_item_id: Any,
                      need: Optional[str] = None) -> Optional[dict]:
    """
    PURE. Find an open AR that already covers this Ops item's NFJ migration
    (same project_item_id + source tag, or same project_item_id + similar need).
    """
    try:
        pid = int(project_item_id) if project_item_id is not None else None
    except (TypeError, ValueError):
        pid = project_item_id
    need_n = (need or "").strip().lower()
    for rec in (requests or {}).values():
        if rec.get("status") not in _OPEN_STATUSES:
            continue
        try:
            rid_pid = int(rec["project_item_id"]) if rec.get("project_item_id") is not None else None
        except (TypeError, ValueError):
            rid_pid = rec.get("project_item_id")
        if rid_pid != pid:
            continue
        if rec.get("source") == SOURCE_NEEDS_FROM_JORDAN:
            return rec
        if need_n and (rec.get("need") or "").strip().lower() == need_n:
            return rec
        # Same project + "Needs from Jordan" in the need text → treat as dup.
        if "needs from jordan" in (rec.get("need") or "").lower():
            return rec
    return None


_CLEAR_NFJ_LABELS = frozenset({"", "clear", "none", "n/a", "na", "-"})


def active_nfj_label(row: dict) -> Optional[str]:
    """PURE. Active Needs from Jordan label on a row, or None when clear."""
    label = (row.get("needs_from_jordan") or "").strip()
    if not label or label.lower() in _CLEAR_NFJ_LABELS:
        return None
    return label


def plan_nfj_migrations(rows: list, *, existing_requests: dict) -> list[dict]:
    """
    PURE. From Ops rows (with needs_from_jordan), return create plans for
    active labels that aren't already covered. Each plan:
      {project_item_id, project_name, need, label}
    """
    plans: list[dict] = []
    seen_ids: set = set()
    for row in rows or []:
        label = active_nfj_label(row)
        if not label:
            continue
        item_id = row.get("item_id")
        if item_id is None or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        need = nfj_need_text(
            project_name=row.get("project_name") or row.get("name"),
            item_name=row.get("name"),
            label=label,
        )
        if existing_open_nfj(existing_requests, project_item_id=item_id, need=need):
            continue
        plans.append({
            "project_item_id": item_id,
            "project_name": row.get("project_name") or row.get("name"),
            "need": need,
            "label": label,
        })
    return plans


def apply_acknowledge(doc: dict, *, request_id: str, by_email: str,
                       at: Optional[str] = None) -> tuple[dict, dict]:
    """PURE. Acknowledge one open request. Idempotent — acknowledging an
    already-acknowledged (or completed) request is a no-op that returns the
    existing record unchanged. Raises if the request doesn't exist."""
    requests = dict(doc.get("requests") or {})
    rec = requests.get(request_id)
    if rec is None:
        raise ActionRequestValidationError(f"No Action Request with id {request_id!r}.")
    if rec.get("status") in (STATUS_ACKNOWLEDGED, STATUS_COMPLETED):
        return doc, rec
    new_rec = {
        **rec,
        "status": STATUS_ACKNOWLEDGED,
        "acknowledged_at": at or _now_iso(),
        "acknowledged_by": _norm_email(by_email) if by_email else None,
        "escalation": ESCALATION_NONE if rec.get("escalation") == ESCALATION_ACK_REMINDER
        else rec.get("escalation", ESCALATION_NONE),
    }
    requests[request_id] = new_rec
    return {**doc, "version": DOC_VERSION, "requests": requests}, new_rec


def apply_complete(doc: dict, *, request_id: str, by_email: str,
                    at: Optional[str] = None) -> tuple[dict, dict]:
    """PURE. Complete one request. Idempotent. Raises if it doesn't exist."""
    requests = dict(doc.get("requests") or {})
    rec = requests.get(request_id)
    if rec is None:
        raise ActionRequestValidationError(f"No Action Request with id {request_id!r}.")
    if rec.get("status") == STATUS_COMPLETED:
        return doc, rec
    new_rec = {
        **rec,
        "status": STATUS_COMPLETED,
        "completed_at": at or _now_iso(),
        "completed_by": _norm_email(by_email) if by_email else None,
        "escalation": ESCALATION_NONE,
    }
    requests[request_id] = new_rec
    return {**doc, "version": DOC_VERSION, "requests": requests}, new_rec


def needs_ack_reminder(req: dict, now: datetime) -> bool:
    """PURE. True when an open, un-acknowledged request has been outstanding
    30+ minutes of wall-clock time and `now` is inside scheduled work hours
    (so a request from last night doesn't ping the moment someone opens
    Slack at 7:01 AM unless it's genuinely still unacknowledged then — which
    it correctly would be)."""
    if req.get("status") != STATUS_OPEN or req.get("acknowledged_at"):
        return False
    created = _parse_iso(req.get("created_at"))
    if created is None or not within_work_hours(now):
        return False
    return (now - created) >= timedelta(minutes=ACK_WINDOW_MINUTES)


def is_overdue(req: dict, now: datetime) -> bool:
    """PURE. True when `due_at` has passed and the request isn't completed."""
    if req.get("status") == STATUS_COMPLETED:
        return False
    due = _parse_iso(req.get("due_at"))
    if due is None:
        return False
    return now >= due


def evaluate_escalations(requests: list, now: datetime) -> list[dict]:
    """
    PURE. `requests` is a list of stored Action Request records. Returns
    signals ONLY for requests whose escalation should change:
    [{"id", "escalation", "reason"}]. Sending the Slack DM / GM ping is the
    caller's job — this function never calls out.
    """
    signals: list[dict] = []
    for req in requests or []:
        current = req.get("escalation", ESCALATION_NONE)
        if req.get("status") == STATUS_NEEDS_TRIAGE:
            continue  # migrated items keep their own lifecycle
        if is_overdue(req, now):
            if current != ESCALATION_OVERDUE:
                signals.append({"id": req["id"], "escalation": ESCALATION_OVERDUE,
                                "reason": "due_at has passed without completion"})
        elif needs_ack_reminder(req, now):
            if current != ESCALATION_ACK_REMINDER:
                signals.append({"id": req["id"], "escalation": ESCALATION_ACK_REMINDER,
                                "reason": f"unacknowledged {ACK_WINDOW_MINUTES}+ minutes during work hours"})
    return signals


def apply_escalations(doc: dict, *, signals: list) -> dict:
    """PURE. Write the escalation field for each signalled request id."""
    if not signals:
        return doc
    requests = dict(doc.get("requests") or {})
    for sig in signals:
        rid = sig.get("id")
        if rid in requests:
            requests[rid] = {**requests[rid], "escalation": sig["escalation"]}
    return {**doc, "version": DOC_VERSION, "requests": requests}


def summarize_for(requests: dict, email: str) -> dict:
    """PURE. `requests` (the doc's {"id": record} map) -> {"incoming",
    "outgoing"} lists for `email`, open/urgent first."""
    email_n = _norm_email(email)
    incoming, outgoing = [], []
    for rec in requests.values():
        if rec.get("needed_from_email") == email_n:
            incoming.append(rec)
        if rec.get("requester_email") == email_n:
            outgoing.append(rec)

    order = {ESCALATION_OVERDUE: 0, ESCALATION_ACK_REMINDER: 1,
             ESCALATION_NEEDS_TRIAGE: 2, ESCALATION_NONE: 3}

    def _key(r: dict):
        return (r.get("status") == STATUS_COMPLETED,
                order.get(r.get("escalation"), 9),
                r.get("due_at") or "9999-99-99",
                r.get("created_at") or "")

    incoming.sort(key=_key)
    outgoing.sort(key=_key)
    return {"incoming": incoming, "outgoing": outgoing}


# ---------------------------------------------------------------------------
# Store-touching API
# ---------------------------------------------------------------------------

def _object_name() -> str:
    return os.environ.get("GVC_MORNING_ACTION_REQUESTS_OBJECT") or DEFAULT_OBJECT


def create_request(*, requester_email: str, needed_from_email: str, category: str,
                    need: str, trade_subtype: Optional[str] = None,
                    project_item_id: Optional[Any] = None, project_name: Optional[str] = None,
                    due_at: Optional[str] = None) -> dict:
    """Create and persist one Action Request. Returns the stored record."""
    requester_n = _norm_email(requester_email)
    needed_n = _norm_email(needed_from_email)

    def fn(doc: dict):
        return apply_create(doc, requester_email=requester_n, needed_from_email=needed_n,
                             category=category, need=need, trade_subtype=trade_subtype,
                             project_item_id=project_item_id, project_name=project_name,
                             due_at=due_at)

    return morning_store.mutate(_object_name(), fn)


def acknowledge(request_id: str, *, by_email: str, at: Optional[str] = None) -> dict:
    """Acknowledge one request. Returns the stored (or unchanged) record."""

    def fn(doc: dict):
        return apply_acknowledge(doc, request_id=request_id, by_email=by_email, at=at)

    return morning_store.mutate(_object_name(), fn)


def complete(request_id: str, *, by_email: str, at: Optional[str] = None) -> dict:
    """Complete one request. Returns the stored (or unchanged) record."""

    def fn(doc: dict):
        return apply_complete(doc, request_id=request_id, by_email=by_email, at=at)

    return morning_store.mutate(_object_name(), fn)


def list_for(email: str) -> dict:
    """{"incoming": [...], "outgoing": [...]} for `email`."""
    doc, _ = morning_store.read_doc(_object_name())
    return summarize_for(doc.get("requests") or {}, email)


def shape_owner_decisions(requests, *, owner_email: str,
                          limit: int = 20) -> list[dict]:
    """
    PURE. Open Action Requests that need the owner's attention → calm Pulse
    cards (not a task dump). Include when:
      - needed_from_email is the owner, OR
      - source is Needs-from-Jordan migration, OR
      - category is decision_approval AND aimed at the owner
    Completed rows never appear. Sorted overdue → triage → ack → none.
    """
    owner_n = _norm_email(owner_email)
    if not owner_n:
        return []
    if isinstance(requests, dict):
        rows = list(requests.values())
    else:
        rows = list(requests or [])

    picked: list[dict] = []
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        if rec.get("status") not in _OPEN_STATUSES:
            continue
        needed = _norm_email(rec.get("needed_from_email") or "")
        source = (rec.get("source") or "").strip()
        category = (rec.get("category") or "").strip()
        # Aimed at owner, or NFJ migration (even when needed_from was blank).
        for_owner = (needed == owner_n or source == SOURCE_NEEDS_FROM_JORDAN)
        if not for_owner:
            continue
        try:
            pid = int(rec["project_item_id"]) if rec.get("project_item_id") is not None else None
        except (TypeError, ValueError):
            pid = None
        href = f"/ui/jobcheck?item={pid}" if pid else "/ui/morning"
        picked.append({
            "id": rec.get("id"),
            "need": (rec.get("need") or "").strip(),
            "project_name": (rec.get("project_name") or "").strip() or None,
            "project_item_id": pid,
            "category": category or None,
            "status": rec.get("status"),
            "escalation": rec.get("escalation") or ESCALATION_NONE,
            "requester_email": rec.get("requester_email"),
            "source": source or None,
            "href": href,
            "created_at": rec.get("created_at"),
            "due_at": rec.get("due_at"),
        })

    order = {ESCALATION_OVERDUE: 0, ESCALATION_ACK_REMINDER: 1,
             ESCALATION_NEEDS_TRIAGE: 2, ESCALATION_NONE: 3}

    def _key(r: dict):
        return (order.get(r.get("escalation"), 9),
                r.get("due_at") or "9999-99-99",
                r.get("created_at") or "")

    picked.sort(key=_key)
    if limit and limit > 0:
        return picked[: int(limit)]
    return picked


def list_owner_decisions(*, owner_email: Optional[str] = None,
                         limit: int = 20) -> list[dict]:
    """
    Load open owner-facing Action Requests for Owner Pulse.
    Soft-fails to [] when the portal store isn't configured.
    """
    owner = _norm_email(
        owner_email
        or os.environ.get("GVC_OWNER_SLACK_EMAIL")
        or "jordan@greenvalleycontractors.com"
    )
    try:
        doc, _ = morning_store.read_doc(_object_name())
    except PortalStoreNotConfigured:
        return []
    except Exception:  # noqa: BLE001 — Pulse must still render
        return []
    return shape_owner_decisions(
        doc.get("requests") or {}, owner_email=owner, limit=limit,
    )


def get_request(request_id: str) -> Optional[dict]:
    doc, _ = morning_store.read_doc(_object_name())
    return (doc.get("requests") or {}).get(request_id)


def run_escalations(now: Optional[datetime] = None) -> list[dict]:
    """
    Evaluate + persist escalation transitions for every stored request, and
    return the FULL updated record for each one that changed — not the bare
    {"id","escalation","reason"} signal `evaluate_escalations` computes
    internally. The caller (a later slice) turns each into a Slack DM / GM
    ping (e.g. slack_notify.notify_action_request_escalation, which reads
    `needed_from_email` and `need` off the record), so it needs the whole
    request, not just what changed. Never sends anything itself.
    """
    now = now or datetime.now(timezone.utc)

    def fn(doc: dict):
        requests = list((doc.get("requests") or {}).values())
        signals = evaluate_escalations(requests, now)
        if not signals:
            return doc, []
        new_doc = apply_escalations(doc, signals=signals)
        updated = dict(new_doc.get("requests") or {})
        full_records = [updated[s["id"]] for s in signals if s.get("id") in updated]
        return new_doc, full_records

    return morning_store.mutate(_object_name(), fn)


def migrate_needs_from_jordan(rows: list, *, actor_email: str,
                               jordan_email: Optional[str] = None) -> dict:
    """
    Import active Needs from Jordan Ops labels into Action Requests
    (status=needs_triage, category=decision_approval, source=needs_from_jordan).

    Idempotent: skips when an open AR already exists for the same
    project_item_id with the migration tag or a similar need. Does NOT
    clear or write the Monday column — that is a later ops step.

    Returns {"created": n, "skipped": n, "created_ids": [...], "skipped_ids": [...]}.
    """
    actor_n = _norm_email(actor_email)
    if not actor_n:
        raise ActionRequestValidationError("actor_email is required.")
    jordan_n = _norm_email(jordan_email) if jordan_email else ""

    created_ids: list[str] = []
    skipped_ids: list[Any] = []

    # Count skips for rows that have an active label but already have an AR.
    doc, _ = morning_store.read_doc(_object_name())
    existing = dict(doc.get("requests") or {})
    plans: list[dict] = []
    for row in rows or []:
        label = active_nfj_label(row)
        if not label:
            continue
        item_id = row.get("item_id")
        need = nfj_need_text(
            project_name=row.get("project_name") or row.get("name"),
            item_name=row.get("name"),
            label=label,
        )
        if existing_open_nfj(existing, project_item_id=item_id, need=need):
            skipped_ids.append(item_id)
            continue
        plans.append({
            "project_item_id": item_id,
            "project_name": row.get("project_name") or row.get("name"),
            "need": need,
            "label": label,
        })

    for plan in plans:
        def fn(doc: dict, plan=plan):
            # Re-check inside the mutate for race safety.
            if existing_open_nfj(doc.get("requests") or {},
                                 project_item_id=plan["project_item_id"],
                                 need=plan["need"]):
                return doc, None
            return apply_create(
                doc,
                requester_email=actor_n,
                needed_from_email=jordan_n,
                category="decision_approval",
                need=plan["need"],
                trade_subtype=None,
                project_item_id=plan["project_item_id"],
                project_name=plan["project_name"],
                due_at=None,
                status=STATUS_NEEDS_TRIAGE,
                source=SOURCE_NEEDS_FROM_JORDAN,
                allow_empty_needed_from=True,
            )

        rec = morning_store.mutate(_object_name(), fn)
        if rec is None:
            skipped_ids.append(plan["project_item_id"])
        else:
            created_ids.append(rec["id"])

    return {
        "created": len(created_ids),
        "skipped": len(skipped_ids),
        "created_ids": created_ids,
        "skipped_ids": skipped_ids,
    }
