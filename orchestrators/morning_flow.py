"""
Morning Brief flow — employee brief + GM huddle + Owner Pulse.
=========================================================================
docs/MORNING_BRIEF_BUILD_SPEC.md
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from adapters.monday import jobcheck as mj
from adapters.monday import lien as ml
from adapters.monday import morning as mm
from adapters.monday.client import MondayClient
from shared import access
from subsystems.morning import action_requests as ar
from subsystems.morning import link_suggest as ls
from subsystems.morning import meeting as meet
from subsystems.morning import owner_pulse as opulse
from subsystems.morning import personal as personal
from subsystems.morning import prep as prep
from subsystems.morning import route as route
from subsystems.morning import store as mstore

_ET = ZoneInfo("America/New_York")


def _person_name_for(email: str) -> Optional[str]:
    try:
        from shared import portal_store
        if access.backend() != "gcs":
            return None
        doc, _ = portal_store.load()
        user = (doc.get("users") or {}).get((email or "").strip().lower()) or {}
        person = user.get("person") or {}
        name = (person.get("name") or "").strip()
        return name or None
    except Exception:  # noqa: BLE001
        return None


def _ops_team_emails() -> list[str]:
    try:
        from shared import portal_store
        if access.backend() != "gcs":
            return []
        doc, _ = portal_store.load()
        out = []
        for email, rec in (doc.get("users") or {}).items():
            feats = access._expand(rec.get("features"))
            if "morning_ops" in feats or "morning_gm" in feats:
                out.append(email)
        return sorted(out)
    except Exception:  # noqa: BLE001
        return []


def _team_prep_percentage(emails: list[str], workdate: str) -> dict[str, Any]:
    rows = []
    full = 0
    for email in emails:
        try:
            p = prep.get_preparation(email, workdate)
        except Exception:  # noqa: BLE001
            p = {"ready": 0, "total": 6, "streak": 0}
        ok = int(p.get("ready") or 0) >= int(p.get("total") or 6)
        if ok:
            full += 1
        rows.append({
            "email": email,
            "ready": p.get("ready"),
            "total": p.get("total"),
            "full": ok,
            "streak": p.get("streak"),
        })
    n = len(emails) or 1
    return {
        "prepared": full,
        "team_size": len(emails),
        "pct": round(100.0 * full / n) if emails else None,
        "members": rows,
    }


def _optimize_order(stops: list[dict]) -> list[dict]:
    def key(s):
        ht = (s.get("hard_time") or "").strip() or "zzz"
        return (0 if ht != "zzz" else 1, ht, (s.get("name") or "").lower())
    return [dict(s) for s in sorted(stops or [], key=key)]


def _list_open_ars() -> list[dict]:
    try:
        doc, _ = mstore.read_doc(
            __import__("os").environ.get("GVC_MORNING_ACTION_REQUESTS_OBJECT")
            or f"{mstore.PREFIX}action-requests.json"
        )
    except mstore.PortalStoreNotConfigured:
        return []
    rows = [r for r in (doc.get("requests") or {}).values()
            if r.get("status") != "completed"]
    rows.sort(key=lambda r: (
        0 if r.get("escalation") in ("overdue", "ack_reminder") else 1,
        r.get("due_at") or r.get("created_at") or "",
    ))
    return rows


def _card(row: dict, *, reason: str) -> dict:
    blocked = (row.get("blocked") or "").strip()
    overdue = (row.get("overdue") or "").strip()
    return {
        "item_id": row["item_id"],
        "name": row["name"],
        "url": row["url"],
        "project_name": row.get("project_name"),
        "location": row.get("location"),
        "stage": row.get("stage"),
        "stage_detail": row.get("stage_detail"),
        "scheduled_day": row.get("scheduled_day"),
        "blocked": blocked or None,
        "overdue": overdue or None,
        "progress": row.get("progress"),
        "ops_owner_text": row.get("ops_owner_text"),
        "updated_at": row.get("updated_at"),
        # Soft-attached in build_employee_brief; None when Ops→Projects→GFolder missing.
        "gfolder_url": row.get("gfolder_url"),
        "clear": not mm.is_attention(row),
        "relevance_reason": reason,
        "group_title": row.get("group_title"),
    }


def _attach_gfolder_urls(mc, cards: list[dict]) -> None:
    """Attach Open Drive URLs onto brief cards. Soft-fails per item."""
    cache: dict[int, Optional[str]] = {}
    for card in cards:
        try:
            iid = int(card.get("item_id") or 0)
        except (TypeError, ValueError):
            continue
        if not iid:
            continue
        if iid in cache:
            card["gfolder_url"] = cache[iid]
            continue
        url = None
        try:
            url = mm.get_gfolder_url_for_ops_item(mc, iid)
        except Exception as e:  # noqa: BLE001
            print(f"[morning] gfolder skipped for {iid}: {e}", file=sys.stderr)
        cache[iid] = url
        card["gfolder_url"] = url


def build_employee_brief(email: str, *, record_open: bool = True) -> dict[str, Any]:
    email = (email or "").strip().lower()
    display_name = _person_name_for(email)
    now = datetime.now(_ET)
    workdate = now.date().isoformat()
    roles = access.morning_role(email)

    if record_open:
        try:
            prep.record_brief_opened(email, workdate)
            # Opening before cutoff counts; also mark criterion when opened.
            prep.mark_criterion(email, workdate, "opened", value=True, actor=email)
        except Exception as e:  # noqa: BLE001
            print(f"[morning] prep open: {e}", file=sys.stderr)

    mc = MondayClient()
    items = mm.fetch_ops_items(mc)

    author_map: dict = {}
    try:
        author_map = mm.fetch_recent_update_authors(
            mc, [r["item_id"] for r in items[:80]], limit=15)
    except Exception as e:  # noqa: BLE001
        print(f"[morning] update authors skipped: {e}", file=sys.stderr)

    mine: list[dict] = []
    attention: list[dict] = []
    holds: list[dict] = []
    unscheduled: list[dict] = []

    for row in items:
        authors = author_map.get(row["item_id"]) or set()
        relevant = mm.is_personally_relevant(
            row, email=email, display_name=display_name)
        if not relevant and authors:
            relevant = mm.is_relevant_by_updates(
                row, email=email, display_name=display_name, authors=authors)
        reason = "Ops. Owner"
        if relevant and authors and not mm.is_personally_relevant(
                row, email=email, display_name=display_name):
            reason = "Recent update author"
        if relevant:
            mine.append(_card(row, reason=reason))

        if mm.is_long_term_hold(row):
            holds.append(_card(row, reason="Long-term hold"))
        elif mm.is_attention(row):
            attention.append(_card(
                row,
                reason=("Needs attention" if not relevant
                        else f"{reason} · attention"),
            ))

        loc = (row.get("location") or "").strip()
        sched = (row.get("scheduled_day") or "").strip().lower()
        if relevant and (not loc or sched in ("", "unscheduled", "tbd", "none")):
            unscheduled.append(_card(row, reason="Needs planning"))

    def _sort_key(c: dict):
        return ((c.get("scheduled_day") or "zzz").lower(),
                (c.get("name") or "").lower())

    mine.sort(key=_sort_key)
    attention.sort(key=_sort_key)
    holds.sort(key=_sort_key)

    try:
        preparation = prep.get_preparation(email, workdate)
    except Exception:  # noqa: BLE001
        preparation = {
            "ready": 0, "total": 6, "label": "0 of 6 ready",
            "criteria": [{"id": c["id"], "label": c["label"], "done": False}
                         for c in prep.CRITERIA],
            "streak": 0, "alerts": [],
        }

    try:
        origin = route.get_origin(email)
    except Exception:  # noqa: BLE001
        origin = dict(getattr(route, "OFFICE_ORIGIN",
                              {"kind": "office", "label": "Green Valley office"}))

    try:
        saved_route = route.get_route(email, workdate)
    except Exception:  # noqa: BLE001
        saved_route = {"stops": []}

    stops = list(saved_route.get("stops") or [])
    if not stops and mine:
        stops = [{
            "item_id": c["item_id"],
            "name": c["name"],
            "location": c.get("location"),
            "sequence": i,
            "hard_time": None,
            "completed": False,
            "note": None,
        } for i, c in enumerate(mine)]
        try:
            route.save_route(email, workdate, stops=stops, actor=email)
        except Exception:  # noqa: BLE001
            pass

    maps = route.maps_url(stops, origin=origin)
    try:
        ov = route.count_overrides_for(email, as_of=now.date())
    except Exception:  # noqa: BLE001
        ov = {"count": 0, "flag": False}

    try:
        actions = ar.list_for(email)
    except Exception:  # noqa: BLE001
        actions = {"incoming": [], "outgoing": []}

    try:
        notes = personal.get_notes(email)
    except Exception:  # noqa: BLE001
        notes = ""
    try:
        ff = personal.list_proposals(for_email=email, pending_only=True)
    except Exception:  # noqa: BLE001
        ff = []

    refreshed = now.strftime("%I:%M %p").lstrip("0")
    hub_label = (
        f"{preparation.get('ready', 0)} of {preparation.get('total', 6)} ready · "
        f"{len(stops)} stops · {len(attention)} need attention"
    )

    # Open Drive on cards — unique item_ids only; soft-fail if Monday chain incomplete.
    try:
        _attach_gfolder_urls(mc, mine + attention + holds + unscheduled)
    except Exception as e:  # noqa: BLE001
        print(f"[morning] gfolder attach skipped: {e}", file=sys.stderr)

    payload = {
        "ok": True,
        "generated_at": now.isoformat(),
        "workdate": workdate,
        "timezone": "America/New_York",
        "employee": {"email": email, "name": display_name},
        "roles": roles,
        "weather": _weather_stub(origin),
        "leave": None,
        "preparation": preparation,
        "origin": origin,
        "stops": stops,
        "maps_url": maps,
        "route_override_signal": ov,
        "unscheduled": unscheduled,
        "action_requests": {
            "incoming": actions.get("incoming") or [],
            "outgoing": actions.get("outgoing") or [],
            "categories": list(ar.CATEGORIES),
            "crew_trades": list(ar.TRADE_SUBTYPES),
        },
        "my_projects": mine,
        "needs_attention": attention,
        "long_term_holds": holds,
        "notes": notes,
        "fireflies": {
            "configured": personal.fireflies_configured(),
            "proposals": ff,
            "note": (None if personal.fireflies_configured()
                     else "Fireflies ingest when GVC_FIREFLIES_API_KEY is set."),
        },
        "links": {
            "gm": "/ui/morning/gm" if roles.get("is_gm") else None,
            "owner": "/ui/morning/owner" if roles.get("is_owner") else None,
        },
        "hub": {
            "ready": preparation.get("ready"),
            "total": preparation.get("total"),
            "stops": len(stops),
            "attention": len(attention),
            "refreshed_at": refreshed,
            "label": hub_label,
        },
    }
    mm.assert_no_financial_keys(payload)
    return payload


def _weather_stub(origin: dict) -> Optional[dict]:
    lat, lng = origin.get("lat"), origin.get("lng")
    if lat is None or lng is None:
        return {"label": origin.get("label") or "Local", "summary": None}
    try:
        import json
        import urllib.request
        url = (
            "https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lng}&current_weather=true"
            "&temperature_unit=fahrenheit"
        )
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        cw = data.get("current_weather") or {}
        temp = cw.get("temperature")
        return {
            "label": origin.get("label") or "Local",
            "temp_f": temp,
            "summary": f"{temp}°F" if temp is not None else None,
        }
    except Exception:  # noqa: BLE001
        return {"label": "Local", "summary": None}


def hub_summary(email: str) -> dict[str, Any]:
    brief = build_employee_brief(email, record_open=False)
    return {"ok": True, "hub": brief.get("hub") or {},
            "generated_at": brief.get("generated_at"),
            "roles": brief.get("roles")}


def set_prep_criterion(email: str, criterion_id: str, *,
                       done: bool = True, note: Optional[str] = None) -> dict:
    workdate = datetime.now(_ET).date().isoformat()
    meta = {"note": note} if note is not None else None
    prep.mark_criterion(email, workdate, criterion_id, value=done,
                        meta=meta, actor=email)
    return {"ok": True, "preparation": prep.get_preparation(email, workdate)}


def save_origin(email: str, body: dict) -> dict:
    o = route.set_origin(
        email,
        kind=body.get("kind") or "office",
        label=body.get("label"),
        address=body.get("address"),
        lat=body.get("lat"),
        lng=body.get("lng"),
        actor=email,
    )
    return {"ok": True, "origin": o}


def save_stops(email: str, stops: list, *, optimized: bool = False,
               mark_override: bool = False) -> dict:
    workdate = datetime.now(_ET).date().isoformat()
    if mark_override:
        ids = [s.get("item_id") for s in stops]
        out = route.reorder_stops(email, workdate, ids, actor=email)
        saved = out.get("route") or route.get_route(email, workdate)
        ov = out.get("override_signal") or {}
    else:
        saved = route.save_route(email, workdate, stops=stops, actor=email)
        if optimized:
            # Re-save with optimized flag via save (system offer).
            saved = route.save_route(email, workdate, stops=stops, actor=email)
        ov = route.count_overrides_for(email)
    origin = route.get_origin(email)
    return {
        "ok": True,
        "route": saved,
        "maps_url": route.maps_url(saved.get("stops") or stops, origin=origin),
        "override_signal": ov,
    }


def optimize_stops(email: str, stops: Optional[list] = None) -> dict:
    workdate = datetime.now(_ET).date().isoformat()
    current = stops if stops is not None else (
        route.get_route(email, workdate).get("stops") or [])
    ordered = _optimize_order(current)
    return save_stops(email, ordered, optimized=True, mark_override=False)


def complete_stop(email: str, item_id: int, *, note: Optional[str] = None,
                  post_monday: bool = True) -> dict:
    workdate = datetime.now(_ET).date().isoformat()
    saved = route.complete_stop(email, workdate, item_id, note=note, actor=email)
    monday_update = None
    if post_monday:
        try:
            mc = MondayClient()
            body = f"Stop completed via Morning Brief ({email.split('@')[0]})"
            if note:
                body += f"\n{note}"
            monday_update = mm.create_item_update(mc, int(item_id), body)
        except Exception as e:  # noqa: BLE001
            monday_update = {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "route": saved, "monday_update": monday_update}


def create_action_request(email: str, body: dict) -> dict:
    req = ar.create_request(
        requester_email=email,
        needed_from_email=body.get("needed_from_email") or "",
        category=body.get("category") or "other",
        need=body.get("need") or "",
        trade_subtype=body.get("trade_subtype"),
        project_item_id=body.get("project_item_id"),
        project_name=body.get("project_name"),
        due_at=body.get("due_at"),
    )
    try:
        from adapters import slack_notify
        slack_notify.notify_action_request_assigned(req)
    except Exception as e:  # noqa: BLE001
        print(f"[morning] AR slack: {e}", file=sys.stderr)
    try:
        prep.mark_criterion(
            email, datetime.now(_ET).date().isoformat(), "requests",
            value=True, actor=email)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "request": req}


def ack_action_request(email: str, request_id: str) -> dict:
    return {"ok": True, "request": ar.acknowledge(request_id, by_email=email)}


def complete_action_request(email: str, request_id: str) -> dict:
    return {"ok": True, "request": ar.complete(request_id, by_email=email)}


def add_project_update(email: str, item_id: int, *, note: str,
                       files: Optional[list] = None) -> dict:
    """
    Upload photos to the job's Pictures folder (when present) and post a
    Monday update with the note + Drive links. Never silently drops photos —
    if files were attached, failures surface in `warning` / `drive_error`.
    """
    from adapters.drive import DriveUploader

    files = list(files or [])
    mc = MondayClient()
    ginfo = mj.get_linked_project_gfolder(mc, int(item_id))
    gurl = ginfo.get("gfolder_url")
    folder_id = ginfo.get("folder_id")
    drive_result = None
    drive_error = None
    warning = None

    if files and not folder_id:
        detail = (ginfo.get("error")
                  or "Couldn't resolve the project Drive folder.")
        warning = (f"Photos were not uploaded — {detail} "
                   "The Monday note was still posted.")
        drive_error = detail
    elif files and folder_id:
        try:
            up = DriveUploader()
            drive_result = up.upload_job_site_photos(
                folder_id, files, note=note)
        except Exception as e:  # noqa: BLE001
            drive_error = f"{type(e).__name__}: {e}"
            warning = ("Photos failed to upload to Drive — "
                       f"{type(e).__name__}. The Monday note was still posted.")
            print(f"[morning] drive photo upload failed: {drive_error}",
                  file=sys.stderr)

    links = []
    if drive_result:
        for u in drive_result.get("uploaded") or []:
            if u.get("webViewLink"):
                links.append(u["webViewLink"])
    body = f"Field update via Morning Brief ({email.split('@')[0]})"
    if note:
        body += f"\n{note}"
    if links:
        body += "\n\nPhotos:"
        for link in links:
            body += f"\n{link}"
    elif files and not links:
        body += f"\n\n({len(files)} photo(s) attached in portal — Drive upload pending)"

    upd = mm.create_item_update(mc, int(item_id), body)
    return {
        "ok": True,
        "drive": drive_result,
        "drive_error": drive_error,
        "photos_uploaded": len(links),
        "photos_requested": len(files),
        "monday_update": upd,
        "gfolder_url": gurl,
        "gfolder": ginfo,
        "warning": warning,
    }


def photo_ready(item_id: int) -> dict[str, Any]:
    """
    Read-only: is this Ops item ready for Drive photo uploads?
    Resolves Ops → Projects → GFolder and returns photo_ready_status fields.
    """
    item_id = int(item_id)
    mc = MondayClient()
    ginfo = mj.get_linked_project_gfolder(mc, item_id)
    status = mj.photo_ready_status(ginfo)
    return {
        "ok": True,
        "item_id": item_id,
        **status,
        "gfolder": ginfo,
    }


def suggest_links(item_id: int, *, limit: int = 100) -> dict[str, Any]:
    """
    Read-only heuristic suggestions for Ops→Projects link and Drive folder.
    NEVER writes Monday. Caps Projects candidates at `limit` (default 100).
    """
    item_id = int(item_id)
    limit = max(1, min(int(limit or 100), 100))
    mc = MondayClient()

    item = mj.get_item_values(mc, item_id, [])
    if item is None:
        return {
            "ok": False,
            "item_id": item_id,
            "error": "ITEM_NOT_FOUND",
            "detail": f"Monday item {item_id} not found.",
        }

    ops_name = item.get("name") or ""
    ginfo = mj.get_linked_project_gfolder(mc, item_id)
    status = mj.photo_ready_status(ginfo)

    projects = ml.fetch_active_projects(mc)[:limit]
    project_candidates = [
        {"id": p["item_id"], "name": p["name"], "url": p.get("url")}
        for p in projects
    ]
    project_sug = ls.suggest_project_for_ops(ops_name, project_candidates)

    # Drive suggestion: prefer the already-linked GFolder; else look up
    # GFolder on the suggested Projects item and score it as a one-candidate list.
    folder_candidates: list[dict] = []
    if ginfo.get("folder_id") and ginfo.get("gfolder_url"):
        folder_candidates.append({
            "id": ginfo["folder_id"],
            "name": ops_name,
            "url": ginfo["gfolder_url"],
        })
    elif (project_sug.get("match") and not project_sug.get("ambiguous")
          and project_sug["match"].get("id")):
        pg = mj.get_project_gfolder(mc, int(project_sug["match"]["id"]))
        if pg.get("folder_id"):
            folder_candidates.append({
                "id": pg["folder_id"],
                "name": project_sug["match"].get("name") or ops_name,
                "url": pg.get("gfolder_url"),
            })
    drive_sug = ls.suggest_drive_folder(ops_name, folder_candidates)

    return {
        "ok": True,
        "item_id": item_id,
        "ops_name": ops_name,
        "photo_ready": status,
        "current": ginfo,
        "project": project_sug,
        "drive": drive_sug,
        "candidates_scanned": len(project_candidates),
    }


def build_gm_view(email: str) -> dict[str, Any]:
    roles = access.morning_role(email)
    if not roles.get("is_gm") and not roles.get("is_owner"):
        return {"ok": False, "code": "FORBIDDEN", "detail": "GM role required."}
    now = datetime.now(_ET)
    workdate = now.date().isoformat()
    team = _ops_team_emails()
    prep_pct = _team_prep_percentage(team, workdate)
    brief = build_employee_brief(email, record_open=False)
    meeting = meet.get_run(workdate)
    ars = _list_open_ars()
    ff = personal.list_proposals(pending_only=True)
    sequence = list(brief.get("needs_attention") or [])
    seen = {c["item_id"] for c in sequence}
    for c in brief.get("my_projects") or []:
        if c["item_id"] not in seen:
            sequence.append(c)
    planning = []
    for e in team:
        try:
            sig = route.count_overrides_for(e)
            if sig.get("flag") or sig.get("alert"):
                planning.append({**sig, "email": e})
        except Exception:  # noqa: BLE001
            pass
    return {
        "ok": True,
        "workdate": workdate,
        "generated_at": now.isoformat(),
        "facilitator": {"email": email, "name": _person_name_for(email)},
        "team_prep": prep_pct,
        "sequence": sequence,
        "unscheduled": brief.get("unscheduled") or [],
        "action_requests": ars,
        "meeting": meeting,
        "fireflies_proposals": ff,
        "planning_signals": planning,
    }


def build_owner_pulse(email: str) -> dict[str, Any]:
    roles = access.morning_role(email)
    if not roles.get("is_owner"):
        return {"ok": False, "code": "FORBIDDEN", "detail": "Owner Pulse role required."}
    now = datetime.now(_ET)
    workdate = now.date().isoformat()
    team = _ops_team_emails()
    prep_pct = _team_prep_percentage(team, workdate)
    prep_alerts = []
    for em in team:
        try:
            p = prep.get_preparation(em, workdate)
            for a in p.get("alerts") or []:
                level = a.get("level")
                if level in (3, 5, "3", "5", "visibility", "coaching"):
                    prep_alerts.append({**a, "email": em})
        except Exception:  # noqa: BLE001
            pass
    meeting = meet.get_run(workdate)
    planning = []
    for e in team:
        try:
            sig = route.count_overrides_for(e)
            if sig.get("flag") or sig.get("alert") or int(sig.get("count") or 0) >= 3:
                planning.append({**sig, "email": e, "alert": True})
        except Exception:  # noqa: BLE001
            pass
    safety = []
    try:
        mc = MondayClient()
        for row in mm.fetch_ops_items(mc):
            blocked = (row.get("blocked") or "").lower()
            if mm.is_attention(row) and any(
                k in blocked for k in ("safety", "hazard", "stop work", "stop-work")
            ):
                safety.append({"item_id": row["item_id"], "name": row["name"],
                               "blocked": row.get("blocked")})
    except Exception:  # noqa: BLE001
        pass

    pulse = opulse.build_owner_pulse({
        "prep_pct": prep_pct.get("pct"),
        "safety_stops": safety,
        "owner_decisions": [],
        "prep_alerts_3_5": prep_alerts,
        "huddle_outcome": {
            "projects_covered": len(meeting.get("ordered_item_ids") or []),
            "actions_assigned": len(meeting.get("actions") or []),
            "unresolved_owner_risks": [],
        },
        "planning_signals": planning,
    })
    # Shape expected by morning-owner.html
    return {
        "ok": True,
        "workdate": workdate,
        "team_preparation_pct": pulse.get("team_prep_pct"),
        "team_preparation": prep_pct,
        "safety_stops": pulse.get("safety_stops") or [],
        "prep_alerts": pulse.get("preparation_alerts") or [],
        "huddle": {
            "projects_covered": (pulse.get("huddle_outcome") or {}).get("projects_covered"),
            "actions_assigned": (pulse.get("huddle_outcome") or {}).get("actions_assigned"),
            "parked": len(meeting.get("parking") or []),
            "started_at": meeting.get("started_at"),
            "ended_at": meeting.get("ended_at"),
        },
        "planning_signals": pulse.get("planning_signals") or [],
        "owner_decisions": pulse.get("owner_decisions") or [],
        "note": "Exception-only. Routine ops stay with the GM and project owners.",
    }


def run_prep_cutoff_sweep() -> dict:
    from adapters import slack_notify
    now = datetime.now(_ET)
    workdate = now.date().isoformat()
    if not prep.is_scheduled_workday(now.date()):
        return {"ok": True, "skipped": "not_a_workday"}
    notified = []
    owner_alerts = []
    for email in _ops_team_emails():
        try:
            p = prep.get_preparation(email, workdate, now=now)
            streak = int(p.get("streak") or 0)
            if streak >= 1 and int(p.get("ready") or 0) < int(p.get("total") or 6):
                slack_notify.notify_prep_miss_private(email, streak=streak)
                notified.append(email)
                # Spec: 3+/5-day tiers get high-level owner visibility — not a task list.
                if streak >= 3:
                    msg = (f"Prep streak {streak} for {email} "
                           f"(informational — GM owns coaching).")
                    slack_notify.notify_owner_prep_alert(msg)
                    owner_alerts.append(email)
        except Exception as e:  # noqa: BLE001
            print(f"[morning] prep sweep {email}: {e}", file=sys.stderr)
    newly = []
    try:
        newly = ar.run_escalations(now)
    except Exception as e:  # noqa: BLE001
        print(f"[morning] AR escalate: {e}", file=sys.stderr)
    for req in newly or []:
        try:
            payload = req.get("request") if isinstance(req, dict) and "request" in req else req
            esc = (payload.get("escalation") if isinstance(payload, dict) else None) or ""
            if esc == "ack_reminder":
                slack_notify.notify_action_request_ack_due(payload)
            else:
                slack_notify.notify_action_request_escalation(payload)
        except Exception:  # noqa: BLE001
            pass
    return {
        "ok": True,
        "prep_notified": notified,
        "owner_alerts": owner_alerts,
        "ar_escalated": len(newly or []),
    }
