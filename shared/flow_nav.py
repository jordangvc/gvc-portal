"""
Money-spine navigation — Hub ↔ Takeoff ↔ Estimate ↔ Job Start ↔ Job Check ↔ Billing.
=========================================================================
Shared step list for the portal Path strip (web/gvc-flow.js) and tests.
Takeoff the *app* still lives on Netlify; the portal owns `/ui/takeoff` as the
in-portal launch + return surface so the rail never dumps people off-domain
with no way back.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

# Canonical Takeoff app URL (Netlify). Query params `return` / `from=portal`
# are appended by the launcher / GvcFlow.takeoffAppUrl() for a future
# Takeoff-side "Back to Portal" control — harmless if Takeoff ignores them.
TAKEOFF_APP_URL = "https://gvctakeoff.netlify.app/v2.html"

# (id, label, portal href) — order is the money spine.
MONEY_SPINE: tuple[tuple[str, str, str], ...] = (
    ("hub", "Hub", "/"),
    ("takeoff", "Takeoff", "/ui/takeoff"),
    ("estimate", "Estimate", "/ui/estimate"),
    ("jobstart", "Job Start", "/ui/jobstart"),
    ("jobcheck", "Job Check", "/ui/jobcheck"),
    ("billing", "Billing", "/ui/billing"),
)


def spine_steps() -> list[dict[str, Any]]:
    """Client-shaped list for API/tests."""
    return [
        {"id": sid, "label": label, "href": href}
        for sid, label, href in MONEY_SPINE
    ]


def spine_ids() -> tuple[str, ...]:
    return tuple(sid for sid, _label, _href in MONEY_SPINE)


def takeoff_app_url(*, portal_origin: str = "") -> str:
    """Netlify Takeoff URL with optional portal return hints."""
    origin = (portal_origin or "").rstrip("/")
    if not origin:
        return TAKEOFF_APP_URL
    qs = urlencode({"return": f"{origin}/", "from": "portal"})
    sep = "&" if "?" in TAKEOFF_APP_URL else "?"
    return f"{TAKEOFF_APP_URL}{sep}{qs}"
