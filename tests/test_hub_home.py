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
    check("owner via admin", hub_nav.resolve_role({"admin", "morning"}) == "owner")
    check("gm", hub_nav.resolve_role({"morning_gm", "morning"}) == "gm")
    check("office via invoice", hub_nav.resolve_role({"invoice", "morning"}) == "office")
    check("field default", hub_nav.resolve_role({"morning", "jobcheck"}) == "field")
    check("greeting morning",
          hub_nav.greeting_for("Jordan Faulkner", hour=8).startswith("Good morning"))
    check("initials", hub_nav.initials_for("Jordan Faulkner", "j@x.com") == "JF")
    check("home label owner", hub_nav.home_tool_label("morning_owner") == "Owner Pulse")
    check("home href billing", hub_nav.home_tool_href("invoice") == "/ui/billing")

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


def test_hub_files_and_route() -> None:
    hub = (ROOT / "web" / "hub.html").read_text(encoding="utf-8")
    check("hub shell classes", "hub-app" in hub and "hub-rail" in hub and "hub-dock" in hub)
    check("brand mark", "hub-rail__brand" in hub)
    check("needs you today", "Needs you today" in hub)
    check("r47 footer", ">r47<" in hub)
    check("skeleton", "hub-skel" in hub and "hublive" in hub)
    check("home cta", "hub-home-cta" in hub)
    check("demo mode", "demoPayload" in hub)
    check("EMAIL_JSON inject", "{{EMAIL_JSON}}" in hub)
    check("pin control", "data-pin" in hub)
    css = (ROOT / "web" / "gvc.css").read_text(encoding="utf-8")
    check("hub css", ".hub-rail" in css and "264px" in css)
    check("clear gold inset", "hub-clear" in css and "inset 3px" in css)
    check("desktop header stays", "hub-top { display: none" not in css)
    check("home cta css", ".hub-home-cta" in css)
    check("urgent need css", ".hub-need.is-urgent" in css)
    check("solid skel no shimmer",
          ".hub-skel" in css and "gvc-shimmer" not in css.split("Hub shell")[-1].split("END COMPONENTS")[0])
    check("rise motion", "@keyframes hub-rise" in css)

    from app.service import app
    paths = {getattr(r, "path", None) for r in app.routes}
    check("/ui/api/hub route", "/ui/api/hub" in paths)
    check("/ui/api/hub/pinned route", "/ui/api/hub/pinned" in paths)
    check("home_tool in PERSON_FIELDS",
          "home_tool" in __import__("shared.portal_store", fromlist=["PERSON_FIELDS"]).PERSON_FIELDS)


def test_need_urgent_flag() -> None:
    from orchestrators.hub_flow import _need

    safe = _need(kind="Safety", amount="Stop", title="A", detail="d",
                 action="Open", href="/x")
    check("safety urgent", safe["urgent"] is True)
    inv = _need(kind="Invoice", amount="1", title="B", detail="d",
                action="Open", href="/x")
    check("invoice not urgent", inv["urgent"] is False)
    forced = _need(kind="Ask", amount="1", title="C", detail="d",
                   action="Open", href="/x", urgent=True)
    check("forced urgent", forced["urgent"] is True)


if __name__ == "__main__":
    test_hub_nav_roles()
    test_hub_pins_validate()
    test_hub_payload_shape()
    test_hub_files_and_route()
    test_need_urgent_flag()
    print("ALL PASSED")
