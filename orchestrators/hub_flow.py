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
    unavailable = value == "—" or value is None
    zero = (not unavailable) and isinstance(value, (int, float)) and value == 0
    # "0/6" style strings that mean empty-ready stay non-zero unless all zero.
    if not unavailable and not zero and value == "0":
        zero = True
    return {
        "label": label,
        "value": "—" if unavailable else value,
        "foot": foot,
        "zero": zero,
        "unavailable": unavailable,
    }


_URGENT_KINDS = frozenset({"safety", "blocked", "stop-work", "overdue"})

_ACTIVITY_NOISE = frozenset({
    "hub.open", "signin", "tool.open", "activity.view", "billing.open",
    "lien.status",
})

_ACTIVITY_LABELS = {
    "invoice.run": "Invoice",
    "estimate.run": "Estimate",
    "estimate.qa": "Estimate QA",
    "change_order.run": "Change order",
    "coi.run": "COI",
    "coi.bulk.run": "COI bulk",
    "check.commit": "Check payment",
    "check.extract": "Check read",
    "jobcheck.save": "Job check",
    "admin.grant.update": "Access changed",
    "estimate.slack": "Estimate Slack notice",
    "estimate.draft.save": "Estimate draft",
    "invoice.draft.save": "Invoice draft",
    "change_order.draft.save": "CO draft",
    "jobstart.sent_to_ops": "Job Start → ops",
    "jobstart.sent_back": "Job Start sent back",
    "jobstart.accepted": "Job Start accepted",
    "jobstart.gc_confirmation_drafted": "GC confirmation drafted",
    "billing.search": "Billing search",
    "billing.lookup": "Billing lookup",
}


def _need(*, kind: str, amount: str, title: str, detail: str,
          action: str, href: str, secondary_href: Optional[str] = None,
          urgent: Optional[bool] = None,
          sort_key: str = "") -> dict:
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
        "sort_key": sort_key or "",
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


def _age_amount(date_str: str, *, prefix: str = "Ready") -> str:
    """Turn YYYY-MM-DD (or similar) into 'Ready 3d' / 'Due today' / prefix."""
    raw = (date_str or "").strip()[:10]
    if not raw or len(raw) < 8:
        return prefix
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return f"{prefix} {raw}" if prefix else raw
    today = datetime.now(_ET).date()
    days = (today - d).days
    if days <= 0:
        return f"{prefix} today" if prefix else "Today"
    if days == 1:
        return f"{prefix} 1d"
    if days < 14:
        return f"{prefix} {days}d"
    return f"{prefix} {days}d"


def _sort_needs(needs: list[dict]) -> list[dict]:
    """Urgent first, then oldest sort_key (ISO date), then title."""
    def key(n: dict) -> tuple:
        urgent = 0 if n.get("urgent") else 1
        sk = (n.get("sort_key") or "9999-99-99")
        return (urgent, sk, (n.get("title") or "").lower())
    return sorted(needs, key=key)


def _human_activity(action: str, target: str, result: str = "") -> Optional[str]:
    act = (action or "").strip()
    if not act or act in _ACTIVITY_NOISE:
        return None
    label = _ACTIVITY_LABELS.get(act) or act.replace(".", " · ")
    tgt = (target or "").strip()
    # Trim long feature lists from hub.open-style targets if any slip through.
    if tgt and "," in tgt and len(tgt) > 40:
        tgt = ""
    text = label + (f" · {tgt}" if tgt else "")
    res = (result or "").strip().lower()
    if res and res not in ("ok", "success", ""):
        text += f" ({result})"
    return text


def _ready_amount(row: dict) -> str:
    labels = row.get("status_labels") or []
    if row.get("ready_date"):
        return _age_amount(str(row.get("ready_date")), prefix="Ready")
    for lab in labels:
        if lab:
            return str(lab)[:40]
    return (row.get("stage") or row.get("billable") or "Ready")[:40]


