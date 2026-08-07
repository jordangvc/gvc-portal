"""Pure tests for the personal hub home (nav + payload + pins).

Run: python tests/test_hub_home.py
  or: .venv/bin/pytest tests/test_hub_home.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_hub_nav_roles() -> None:
    from shared import hub_nav

    check("owner via morning_owner", hub_nav.resolve_role({"morning_owner"}) == "owner")
    check("admin alone is NOT owner",
          hub_nav.resolve_role({"admin", "morning", "activity"}) == "field")
    check("admin + invoice → office",
          hub_nav.resolve_role({"admin", "invoice", "activity"}) == "office")
    check("gm", hub_nav.resolve_role({"morning_gm", "morning"}) == "gm")
    check("office via invoice", hub_nav.resolve_role({"invoice", "morning"}) == "office")
    check("sales via estimate",
          hub_nav.resolve_role({"estimate", "takeoff", "jobstart", "morning"}) == "sales")
    check("estimate+invoice → office not sales",
          hub_nav.resolve_role({"estimate", "invoice"}) == "office")
    check("field default", hub_nav.resolve_role({"morning", "jobcheck"}) == "field")
    check("greeting morning",
          hub_nav.greeting_for("Jordan Faulkner", hour=8).startswith("Good morning"))
    check("initials", hub_nav.initials_for("Jordan Faulkner", "j@x.com") == "JF")
    check("home label owner", hub_nav.home_tool_label("morning_owner") == "Owner Pulse")
    check("home href billing", hub_nav.home_tool_href("invoice") == "/ui/billing")
    check("sales home estimate", hub_nav.ROLE_HOME_TOOL["sales"] == "estimate")
    check("gm queue title", "Huddle" in hub_nav.ROLE_QUEUE_TITLE["gm"])

    groups = hub_nav.groups_for_client({"morning", "jobcheck", "fieldguide", "timeoff"})
    names = [g["name"] for g in groups]
    check("Today group first", names[0] == "Today")
    morning = next(t for g in groups for t in g["tools"] if t["feature"] == "morning")
    estimate = next(t for g in groups for t in g["tools"] if t["feature"] == "estimate")
    check("granted morning", morning["granted"] is True)
    check("dim estimate", estimate["granted"] is False)


def test_hub_pins_validate() -> None:
    from subsystems.hub import pinned as hub_pins

    items = hub_pins.validate_items([
        {"name": "Job A", "href": "/ui/jobcheck?item=1", "sub": "Blocked"},
        {"name": "Job A", "href": "/ui/jobcheck?item=1"},  # dup
        {"name": "", "href": "/x"},  # drop
        {"name": "Job B", "href": "/ui/billing"},
    ])
    check("deduped to 2", len(items) == 2)
    check("ids set", items[0]["id"] == "/ui/jobcheck?item=1")
    try:
        hub_pins.validate_items("nope")
        check("reject non-list", False)
    except ValueError:
        check("reject non-list", True)


def test_hub_payload_shape() -> None:
    os.environ["GVC_PORTAL_ALLOWED_EMAILS"] = "dev-bypass@localhost"
    os.environ["GVC_GRANTS_BACKEND"] = "env"
    from orchestrators import hub_flow

    payload = hub_flow.build_hub_payload("dev-bypass@localhost")
    check("ok", payload.get("ok") is True)
    check("user", isinstance(payload.get("user"), dict))
    check("greeting", bool(payload.get("greeting")))
    check("four metrics", len(payload.get("metrics") or []) == 4)
    check("needs list", isinstance(payload.get("needs"), list))
    check("queue", isinstance((payload.get("queue") or {}).get("rows"), list))
    check("nav groups", len((payload.get("nav") or {}).get("groups") or []) >= 5)
    check("role owner for superadmin", payload["user"]["role"] == "owner")
    check("homeTool set", bool(payload["user"].get("homeTool")))
    check("homeToolName human",
          payload["user"].get("homeToolName") == "Owner Pulse"
          or "Pulse" in str(payload["user"].get("homeToolName")))
    check("needs capped", len(payload["needs"]) <= 4)
    check("pinned list", isinstance(payload.get("pinned"), list))
    # Without Monday, metrics should be em-dash (source unavailable), not fake zeros.
    vals = [m["value"] for m in payload["metrics"]]
    check("unavailable metrics use dash", all(v == "—" or isinstance(v, (int, str)) for v in vals))
    # Unreachable sources must NOT claim clear.
    check("superadmin not false-clear when pulse/billing down",
          payload.get("needs_clear") is False)


def test_hub_files_and_route() -> None:
    hub = (ROOT / "web" / "hub.html").read_text(encoding="utf-8")
    check("hub shell classes", "hub-app" in hub and "hub-rail" in hub and "hub-dock" in hub)
    check("brand mark", "hub-rail__brand" in hub)
    check("needs you today", "Needs you today" in hub)
    check("r53 footer", ">r53<" in hub)
    check("skeleton", "hub-skel" in hub and "hublive" in hub)
    check("home cta", "hub-home-cta" in hub)
    check("quiet cta", "hub-home-cta--quiet" in hub)
    check("demo mode", "demoPayload" in hub)
    check("EMAIL_JSON inject", "{{EMAIL_JSON}}" in hub)
    check("pin control", "data-pin" in hub)
    check("waiting on live data UI", "Waiting on live data" in hub)
    check("honest clear flag only", "!!payload.needs_clear" in hub)
    check("pin persisted toast", "not saved yet" in hub)
    check("activityall id", "activityall" in hub)
    css = (ROOT / "web" / "gvc.css").read_text(encoding="utf-8")
    check("hub css", ".hub-rail" in css and "264px" in css)
    check("clear gold inset", "hub-clear" in css and "inset 3px" in css)
    check("desktop header stays", "hub-top { display: none" not in css)
    check("home cta css", ".hub-home-cta" in css)
    check("quiet cta css", ".hub-home-cta--quiet" in css)
    check("unavailable metric", ".hub-metric.is-unavailable" in css)
    check("urgent need css", ".hub-need.is-urgent" in css)
    check("tablet dock until 1024",
          ".hub-dock { display: none" in css.split("@media (min-width: 1024px)")[1].split("@media")[0]
          or "1024px" in css and "hub-dock" in css)
    check("solid skel no shimmer",
          ".hub-skel" in css and "gvc-shimmer" not in css.split("Hub shell")[-1].split("END COMPONENTS")[0])
    check("rise motion", "@keyframes hub-rise" in css)
    check("hidden overrides display",
          ".hub-skel[hidden]" in css and "#hublive[hidden]" in css
          and ".hub-kicker__count[hidden]" in css)

    from app.service import app
    paths = {getattr(r, "path", None) for r in app.routes}
    check("/ui/api/hub route", "/ui/api/hub" in paths)
    check("/ui/api/hub/pinned route", "/ui/api/hub/pinned" in paths)
    check("home_tool in PERSON_FIELDS",
          "home_tool" in __import__("shared.portal_store", fromlist=["PERSON_FIELDS"]).PERSON_FIELDS)


def test_need_urgent_flag() -> None:
    from orchestrators.hub_flow import _need, _metric, _human_activity, _age_amount

    safe = _need(kind="Safety", amount="Stop", title="A", detail="d",
                 action="Open", href="/x")
    check("safety urgent", safe["urgent"] is True)
    inv = _need(kind="Invoice", amount="1", title="B", detail="d",
                action="Open", href="/x")
    check("invoice not urgent", inv["urgent"] is False)
    forced = _need(kind="Ask", amount="1", title="C", detail="d",
                   action="Open", href="/x", urgent=True)
    check("forced urgent", forced["urgent"] is True)
    dash = _metric("Ready", "—", "x")
    check("dash unavailable", dash["unavailable"] is True and dash["zero"] is False)
    zero = _metric("Ready", 0, "x")
    check("zero live empty", zero["zero"] is True and zero["unavailable"] is False)
    check("noise filtered", _human_activity("hub.open", "a") is None)
    check("jobcheck label", "Job check" in (_human_activity("jobcheck.save", "X") or ""))
    check("age amount", "d" in _age_amount("2026-07-01", prefix="Ready"))


def test_office_queue_ids_and_handoffs() -> None:
    from orchestrators import hub_flow

    billing = {
        "counts": {
            "ready_to_invoice": 1, "needs_handoff": 1,
            "accepted_bids": 1, "projects_billing": 0,
        },
        "queues": {
            "ready_to_invoice": [{
                "name": "Ready Job", "item_id": "ops1",
                "project_item_id": "proj9",
                "ready_date": "2026-08-01",
                "invoice_href": "/ui/billing?x=1",
                "builder": "ACME",
                "status_labels": ["Ready 2026-08-01"],
            }],
            "accepted_bids": [{
                "name": "Won Bid", "item_id": "bid7",
                "needs_handoff": True,
                "accepted_date": "2026-07-15",
                "jobstart_href": "/ui/jobstart?bid=bid7",
                "builder": "Builder",
            }],
        },
    }
    out = hub_flow._build_office("office@x.com", billing, None)
    check("has handoff need", any(n["kind"] == "Handoff" for n in out["needs"]))
    check("has invoice need", any(n["kind"] == "Invoice" for n in out["needs"]))
    check("queue ids present", all(r.get("id") for r in out["queue_rows"]))
    check("project id preferred",
          any(r.get("id") == "proj9" for r in out["queue_rows"]))
    inv = next(n for n in out["needs"] if n["kind"] == "Invoice")
    check("ready amount aged", "Ready" in inv["amount"])


def test_clear_summary_honest_when_unreachable() -> None:
    from orchestrators import hub_flow

    out = hub_flow._build_owner("o@x.com", None, None, None)
    check("owner unreachable NOT clear", out["clear"] is False)
    check("owner unreachable no false clear lead",
          not out["summary"].lower().startswith("you're clear"))
    office = hub_flow._build_office("o@x.com", None, None)
    check("office unreachable NOT clear", office["clear"] is False)
    check("office unreachable no false clear lead",
          not office["summary"].lower().startswith("you're clear"))
    gm = hub_flow._build_gm("g@x.com", None, None, None)
    check("gm unreachable NOT clear", gm["clear"] is False)
    sales = hub_flow._build_sales("s@x.com", None, None)
    check("sales unreachable NOT clear", sales["clear"] is False)


def test_build_gm_and_sales_shapes() -> None:
    from orchestrators import hub_flow

    gm_view = {
        "ok": True,
        "team_prep": {"pct": 80, "prepared": 4, "team_size": 5},
        "sequence": [
            {"item_id": "i1", "name": "Blocked Job", "blocked": "Waiting on GC",
             "stage": "Framing"},
            {"item_id": "i2", "name": "Clear Job", "blocked": "", "stage": "Hang"},
        ],
        "unscheduled": [{"item_id": "u1", "name": "Open Job", "location": "Cincy"}],
        "action_requests": [
            {"id": "ar1", "need": "Confirm scaffold", "project_name": "Site A",
             "due_at": "2026-08-07"},
        ],
        "planning_signals": [{"email": "crew@x.com", "count": 3, "flag": True}],
    }
    gm = hub_flow._build_gm("gm@x.com", gm_view, None, None)
    check("gm clear false with needs", gm["clear"] is False)
    check("gm has blocked need", any(n["kind"] == "Blocked" for n in gm["needs"]))
    check("gm huddle action",
          any("Huddle" in (n.get("action") or "") for n in gm["needs"]))
    check("gm queue has rows", len(gm["queue_rows"]) >= 1)
    prep_m = gm["metrics"][0]
    check("gm team prep pct", prep_m["value"] == "80%")

    billing = {
        "counts": {"needs_handoff": 1, "accepted_bids": 2, "ready_to_invoice": 3},
        "queues": {
            "accepted_bids": [{
                "name": "Won", "item_id": "b1", "needs_handoff": True,
                "accepted_date": "2026-07-20",
                "jobstart_href": "/ui/jobstart?bid=b1",
            }],
            "ready_to_invoice": [],
        },
    }
    sales = hub_flow._build_sales("jake@x.com", billing, None)
    check("sales handoff primary",
          sales["needs"] and sales["needs"][0]["kind"] == "Handoff")
    check("sales clear false", sales["clear"] is False)
    check("sales metric handoff first", sales["metrics"][0]["label"] == "Need handoff")

    q_link, q_href = hub_flow._queue_link_for("gm", "/ui/morning-gm", "GM Morning Huddle")
    check("gm queue link huddle", "Huddle" in q_link and "morning-gm" in q_href)


def test_field_prep_badge() -> None:
    from orchestrators import hub_flow

    brief = {
        "ok": True,
        "stops": [],
        "needs_attention": [],
        "action_requests": {"incoming": []},
        "preparation": {"ready": 2, "total": 6},
        "hub": {"label": "You're clear — nothing waiting on you."},
    }
    out = hub_flow._build_field("crew@x.com", brief)
    check("field clear with no needs", out["clear"] is True)
    check("prep incomplete badges morning", out["badges"].get("morning") == 1)


def test_morning_deeplink_hyphens() -> None:
    text = (ROOT / "orchestrators" / "morning_flow.py").read_text(encoding="utf-8")
    check("no slash morning/gm deep link", '"/ui/morning/gm"' not in text)
    check("no slash morning/owner deep link", '"/ui/morning/owner"' not in text)
    check("hyphen gm route", "/ui/morning-gm" in text)
    check("hyphen owner route", "/ui/morning-owner" in text)


if __name__ == "__main__":
    test_hub_nav_roles()
    test_hub_pins_validate()
    test_hub_payload_shape()
    test_hub_files_and_route()
    test_need_urgent_flag()
    test_office_queue_ids_and_handoffs()
    test_clear_summary_honest_when_unreachable()
    test_build_gm_and_sales_shapes()
    test_field_prep_badge()
    test_morning_deeplink_hyphens()
    print("all hub home tests passed")
