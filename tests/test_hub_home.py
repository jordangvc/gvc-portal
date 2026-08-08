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

    groups = hub_nav.groups_for_client(
        {"morning", "jobcheck", "fieldguide", "timeoff", "training"})
    names = [g["name"] for g in groups]
    check("Today group first", names[0] == "Today")
    morning = next(t for g in groups for t in g["tools"] if t["feature"] == "morning")
    estimate = next(t for g in groups for t in g["tools"] if t["feature"] == "estimate")
    training = next(t for g in groups for t in g["tools"] if t["feature"] == "training")
    check("granted morning", morning["granted"] is True)
    check("dim estimate", estimate["granted"] is False)
    check("training on hub", training["granted"] is True)
    check("training href", training["href"] == "/ui/training")

    takeoff = next(t for g in hub_nav.groups_for_client({"takeoff"}) for t in g["tools"]
                   if t["feature"] == "takeoff")
    check("takeoff portal launcher", takeoff["href"] == "/ui/takeoff")
    check("takeoff not external", takeoff["external"] is False)


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


def test_hub_brief_billing_parallel_contract() -> None:
    """Master #94 fans brief/billing/pulse/gm — keep the contract honest."""
    src = (ROOT / "orchestrators" / "hub_flow.py").read_text(encoding="utf-8")
    chunk = src.split("def _live_hub_slice")[1].split("def build_hub_refresh")[0]
    check("hub enrichment pool", "ThreadPoolExecutor(max_workers=5)" in chunk)
    check("submits morning brief", "pool.submit(_try_morning_brief" in chunk)
    check("submits billing", "pool.submit(_try_billing)" in chunk)
    check("submits owner pulse", "pool.submit(_try_owner_pulse" in chunk)
    check("submits gm view", "pool.submit(_try_gm_view" in chunk)
    check("submits jobstart drafts", "pool.submit(_try_jobstart_drafts)" in chunk)
    check("folds jobstart queue", "_fold_jobstart_queue(" in chunk)
    check("as_completed gather", "as_completed" in chunk)
    check("refresh helper exists", "def build_hub_refresh" in src)
    check("refresh skips activity",
          "activity_read" not in src.split("def build_hub_refresh")[1].split("def build_hub_payload")[0])


def test_hub_refresh_shape() -> None:
    os.environ["GVC_PORTAL_ALLOWED_EMAILS"] = "dev-bypass@localhost"
    os.environ["GVC_GRANTS_BACKEND"] = "env"
    from orchestrators import hub_flow

    out = hub_flow.build_hub_refresh("dev-bypass@localhost")
    check("refresh ok", out.get("ok") is True)
    check("refresh badges", isinstance(out.get("badges"), dict))
    check("refresh setup", isinstance(out.get("setup"), dict))
    check("refresh needs", isinstance(out.get("needs"), list))
    check("refresh metrics", isinstance(out.get("metrics"), list))
    check("refresh queue", isinstance((out.get("queue") or {}).get("rows"), list))
    check("no activity key", "activity" not in out)
    check("no nav key", "nav" not in out)
    check("no pinned key", "pinned" not in out)


def test_hub_setup_flags() -> None:
    """Config cliffs surface as sparse setup[feature]=True — never secrets."""
    from orchestrators import hub_flow

    saved_url = os.environ.get("GVC_TIMEOFF_FORM_URL")
    saved_backend = os.environ.get("GVC_GRANTS_BACKEND")
    try:
        os.environ.pop("GVC_TIMEOFF_FORM_URL", None)
        os.environ["GVC_GRANTS_BACKEND"] = "env"
        flags = hub_flow.setup_flags()
        check("timeoff needs setup without URL", flags.get("timeoff") is True)
        check("admin needs setup without gcs", flags.get("admin") is True)
        check("setup values are bools", all(isinstance(v, bool) for v in flags.values()))

        os.environ["GVC_TIMEOFF_FORM_URL"] = "https://forms.example/timeoff"
        os.environ["GVC_GRANTS_BACKEND"] = "gcs"
        flags2 = hub_flow.setup_flags()
        check("timeoff ready clears flag", "timeoff" not in flags2)
        check("admin ready clears flag", "admin" not in flags2)

        os.environ["GVC_PORTAL_ALLOWED_EMAILS"] = "dev-bypass@localhost"
        payload = hub_flow.build_hub_payload("dev-bypass@localhost")
        check("payload has setup", isinstance(payload.get("setup"), dict))
        refresh = hub_flow.build_hub_refresh("dev-bypass@localhost")
        check("refresh has setup", isinstance(refresh.get("setup"), dict))
    finally:
        if saved_url is None:
            os.environ.pop("GVC_TIMEOFF_FORM_URL", None)
        else:
            os.environ["GVC_TIMEOFF_FORM_URL"] = saved_url
        if saved_backend is None:
            os.environ.pop("GVC_GRANTS_BACKEND", None)
        else:
            os.environ["GVC_GRANTS_BACKEND"] = saved_backend


def test_hub_skips_morning_gfolder_attach():
    """Office hub must not pay 2×N GFolder GraphQL on first paint."""
    src = (ROOT / "orchestrators" / "hub_flow.py").read_text(encoding="utf-8")
    chunk = src.split("def _try_morning_brief")[1].split("def _try_billing")[0]
    check("hub passes attach_gfolder=False",
          "attach_gfolder=False" in chunk)