def _queue_link_for(role: str, home_href: str, home_name: str) -> tuple[str, str]:
    if role == "owner":
        return ("Open Owner Pulse", home_href or "/ui/morning-owner")
    if role in ("office", "gm"):
        return ("Open Billing", "/ui/billing")
    return (f"Open {home_name}" if home_name else "Open", home_href or "#")


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
        "needs": _sort_needs(needs)[:4],
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
    bids = queues.get("accepted_bids") or []
    handoff_bids = [b for b in bids if b.get("needs_handoff")]
    handoff_n = int(counts.get("needs_handoff") or len(handoff_bids))
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
    if handoff_n:
        badges["jobstart"] = handoff_n

    # Oldest ready first (ready_date ascending).
    ready_sorted = sorted(
        ready,
        key=lambda r: (r.get("ready_date") or "9999-99-99", r.get("name") or ""),
    )
    for row in ready_sorted[:4]:
        name = row.get("name") or "Job"
        href = row.get("invoice_href") or row.get("primary_href") or "/ui/billing"
        rid = str(row.get("project_item_id") or row.get("item_id") or "").strip()
        needs.append(_need(
            kind="Invoice",
            amount=_ready_amount(row),
            title=name,
            detail="Crew marked this ready — approve to send from Billing Hub.",
            action="Approve to send",
            href=href,
            secondary_href=row.get("monday_url") or href,
            sort_key=str(row.get("ready_date") or ""),
        ))

    handoff_sorted = sorted(
        handoff_bids,
        key=lambda b: (b.get("accepted_date") or "9999-99-99", b.get("name") or ""),
    )
    for bid in handoff_sorted:
        if len(needs) >= 4:
            break
        bid_id = str(bid.get("item_id") or bid.get("bid_item_id") or "").strip()
        href = bid.get("jobstart_href") or bid.get("primary_href") or "/ui/jobstart"
        if bid_id and "bid=" not in href and href.startswith("/ui/jobstart"):
            href = f"/ui/jobstart?bid={bid_id}"
        needs.append(_need(
            kind="Handoff",
            amount=_age_amount(str(bid.get("accepted_date") or ""), prefix="Accepted"),
            title=bid.get("name") or "Accepted bid",
            detail="Won deal without a Projects item — hand off before billing.",
            action="Open Job Start",
            href=href,
            secondary_href=bid.get("monday_url") or href,
            sort_key=str(bid.get("accepted_date") or ""),
        ))

    for row in ready_sorted[:12]:
        rid = str(row.get("project_item_id") or row.get("item_id") or "").strip()
        href = row.get("invoice_href") or row.get("primary_href") or "/ui/billing"
        # Flag when ready ≥3 days.
        flagged = False
        try:
            rd = str(row.get("ready_date") or "")[:10]
            if rd:
                d = datetime.strptime(rd, "%Y-%m-%d").date()
                flagged = (datetime.now(_ET).date() - d).days >= 3
        except ValueError:
            flagged = False
        queue_rows.append(_queue_row(
            row.get("name") or "Job",
            row.get("builder") or row.get("location") or "Ready to invoice",
            tag="Ready",
            flagged=flagged,
            href=href,
            row_id=rid or href,
        ))

    for bid in handoff_sorted[:8]:
        if len(queue_rows) >= 12:
            break
        bid_id = str(bid.get("item_id") or "").strip()
        href = bid.get("jobstart_href") or "/ui/jobstart"
        if bid_id and "bid=" not in href:
            href = f"/ui/jobstart?bid={bid_id}"
        queue_rows.append(_queue_row(
            bid.get("name") or "Accepted bid",
            bid.get("builder") or "Needs Job Start",
            tag="Handoff",
            flagged=True,
            href=href,
            row_id=bid_id or href,
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
            sid = str(row.get("item_id") or "").strip()
            needs.append(_need(
                kind="Field",
                amount=(row.get("blocked") or "Attention")[:40],
                title=row.get("name") or "Job",
                detail="Flagged on Operations.",
                action="Open Job Check",
                href=(f"/ui/jobcheck?item={sid}" if sid else "/ui/jobcheck"),
                urgent=True,
                sort_key="",
            ))

    return {
        "needs": _sort_needs(needs)[:4],
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
    ready = ((billing or {}).get("queues") or {}).get("ready_to_invoice") or []
    ready_n = int(counts.get("ready_to_invoice") or len(ready))
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
        sid = str(row.get("item_id") or "").strip()
        jc = f"/ui/jobcheck?item={sid}" if sid else "/ui/morning-owner"
        needs.append(_need(
            kind="Safety",
            amount="Stop-work",
            title=row.get("name") or "Job",
            detail=row.get("blocked") or "Safety hold on Operations.",
            action="Open Owner Pulse",
            href="/ui/morning-owner",
            secondary_href=jc,
            sort_key="",
        ))

    ready_sorted = sorted(
        ready,
        key=lambda r: (r.get("ready_date") or "9999-99-99", r.get("name") or ""),
    )
    if ready_sorted and billing and len(needs) < 4:
        # Prefer one aggregate if many, else surface top aged job.
        if ready_n > 2:
            needs.append(_need(
                kind="Invoice",
                amount=str(ready_n),
                title="Jobs ready to invoice",
                detail="Complete work waiting on an invoice draft — approve to send.",
                action="Approve to send",
                href="/ui/billing",
                sort_key=str(ready_sorted[0].get("ready_date") or ""),
            ))
        else:
            for row in ready_sorted[: max(0, 4 - len(needs))]:
                href = row.get("invoice_href") or "/ui/billing"
                needs.append(_need(
                    kind="Invoice",
                    amount=_ready_amount(row),
                    title=row.get("name") or "Job",
                    detail="Ready to invoice — approve to send.",
                    action="Approve to send",
                    href=href,
                    sort_key=str(row.get("ready_date") or ""),
                ))

    for a in prep_alerts[: max(0, 4 - len(needs))]:
        needs.append(_need(
            kind="Prep",
            amount=str(a.get("level") or "Alert"),
            title=a.get("email") or "Team prep",
            detail=a.get("message") or a.get("detail") or "Preparation alert.",
            action="Open Owner Pulse",
            href="/ui/morning-owner",
            sort_key="",
        ))

    for row in (safety + planning)[:12]:
        sid = str(row.get("item_id") or "").strip()
        href = f"/ui/jobcheck?item={sid}" if sid else "/ui/morning-owner"
        queue_rows.append(_queue_row(
            row.get("name") or row.get("email") or "Exception",
            row.get("blocked") or row.get("message") or "Needs owner eyes",
            tag="Flag",
            flagged=True,
            href=href,
            row_id=sid or href,
        ))

    if billing:
        for row in ready_sorted[:8]:
            if len(queue_rows) >= 12:
                break
            rid = str(row.get("project_item_id") or row.get("item_id") or "").strip()
            href = row.get("invoice_href") or "/ui/billing"
            queue_rows.append(_queue_row(
                row.get("name") or "Job",
                "Ready to invoice",
                tag="Ready",
                href=href,
                row_id=rid or href,
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
        "needs": _sort_needs(needs)[:4],
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
            actor=email, page_size=16, range_key="7d")
        for ev in packed.get("events") or []:
            text = _human_activity(
                ev.get("action") or "",
                ev.get("target") or "",
                ev.get("result") or "",
            )
            if not text:
                continue
            when = ev.get("ts") or ev.get("timestamp") or ""
            activity_rows.append({
                "text": text,
                "when": when,
                "href": "/ui/activity",
            })
            if len(activity_rows) >= 6:
                break
    except Exception:  # noqa: BLE001
        activity_rows = []

    pinned: list[dict] = []
    try:
        from subsystems.hub import pinned as hub_pins
        pinned = hub_pins.list_for(email)
    except Exception:  # noqa: BLE001
        pinned = []

    q_link, q_href = _queue_link_for(role, home_href, home_tool_name)

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
            "link": q_link,
            "href": q_href,
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
