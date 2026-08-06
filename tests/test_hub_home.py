"""Pure tests for the personal hub home (nav + payload shape).

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

    groups = hub_nav.groups_for_client({"morning", "jobcheck", "fieldguide", "timeoff"})
    names = [g["name"] for g in groups]
    check("Today group first", names[0] == "Today")
    morning = next(t for g in groups for t in g["tools"] if t["feature"] == "morning")
    estimate = next(t for g in groups for t in g["tools"] if t["feature"] == "estimate")
    check("granted morning", morning["granted"] is True)
    check("dim estimate", estimate["granted"] is False)


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
    check("needs capped", len(payload["needs"]) <= 4)


def test_hub_files_and_route() -> None:
    hub = (ROOT / "web" / "hub.html").read_text(encoding="utf-8")
    check("hub shell classes", "hub-app" in hub and "hub-rail" in hub and "hub-dock" in hub)
    check("needs you today", "Needs you today" in hub)
    check("r44 footer", ">r44<" in hub)
    check("EMAIL_JSON inject", "{{EMAIL_JSON}}" in hub)
    css = (ROOT / "web" / "gvc.css").read_text(encoding="utf-8")
    check("hub css", ".hub-rail" in css and "264px" in css)

    from app.service import app
    paths = {getattr(r, "path", None) for r in app.routes}
    check("/ui/api/hub route", "/ui/api/hub" in paths)


if __name__ == "__main__":
    test_hub_nav_roles()
    test_hub_payload_shape()
    test_hub_files_and_route()
    print("ALL PASSED")
