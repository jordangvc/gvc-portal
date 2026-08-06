"""
Personal hub payload — one response renders the whole home screen.
=========================================================================
Shape locked by docs handoff (HubPayload). Live enrichment is best-effort:
Monday / billing / morning failures never 500 the page — they degrade to
role-shaped zeros and a clear or soft summary line.
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from shared import access, hub_nav

_ET = ZoneInfo("America/New_York")


def _person_record(email: str) -> dict:
    try:
        from shared import portal_store
        if access.backend() != "gcs":
            return {}
        rec = portal_store.get_user(email) or {}
        return dict(rec.get("person") or {})
    except Exception:  # noqa: BLE001
        return {}


def _metric(label: str, value: Any, foot: str) -> dict:
    zero = isinstance(value, (int, float)) and value == 0
    return {"label": label, "value": value, "foot": foot, "zero": zero}


_URGENT_KINDS = frozenset({"safety", "blocked", "stop-work", "overdue"})


def _need(*, kind: str, amount: str, title: str, detail: str,
          action: str, href: str, secondary_href: Optional[str] = None,
          urgent: Optional[bool] = None) -> dict:
    kind_s = (kind or "").strip()
    is_urgent = bool(urgent) if urgent is not None else (
        kind_s.lower() in _URGENT_KINDS
    )
    out = {
        "kind": kind_s,
        "amount": amount,
        "title": title,
        "detail": detail,
        "action": action,
        "href": href,
        "urgent": is_urgent,
    }
    if secondary_href:
        out["secondary_href"] = secondary_href
    return out


def _queue_row(name: str, sub: str, *, tag: str = "",
               flagged: bool = False, href: str = "",
               row_id: str = "") -> dict:
    href_s = href or "#"
    return {
        "id": (row_id or href_s).strip() or href_s,
        "name": name,
        "sub": sub,
        "tag": tag,
        "flagged": bool(flagged),
        "href": href_s,
    }


def _try_morning_brief(email: str) -> Optional[dict]:
    try:
        from orchestrators import morning_flow
        return morning_flow.build_employee_brief(email, record_open=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[hub] morning brief skipped: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def _try_billing() -> Optional[dict]:
    try:
        from orchestrators import billing_flow
        return billing_flow.billing_hub_payload()
    except Exception as exc:  # noqa: BLE001
        print(f"[hub] billing skipped: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def _try_owner_pulse(email: str) -> Optional[dict]:
    try:
        from orchestrators import morning_flow
        out = morning_flow.build_owner_pulse(email)
        if out.get("ok") is False:
            return None
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[hub] owner pulse skipped: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def _build_field(email: str, brief: Optional[dict]) -> dict[str, Any]:
    needs: list[dict] = []
    queue_rows: list[dict] = []
    metrics = [
        _metric("Stops today", "—", "from Morning Brief"),
        _metric("Need attention", "—", "blocked or flagged"),
        _metric("Incoming asks", "—", "action requests"),
        _metric("Prep", "—", "ready criteria"),
    ]
    summary = "Open Your Morning Brief for today's route and blockers."
    badges: dict[str, int] = {}

    if not brief or not brief.get("ok", True):
        return {
            "needs": needs, "metrics": metrics, "queue_rows": queue_rows,
            "summary": summary, "badges": badges, "clear": False,
        }

    stops = brief.get("stops") or []
    attention = brief.get("needs_attention") or []
    incoming = (brief.get("action_requests") or {}).get("incoming") or []
    prep = brief.get("preparation") or {}
    hub = brief.get("hub") or {}

    metrics = [
        _metric("Stops today", len(stops), "on your route"),
        _metric("Need attention", len(attention), "blocked or flagged"),
        _metric("Incoming asks", len(incoming), "waiting on you"),
        _metric("Prep",
                f"{prep.get('ready', 0)}/{prep.get('total', 6)}",
                "ready criteria"),
    ]
    if attention:
        badges["jobcheck"] = len(attention)
    if incoming:
        badges["morning"] = len(incoming)

    for row in attention[:4]:
        name = row.get("name") or "Job"
        blocked = (row.get("blocked") or "").strip() or "Needs attention"
        item_id = row.get("item_id")
        href = f"/ui/jobcheck?item={item_id}" if item_id else "/ui/jobcheck"
        needs.append(_need(
            kind="Blocked",
            amount=blocked[:40],
            title=name,
            detail="Flagged on Operations — update Job Check or clear the block.",
            action="Open Job Check",
            href=href,
            secondary_href=href,
        ))

    for req in incoming[: max(0, 4 - len(needs))]:
        needs.append(_need(
            kind="Ask",
            amount=(req.get("due_at") or "Due soon")[:24],
            title=(req.get("project_name") or req.get("need") or "Action request")[:80],
            detail=(req.get("need") or "Someone needs something from you.")[:160],
            action="Open Morning Brief",
            href="/ui/morning",
        ))

    for stop in stops[:12]:
        sid = str(stop.get("item_id") or "").strip()
        queue_rows.append(_queue_row(
            stop.get("name") or "Stop",
            stop.get("address") or stop.get("stage") or "On route",
            tag=stop.get("stage") or "",
            flagged=bool(stop.get("blocked")),
            href=(f"/ui/jobcheck?item={sid}" if sid else "/ui/morning"),
            row_id=sid or "",
        ))

    if not needs:
        summary = (
            f"You're clear. {len(stops)} stop(s) on the route, "
            f"{len(attention)} needing attention."
        )
        if len(stops) == 0 and len(attention) == 0:
            summary = hub.get("label") or "You're clear — nothing waiting on you."
    else:
        summary = (
            f"{len(needs)} item(s) need you · "
            f"{len(stops)} stop(s) on today's route."
        )

    return {
        "needs": needs[:4],
        "metrics": metrics,
        "queue_rows": queue_rows,
        "summary": summary,
        "badges": badges,
        "clear": len(needs) == 0,
    }


def _build_office(email: str, billing: Optional[dict],
                  brief: Optional[dict]) -> dict[str, Any]:
    needs: list[dict] = []
    queue_rows: list[dict] = []
    badges: dict[str, int] = {}
    counts = (billing or {}).get("counts") or {}
    queues = (billing or {}).get("queues") or {}
    ready = queues.get("ready_to_invoice") or []
    handoff_n = int(counts.get("needs_handoff") or 0)
    ready_n = int(counts.get("ready_to_invoice") or len(ready))

    metrics = [
        _metric("Ready to invoice", ready_n if billing else "—",
                "Operations → Ready to Invoice"),
        _metric("Need handoff", handoff_n if billing else "—",
                "accepted bids without a project"),
        _metric("Accepted bids",
                counts.get("accepted_bids", "—") if billing else "—",
                "on the Bid Board"),
        _metric("Projects billing",
                counts.get("projects_billing", "—") if billing else "—",
                "invoice status on Projects"),
    ]
    if ready_n:
        badges["invoice"] = ready_n

    for row in ready[:4]:
        name = row.get("name") or "Job"
        href = row.get("invoice_href") or row.get("primary_href") or "/ui/billing"
        needs.append(_need(
            kind="Invoice",
            amount=row.get("status_label") or "Ready",
            title=name,
            detail="Crew marked this ready — approve to send from Billing Hub.",
            action="Approve to send",
            href=href,
            secondary_href=row.get("monday_url") or href,
        ))

    if handoff_n and len(needs) < 4:
        needs.append(_need(
            kind="Handoff",
            amount=str(handoff_n),
            title="Accepted bids need Job Start",
            detail="Won deals without a Projects item yet — hand them off before billing.",
            action="Open Job Start",
            href="/ui/jobstart",
        ))
        badges["jobstart"] = handoff_n

    for row in ready[:12]:
        queue_rows.append(_queue_row(
            row.get("name") or "Job",
            row.get("builder") or row.get("location") or "Ready to invoice",
            tag="Ready",
            flagged=False,
            href=row.get("invoice_href") or row.get("primary_href") or "/ui/billing",
        ))

    if not billing:
        summary = (
            "Billing numbers will fill in when Monday is reachable — "
            "tools still work from the rail."
        )
        clear = True
    elif not needs:
        summary = (
            f"You're clear. {ready_n} ready to invoice, "
            f"{handoff_n} waiting on handoff."
        )
        clear = True
    else:
        summary = f"{len(needs)} billing item(s) need you today."
        clear = False

    # Fold field attention into office if they also have morning (rare).
    if brief and len(needs) < 4:
        for row in (brief.get("needs_attention") or [])[: 4 - len(needs)]:
            needs.append(_need(
                kind="Field",
                amount=(row.get("blocked") or "Attention")[:40],
                title=row.get("name") or "Job",
                detail="Flagged on Operations.",
                action="Open Job Check",
                href=(f"/ui/jobcheck?item={row['item_id']}"
                      if row.get("item_id") else "/ui/jobcheck"),
            ))

    return {
        "needs": needs[:4],
        "metrics": metrics,
        "queue_rows": queue_rows,
        "summary": summary,
        "badges": badges,
        "clear": clear,
    }


def _build_owner(email: str, pulse: Optional[dict], billing: Optional[dict],
                 brief: Optional[dict]) -> dict[str, Any]:
    needs: list[dict] = []
    queue_rows: list[dict] = []
    badges: dict[str, int] = {}
    counts = (billing or {}).get("counts") or {}
    ready_n = int(counts.get("ready_to_invoice") or 0)
    safety = (pulse or {}).get("safety_stops") or []
    prep_alerts = (pulse or {}).get("prep_alerts") or []
    planning = (pulse or {}).get("planning_signals") or []
    team_pct = (pulse or {}).get("team_preparation_pct")
    if team_pct is None:
        team_pct = ((pulse or {}).get("team_preparation") or {}).get("pct")

    metrics = [
        _metric("Ready to invoice", ready_n if billing else "—",
                "unbilled complete / ready"),
        _metric("Team prep",
                f"{team_pct}%" if team_pct is not None else "—",
                "ops ready for huddle"),
        _metric("Safety / stop-work", len(safety) if pulse else "—",
                "owner-visible holds"),
        _metric("Planning flags", len(planning) if pulse else "—",
                "route override signals"),
    ]
    if ready_n and billing:
        badges["invoice"] = ready_n
    if safety and pulse:
        badges["morning_owner"] = len(safety)

    for row in safety[:3]:
        needs.append(_need(
            kind="Safety",
            amount="Stop-work",
            title=row.get("name") or "Job",
            detail=row.get("blocked") or "Safety hold on Operations.",
            action="Open Owner Pulse",
            href="/ui/morning-owner",
            secondary_href=(f"/ui/jobcheck?item={row['item_id']}"
                            if row.get("item_id") else "/ui/morning-owner"),
        ))

    if ready_n and billing and len(needs) < 4:
        needs.append(_need(
            kind="Invoice",
            amount=str(ready_n),
            title="Jobs ready to invoice",
            detail="Complete work waiting on an invoice draft — approve to send.",
            action="Approve to send",
            href="/ui/billing",
        ))

    for a in prep_alerts[: max(0, 4 - len(needs))]:
        needs.append(_need(
            kind="Prep",
            amount=str(a.get("level") or "Alert"),
            title=a.get("email") or "Team prep",
            detail=a.get("message") or a.get("detail") or "Preparation alert.",
            action="Open Owner Pulse",
            href="/ui/morning-owner",
        ))

    for row in (safety + planning)[:12]:
        queue_rows.append(_queue_row(
            row.get("name") or row.get("email") or "Exception",
            row.get("blocked") or row.get("message") or "Needs owner eyes",
            tag="Flag",
            flagged=True,
            href="/ui/morning-owner",
        ))

    if billing:
        for row in ((billing.get("queues") or {}).get("ready_to_invoice") or [])[:8]:
            if len(queue_rows) >= 12:
                break
            queue_rows.append(_queue_row(
                row.get("name") or "Job",
                "Ready to invoice",
                tag="Ready",
                href=row.get("invoice_href") or "/ui/billing",
            ))

    if not needs:
        if billing is None and pulse is None:
            summary = (
                "Live numbers will fill in when Monday is reachable — "
                "nothing is waiting on you in the portal right now."
            )
        else:
            summary = (
                "You're clear. "
                f"{ready_n if billing else 0} ready to invoice, "
                f"{len(safety) if pulse else 0} safety hold(s), "
                "nothing else waiting on you."
            )
        clear = True
    else:
        summary = f"{len(needs)} exception(s) need you today."
        clear = False

    if brief and not pulse and not billing:
        summary = (
            (brief.get("hub") or {}).get("label")
            or summary
        )

    return {
        "needs": needs[:4],
        "metrics": metrics,
        "queue_rows": queue_rows,
        "summary": summary,
        "badges": badges,
        "clear": clear,
    }


def build_hub_payload(email: str) -> dict[str, Any]:
    """
    Single HubPayload for the signed-in user. Never raises for missing
    integrations — always returns a renderable document.
    """
    email = (email or "").strip().lower()
    feats = access.effective_features(email)
    role = hub_nav.resolve_role(feats)
    person = _person_record(email)
    # Admin-stored override later; person.home_tool is reserved.
    home_override = (person.get("home_tool") or "").strip()
    name = hub_nav.display_name(email, person)
    title = (person.get("position") or "").strip() or hub_nav.ROLE_TITLE.get(role, "")
    now = datetime.now(_ET)
    greeting = hub_nav.greeting_for(name, hour=now.hour)

    home_tool = home_override or hub_nav.ROLE_HOME_TOOL.get(role, "morning")
    home_href = hub_nav.home_tool_href(home_tool, role)
    home_tool_name = hub_nav.home_tool_label(home_tool)

    brief = _try_morning_brief(email) if "morning" in feats else None
    billing = None
    if role in ("owner", "office", "gm") and "invoice" in feats:
        billing = _try_billing()
    pulse = None
    if role == "owner" and ("morning_owner" in feats or email in access.superadmin_emails()):
        pulse = _try_owner_pulse(email)

    if role == "owner":
        shaped = _build_owner(email, pulse, billing, brief)
    elif role in ("office", "gm"):
        shaped = _build_office(email, billing, brief)
    else:
        shaped = _build_field(email, brief)

    # Activity: recent portal events for this actor (best-effort).
    activity_rows: list[dict] = []
    try:
        from shared import activity_read
        # Soft-fail if Cloud Logging / IAM missing — empty is fine.
        packed = activity_read.fetch_events(
            actor=email, page_size=8, range_key="7d")
        for ev in packed.get("events") or []:
            action = ev.get("action") or "event"
            when = ev.get("ts") or ev.get("timestamp") or ""
            target = ev.get("target") or ""
            activity_rows.append({
                "text": f"{action}" + (f" · {target}" if target else ""),
                "when": when,
                "href": "/ui/activity",
            })
    except Exception:  # noqa: BLE001
        activity_rows = []

    pinned: list[dict] = []
    try:
        from subsystems.hub import pinned as hub_pins
        pinned = hub_pins.list_for(email)
    except Exception:  # noqa: BLE001
        pinned = []

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "user": {
            "email": email,
            "name": name,
            "initials": hub_nav.initials_for(name, email),
            "title": title,
            "role": role,
            "homeTool": home_tool,
            "homeToolName": home_tool_name,
            "homeHref": home_href,
        },
        "greeting": greeting,
        "summary": shaped["summary"],
        "needs": shaped["needs"],
        "needs_clear": shaped["clear"],
        "metrics": shaped["metrics"],
        "queue": {
            "title": hub_nav.ROLE_QUEUE_TITLE.get(role, "Your queue"),
            "link": "Open full list",
            "href": home_href,
            "rows": shaped["queue_rows"],
        },
        "pinned": pinned,
        "activity": activity_rows,
        "recent": [],  # client fills from localStorage
        "badges": shaped["badges"],
        "nav": {
            "groups": hub_nav.groups_for_client(feats),
            "features": sorted(feats),
        },
    }
