"""
Lien Watch flow — Monday facts in, deadline tracker out. READ-ONLY (P1).
=========================================================================
For every active job on the Projects board, work out the state, project
type, and first-furnishing clock, run the rule pack's deadline math
(subsystems/lien_watch/deadlines.py over shared/lien_rules.json), and
produce the payload the /ui/lien status page renders.

First-furnishing sourcing (investigated live 2026-07-26):
  1. the board's "Start Date" column (`date`) when set — basis "start_date";
  2. otherwise the item's creation date — basis "assumed" (documented
     fallback; usually later than reality is impossible here, but it can be
     badly EARLY for long-queued jobs, hence the visible basis chip);
  3. EXCEPT jobs still in "New Projects (Not Started)" with no Start Date:
     no furnishing has happened, so no clock — deadlines stay "unknown"
     instead of fabricating a countdown from whenever the row was typed in.

ALERTS ARE BUILT DARK (design amendment, Jordan 2026-07-26 — LAW):
send_lien_alerts() exists, is tested, and does nothing unless the env var
GVC_LIEN_ALERTS_ENABLED is exactly "true". There is deliberately NO
scheduler, NO cron, NO route, and NO wiring into any existing loop calling
it. ONLY JORDAN enables that flag, and only once the team is ready to
absorb the pings.

Dedup (sent-markers) lives in subsystems/lien_watch/alert_state.py so a
scheduled sweep can run more than once per day without re-pinging the same
T-14/7/3/1 mark. Markers key on item + kind + mark + due_date.

Monday is READ-ONLY here — no notice-status writebacks until a later phase.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from typing import Optional

# T-minus marks (days remaining) at which a deadline earns a Slack ping.
ALERT_MARKS = (14, 7, 3, 1)


def _first_furnishing(job: dict) -> tuple[Optional[date], Optional[str]]:
    """(clock date, basis) for one normalized Monday row. basis is
    "start_date" | "assumed" | None (None = job not started, no clock)."""
    raw = (job.get("start_date") or "").strip()
    if raw:
        try:
            return date.fromisoformat(raw[:10]), "start_date"
        except ValueError:
            pass  # garbage in the column — fall through to the fallback
    from adapters.monday.lien import NOT_STARTED_GROUP_ID
    if job.get("group_id") == NOT_STARTED_GROUP_ID:
        return None, None  # nothing furnished yet — no clock to run
    created = (job.get("created_at") or "").strip()
    if created:
        try:
            return date.fromisoformat(created[:10]), "assumed"
        except ValueError:
            pass
    return None, None


def build_tracker(*, today: Optional[date] = None) -> dict:
    """
    One full read of the Projects board → the tracker payload:
      {ok, generated_at, rules_version, attorney_reviewed: false,
       counts: {jobs, missed, critical, warn, ok, unknown}, jobs: [...]}
    Jobs sorted most-urgent first. Per-job problems (bad dates, unparseable
    state) degrade that job to "unknown" — they never break the sweep.
    """
    from adapters.monday.client import MondayClient
    from adapters.monday.lien import fetch_active_projects
    from subsystems.lien_watch import deadlines as lw

    today = today or date.today()
    rules = lw.load_rules()
    mc = MondayClient()
    raw_jobs = fetch_active_projects(mc)

    jobs: list[dict] = []
    for job in raw_jobs:
        state = lw.parse_state(job.get("location"), job.get("name"))
        ptype = lw.normalize_project_type(job.get("project_type_label"))
        ff_date, basis = _first_furnishing(job)
        result = lw.compute_deadlines(state, ptype, ff_date, today=today,
                                      rules=rules)
        notes: list[str] = list(result["advisories"])
        if not result["state_known"]:
            notes.insert(0, "State unknown — set the Job Location on the "
                            "Monday row so deadlines can be computed.")
        if ff_date is None and result["state_known"]:
            notes.insert(0, "Job not started (no Start Date) — the furnishing "
                            "clock hasn't begun.")
        elif basis == "assumed":
            notes.append("First furnishing assumed from the Monday item's "
                         "creation date — set the Start Date column for a "
                         "real clock.")
        jobs.append({
            "item_id": job["item_id"],
            "name": job["name"],
            "url": job["url"],
            "group": job.get("group_title"),
            "project_number": job.get("project_number"),
            "state": state,
            "project_type": ptype,
            "project_type_label": job.get("project_type_label"),
            "ambiguous_type": ptype not in lw.PROJECT_TYPES,
            "first_furnishing": ff_date.isoformat() if ff_date else None,
            "first_furnishing_basis": basis,
            "deadlines": result["deadlines"],
            "max_severity": result["max_severity"],
            "notes": notes,
        })

    rank = {s: i for i, s in enumerate(lw.SEVERITY_ORDER)}

    def _urgency(j: dict):
        dated = [d["days_remaining"] for d in j["deadlines"]
                 if d["days_remaining"] is not None]
        return (rank.get(j["max_severity"], len(rank)),
                min(dated) if dated else 10**6,
                j["name"].lower())

    jobs.sort(key=_urgency)
    counts = {"jobs": len(jobs)}
    for sev in lw.SEVERITY_ORDER:
        counts[sev] = sum(1 for j in jobs if j["max_severity"] == sev)
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "rules_version": rules.get("version"),
        "attorney_reviewed": False,  # flips only when P0 counsel review lands
        "counts": counts,
        "jobs": jobs,
    }


def _alert_message(job: dict, deadline: dict) -> str:
    """PURE: one T-minus Slack ping. Unit-testable without a Slack stub."""
    days = deadline["days_remaining"]
    when = ("due TODAY" if days == 0
            else f"due in {days} day{'s' if days != 1 else ''}")
    parts = [
        f"⏳ *Lien Watch — {deadline['label']} {when}* "
        f"({deadline['due_date']}).",
        f"• Job: {job['name']}",
        f"• State: {job.get('state', '—')} · {deadline['statute']}",
        f"• Clock: {deadline['anchor_text']} "
        f"{job.get('first_furnishing', '—')}"
        + (" (assumed from item creation)"
           if job.get("first_furnishing_basis") == "assumed" else ""),
        f"• {job['url']}",
        "_Deadline math is unverified by counsel — attorney review pending._",
    ]
    return "\n".join(parts)


def send_lien_alerts(tracker: Optional[dict] = None, *,
                     dry_run: bool = False) -> dict:
    """
    T-14 / T-7 / T-3 / T-1 Slack pings for every dated deadline in the
    tracker. BUILT DARK — the design amendment (Jordan, 2026-07-26) is LAW:

        ONLY JORDAN ENABLES GVC_LIEN_ALERTS_ENABLED. No agent, no session,
        no deploy sets it. Until then this function logs and returns.

    Gate first, work later: nothing below the check runs while dark. Channel
    comes ONLY from GVC_LIEN_SLACK_CHANNEL (COI pattern — no named fallback,
    so a half-configured deploy can't spray pings into #bids). Dedup via
    alert_state sent-markers — safe to schedule once the flag is on.
    """
    # THE GATE (top of function, per the amendment). Exactly "true" — not
    # "1", not "yes" — so nothing enables it by accident.
    if (os.environ.get("GVC_LIEN_ALERTS_ENABLED") or "").strip() != "true":
        print("[lien-watch] lien alerts disabled "
              "(GVC_LIEN_ALERTS_ENABLED != 'true'); nothing sent.",
              file=sys.stderr)
        return {"ok": True, "enabled": False, "sent": [], "errors": [],
                "skipped": []}

    from adapters import slack_notify
    from subsystems.lien_watch import alert_state

    if tracker is None:
        tracker = build_tracker()
    channel = (os.environ.get("GVC_LIEN_SLACK_CHANNEL") or "").strip()
    sent_doc, dedup_backend = alert_state.load_sent_doc()
    out: dict = {"ok": True, "enabled": True, "dry_run": dry_run,
                 "dedup": dedup_backend, "sent": [], "skipped": [],
                 "errors": []}
    if not channel:
        out["ok"] = False
        out["errors"].append("GVC_LIEN_SLACK_CHANNEL not set; no pings sent.")
        return out

    for job in tracker.get("jobs", []):
        for deadline in job.get("deadlines", []):
            if deadline.get("days_remaining") not in ALERT_MARKS:
                continue
            key = alert_state.alert_key(
                job["item_id"], deadline["kind"],
                deadline["days_remaining"], deadline.get("due_date"))
            entry = {"item_id": job["item_id"], "kind": deadline["kind"],
                     "days_remaining": deadline["days_remaining"],
                     "key": key}
            if alert_state.already_sent(sent_doc, key):
                entry["skipped"] = "already_sent"
                out["skipped"].append(entry)
                continue
            if dry_run:
                entry["would_send"] = True
                out["sent"].append(entry)
                continue
            try:
                slack_notify.post_message(_alert_message(job, deadline),
                                          channel=channel)
                entry["sent"] = True
                backend = alert_state.remember_sent(key)
                out["dedup"] = backend
                # Keep the in-sweep view current so a duplicate row in the
                # same tracker can't double-post before GCS round-trips.
                sent_doc = alert_state.with_sent(sent_doc, key)
            except slack_notify.SlackNotConfigured:
                out["errors"].append("SLACK_BOT_TOKEN not set; ping skipped.")
                entry["sent"] = False
            except Exception as e:  # noqa: BLE001 — one bad ping ≠ dead sweep
                out["errors"].append(f"{job['item_id']}/{deadline['kind']}: "
                                     f"{type(e).__name__}: {e}")
                entry["sent"] = False
            out["sent"].append(entry)
    return out
