"""Hub replays the last live numbers on boot — without replaying grants.

The hub shell paints instantly from HUB_BOOT, then waits on /ui/api/hub while
it walks Monday, so the page sits visibly empty. The stash replays the previous
payload's DISPLAY slices over the boot shell.

Two rules this file exists to hold:
  1. Access-controlled data (nav / user / quick_actions) is NEVER stashed —
     replaying a cached rail could show a tile whose grant was revoked.
  2. Replayed numbers are never presented as current — the dateline says when
     they are from. A stale panel that looks live is the failure that hid a
     three-week-old recap in the Takeoff app.

Runs under pytest OR directly: ``python tests/test_hub_stash.py``.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "web" / "hub.html"


def _hub() -> str:
    return HUB.read_text(encoding="utf-8")


def _stash_fields(html: str) -> list[str]:
    m = re.search(r"const STASH_FIELDS = \[(.*?)\];", html, flags=re.DOTALL)
    assert m, "STASH_FIELDS not found in hub.html"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_stash_carries_display_data_only() -> None:
    fields = set(_stash_fields(_hub()))
    assert {"needs", "metrics", "queue"} <= fields, fields
    # The security line: anything grant-derived stays server-owned.
    for forbidden in ("nav", "user", "quick_actions", "features", "badges"):
        assert forbidden not in fields, (
            f"{forbidden!r} must not be stashed — it is access-controlled and "
            "replaying it could show a tool whose grant was revoked"
        )


def test_stash_is_scoped_and_expires() -> None:
    html = _hub()
    assert "raw.email !== EMAIL" in html, "stash must not cross sign-ins"
    assert "STASH_MAX_AGE_MS" in html and "age > STASH_MAX_AGE_MS" in html, (
        "stash must expire so an abandoned tab cannot replay old numbers"
    )
    assert "p.ok === false" in html, "a failed payload must never be stashed"
    assert 'soBtn.addEventListener("click", clearStash)' in html, (
        "sign-out must clear the stash"
    )


def test_replayed_numbers_are_labelled_not_silent() -> None:
    html = _hub()
    assert "showingStash" in html
    assert "as of " in html, "stale numbers must carry an 'as of' stamp"
    assert "refreshing…" in html, "an in-flight refresh must be visible"
    # Live arrival clears the stale state in both hydration paths.
    assert html.count("showingStash = false") >= 2, (
        "both fetchPayload and refreshBadges must clear the stale flag"
    )
    assert "refresh failed" in html, (
        "a failed refresh must say so rather than leaving numbers looking live"
    )


def test_hub_inline_script_still_parses() -> None:
    html = _hub()
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert scripts, "hub has no inline script"
    source = re.sub(r"\{\{[A-Z0-9_]+\}\}", '"gvc-test@localhost"', "\n".join(scripts))
    checked = subprocess.run(
        ["node", "--check", "-"],
        input=source, text=True, capture_output=True, check=False,
    )
    assert checked.returncode == 0, checked.stderr


def test_footer_bumped() -> None:
    assert "Portal <b>r107</b>" in _hub()


if __name__ == "__main__":
    test_stash_carries_display_data_only()
    test_stash_is_scoped_and_expires()
    test_replayed_numbers_are_labelled_not_silent()
    test_hub_inline_script_still_parses()
    test_footer_bumped()
    print("ALL PASSED")