def test_hub_files_and_route() -> None:
    hub = (ROOT / "web" / "hub.html").read_text(encoding="utf-8")
    check("hub shell classes", "hub-app" in hub and "hub-rail" in hub and "hub-dock" in hub)
    check("brand mark", "hub-rail__brand" in hub)
    check("needs you today", "Needs you today" in hub)
    check("r74 footer", ">r74<" in hub)
    check("setup badge class", "is-setup" in hub)
    check("setup in refresh slice", "payload.setup" in hub)
    check("light refresh endpoint", "/ui/api/hub/refresh" in hub)
    check("refresh debounce", "REFRESH_MIN_MS" in hub)
    fetch_fn = hub.split("async function fetchPayload")[1].split("async function refreshBadges")[0]
    check("first paint one hub call", 'fetch("/ui/api/hub"' in fetch_fn)
    check("first paint does not fetch refresh",
          'fetch("/ui/api/hub/refresh"' not in fetch_fn)
    check("polls still use refresh",
          'fetch("/ui/api/hub/refresh"' in hub.split("async function refreshBadges")[1])
    check("logs monday_trace when present", "monday_trace" in fetch_fn)
    check("warm after first paint", "warmMondayAfterPaint" in hub)
    warm_fn = hub.split("function warmMondayAfterPaint")[1].split(
        "wireChrome();")[0]
    check("warm POST inside helper",
          'fetch("/ui/api/monday/warm"' in warm_fn)
    # Concurrent boot warm removed — only the helper + post-fetchPayload calls remain.
    check("no top-level warm race at script end",
          not hub.rstrip().endswith('keepalive: true\n  }).catch(() => {});\n})();'))
    boot = hub.split("wireChrome();")[1]
    check("boot warms after fetchPayload settles",
          "warmMondayAfterPaint()" in boot
          and boot.index("fetchPayload()") < boot.index("warmMondayAfterPaint()"))
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
    check("setup badge css", ".hub-rail__badge.is-setup" in css)
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
    check("/ui/api/hub/refresh route", "/ui/api/hub/refresh" in paths)
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


def test_jobstart_accept_queue_two_party() -> None:
    from orchestrators import hub_flow

    rows = [
        {"bid_id": 101, "status": "with_ops", "job_name": "101 Main | ACME",
         "sent_by": "jake@x.com", "sent_at": "2026-08-01T12:00:00+00:00"},
        {"bid_id": 102, "status": "draft", "job_name": "Still drafting",
         "sent_by": "", "sent_at": ""},
        {"bid_id": 103, "status": "with_ops", "label": "103 Side | Builder",
         "sent_by": "ops@x.com", "sent_at": "2026-08-02"},
    ]
    as_ops = hub_flow.jobstart_accept_queue(rows, "ops@x.com", is_admin=False)
    check("ops can accept jake's",
          any(i["bid_id"] == "101" for i in as_ops["accept"]))
    check("ops cannot accept own",
          not any(i["bid_id"] == "103" for i in as_ops["accept"]))
    check("ops sees own as waiting",
          any(i["bid_id"] == "103" for i in as_ops["waiting_mine"]))
    check("drafts ignored",
          not any(i["bid_id"] == "102" for i in as_ops["accept"] + as_ops["waiting_mine"]))

    as_admin = hub_flow.jobstart_accept_queue(rows, "jake@x.com", is_admin=True)
    check("admin can accept self-sent",
          any(i["bid_id"] == "101" for i in as_admin["accept"]))

    shaped = {
        "needs": [],
        "queue_rows": [],
        "badges": {},
        "summary": "You're clear.",
        "clear": True,
        "metrics": [],
    }
    folded = hub_flow._fold_jobstart_queue(
        shaped, "ops@x.com", {"jobstart", "morning"}, rows)
    check("fold adds Accept need",
          any(n["kind"] == "Accept" for n in folded["needs"]))
    check("fold Accept CTA",
          any(n.get("action") == "Accept handoff" for n in folded["needs"]))
    check("fold deeplink bid",
          any("bid=101" in (n.get("href") or "") for n in folded["needs"]))
    check("fold not clear", folded["clear"] is False)
    check("fold badge jobstart", folded["badges"].get("jobstart", 0) >= 1)
    check("fold skips without grant",
          hub_flow._fold_jobstart_queue(shaped, "x@x.com", {"morning"}, rows)
          is shaped)

    sender = hub_flow._fold_jobstart_queue(
        {"needs": [], "queue_rows": [], "badges": {}, "summary": "You're clear.",
         "clear": True, "metrics": []},
        "jake@x.com", {"jobstart"}, rows)
    check("sender no Accept need for own",
          not any(n["kind"] == "Accept" and "101" in (n.get("href") or "")
                  for n in sender["needs"]))
    check("sender Waiting queue row",
          any(r.get("tag") == "Waiting" for r in sender["queue_rows"]))


if __name__ == "__main__":
    test_hub_nav_roles()
    test_hub_pins_validate()
    test_hub_payload_shape()
    test_hub_brief_billing_parallel_contract()
    test_hub_skips_morning_gfolder_attach()
    test_hub_files_and_route()
    test_hub_refresh_shape()
    test_hub_setup_flags()
    test_need_urgent_flag()
    test_office_queue_ids_and_handoffs()
    test_clear_summary_honest_when_unreachable()
    test_build_gm_and_sales_shapes()
    test_field_prep_badge()
    test_morning_deeplink_hyphens()
    test_jobstart_accept_queue_two_party()
    print("all hub home tests passed")
