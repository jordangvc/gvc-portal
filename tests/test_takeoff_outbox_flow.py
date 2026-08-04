"""
Takeoff outbox poller tests — Phase 2 consumer.

RTDB HTTP and the shared draft store are stubbed at the flow's module seams
(_bearer_token / _fetch_queued / _ack, estimate_drafts.upsert_draft), so this
file runs without Firebase, google-auth credentials, GCS, or any network.

Runs under pytest OR directly: ``python tests/test_takeoff_outbox_flow.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrators import takeoff_outbox_flow as flow  # noqa: E402
from subsystems.estimate import drafts as estimate_drafts  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def _example() -> dict:
    return json.loads((ROOT / "example_estimate.json").read_text(encoding="utf-8"))


def _entry(status: str = "queued", **overrides) -> dict:
    entry = {
        "draftId": "abc123XYZ",
        "estimate": _example(),
        "status": status,
        "queuedAt": "2026-08-04T12:00:00+00:00",
        "queuedBy": "melvin@greenvalleycontractors.com",
        "bidTotal": 15200.5,
        "source": "takeoff-v2",
    }
    entry.update(overrides)
    return entry


class _Harness:
    """Swap the flow's I/O seams for in-memory fakes; restore on exit."""

    def __init__(self, entries: dict, upsert=None):
        self.entries = entries
        self.acks: list[tuple[str, dict]] = []
        self.upserts: list[dict] = []
        self._upsert_override = upsert

    def __enter__(self):
        self._orig_flow = {
            "_bearer_token": flow._bearer_token,
            "_fetch_queued": flow._fetch_queued,
            "_ack": flow._ack,
        }
        self._orig_upsert = estimate_drafts.upsert_draft
        flow._bearer_token = lambda: "test-token"
        flow._fetch_queued = lambda token, limit: dict(
            list(self.entries.items())[:limit]
        )
        flow._ack = lambda token, outbox_id, fields: self.acks.append(
            (outbox_id, fields)
        )

        def default_upsert(draft_id, *, label, payload, updated_at, actor):
            record = {"id": draft_id, "label": label, "payload": payload,
                      "updated_at": updated_at, "updated_by": actor}
            self.upserts.append(record)
            return record, False

        estimate_drafts.upsert_draft = self._upsert_override or default_upsert
        return self

    def __exit__(self, *exc_info):
        for name, fn in self._orig_flow.items():
            setattr(flow, name, fn)
        estimate_drafts.upsert_draft = self._orig_upsert
        return False


# ---------------------------------------------------------------------------
# portal_draft_id — deterministic sanitizer
# ---------------------------------------------------------------------------

def test_portal_draft_id_passes_clean_ids_through_deterministically():
    assert flow.portal_draft_id("abc123XYZ") == "takeoff-abc123XYZ"
    assert flow.portal_draft_id("abc123XYZ") == flow.portal_draft_id("abc123XYZ")
    assert estimate_drafts.valid_draft_id(flow.portal_draft_id("abc123XYZ"))


def test_portal_draft_id_truncates_long_ids_with_stable_hash_suffix():
    long_id = "x" * 100
    first = flow.portal_draft_id(long_id)
    assert first == flow.portal_draft_id(long_id)
    assert len(first) <= 64
    assert estimate_drafts.valid_draft_id(first)
    # Two long ids sharing the same 55-char prefix must still stay distinct.
    sibling = flow.portal_draft_id("x" * 99 + "y")
    assert sibling != first


def test_portal_draft_id_replaces_bad_chars_without_collisions():
    slashed = flow.portal_draft_id("a/b c")
    assert estimate_drafts.valid_draft_id(slashed)
    assert slashed == flow.portal_draft_id("a/b c")
    # Different raw ids that sanitize to the same base must not collide —
    # the hash suffix comes from the ORIGINAL id.
    assert flow.portal_draft_id("a/b c") != flow.portal_draft_id("a?b c")
    # Even an empty id yields a valid (if useless) store-safe id.
    assert estimate_drafts.valid_draft_id(flow.portal_draft_id(""))


# ---------------------------------------------------------------------------
# poll_outbox — staging, filtering, acks, isolation
# ---------------------------------------------------------------------------

def test_poll_stages_queued_entry_and_acks_staged():
    with _Harness({"abc123XYZ": _entry()}) as h:
        out = flow.poll_outbox()

    assert set(out) == {"ok", "dry_run", "checked", "staged", "skipped", "errors"}
    assert out["ok"] is True and out["staged"] == 1 and out["errors"] == []
    assert len(h.upserts) == 1
    stored = h.upserts[0]
    assert stored["id"] == "takeoff-abc123XYZ"
    assert stored["payload"]["estimate"]["identifier"] == ""  # never a revision
    assert stored["updated_by"] == "outbox:melvin@greenvalleycontractors.com"
    assert len(h.acks) == 1
    outbox_id, fields = h.acks[0]
    assert outbox_id == "abc123XYZ"
    assert fields["status"] == "staged"
    assert fields["portalDraftId"] == "takeoff-abc123XYZ"
    assert fields["stagedAt"]  # ISO timestamp present


def test_poll_only_consumes_queued_status():
    entries = {
        "queued-entry-1": _entry(),
        "staged-entry-1": _entry(status="staged"),
        "error-entry-01": _entry(status="error"),
    }
    with _Harness(entries) as h:
        out = flow.poll_outbox()

    assert out["staged"] == 1 and out["skipped"] == 2
    assert [u["id"] for u in h.upserts] == ["takeoff-queued-entry-1"]
    assert [a[0] for a in h.acks] == ["queued-entry-1"]


def test_invalid_payload_acks_error_and_never_touches_draft_store():
    bad = _entry()
    bad["estimate"]["client"]["email"] = " "
    with _Harness({"bad-entry-0001": bad}) as h:
        out = flow.poll_outbox()

    assert out["staged"] == 0 and h.upserts == []
    assert len(out["errors"]) == 1
    assert out["errors"][0]["draftId"] == "bad-entry-0001"
    assert "client.email" in out["errors"][0]["error"]
    outbox_id, fields = h.acks[0]
    assert outbox_id == "bad-entry-0001"
    assert fields["status"] == "error"
    assert "client.email" in fields["error"]
    assert fields["processedAt"]


def test_one_bad_entry_never_kills_the_sweep():
    def flaky_upsert(draft_id, *, label, payload, updated_at, actor):
        if draft_id == "takeoff-broken-000001":
            raise RuntimeError("draft store hiccup")
        return {"id": draft_id, "label": label, "payload": payload,
                "updated_at": updated_at, "updated_by": actor}, False

    entries = {"broken-000001": _entry(), "healthy-00001": _entry()}
    with _Harness(entries, upsert=flaky_upsert) as h:
        out = flow.poll_outbox()

    assert out["checked"] == 2 and out["staged"] == 1
    assert [a[0] for a in h.acks] == ["healthy-00001"]
    assert len(out["errors"]) == 1
    assert out["errors"][0]["draftId"] == "broken-000001"
    assert "draft store hiccup" in out["errors"][0]["error"]


def test_dry_run_reports_without_writing_anywhere():
    bad = _entry()
    bad["estimate"]["client"]["email"] = " "
    entries = {"queued-entry-1": _entry(), "bad-entry-0001": bad}
    with _Harness(entries) as h:
        out = flow.poll_outbox(dry_run=True)

    assert out["dry_run"] is True
    assert out["staged"] == 1 and len(out["errors"]) == 1
    assert h.upserts == [] and h.acks == []


def test_missing_draft_store_aborts_sweep_with_code():
    def unavailable(*args, **kwargs):
        raise estimate_drafts.PortalStoreNotConfigured("state bucket missing")

    with _Harness({"queued-entry-1": _entry()}, upsert=unavailable) as h:
        out = flow.poll_outbox()

    assert out["ok"] is False
    assert out["code"] == "STORE_NOT_CONFIGURED"
    assert h.acks == []  # entry stays "queued" for the next sweep


def test_unsafe_outbox_key_is_reported_but_never_written_back():
    with _Harness({"evil/../key": _entry()}) as h:
        out = flow.poll_outbox()

    assert out["staged"] == 0 and h.upserts == [] and h.acks == []
    assert out["errors"][0]["draftId"] == "evil/../key"
    assert "unsafe outbox key" in out["errors"][0]["error"]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failed} failed" if failed else "\nall tests passed")
    sys.exit(1 if failed else 0)
