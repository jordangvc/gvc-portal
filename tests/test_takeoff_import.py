"""
Takeoff -> portal estimate-draft contract tests.

Pure payload normalization is exercised against the repository's canonical
``example_estimate.json``.  The staging flow uses a stub draft store, so this
file runs without GCS or any other external service.

Runs under pytest OR directly: ``python tests/test_takeoff_import.py``.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrators import estimate_flow  # noqa: E402
from orchestrators import takeoff_import_flow  # noqa: E402
from subsystems.estimate import drafts as estimate_drafts  # noqa: E402
from subsystems.estimate import takeoff_import as ti  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def _example() -> dict:
    return json.loads((ROOT / "example_estimate.json").read_text(encoding="utf-8"))


def test_normalize_blanks_legacy_identifier_without_mutating_source():
    raw = _example()
    original = deepcopy(raw)

    normalized = ti.normalize_takeoff_payload(raw)

    assert raw == original
    assert normalized["estimate"]["identifier"] == ""
    assert normalized["client"]["name"] == "Maxwell Construction"


def test_normalize_coerces_prices_quantities_notes_and_strips_top_level_junk():
    raw = _example()
    raw["estimate"]["line_items"][0]["unit_price"] = "$15,200.50"
    raw["estimate"]["line_items"][0]["quantity"] = "2"
    raw["estimate"]["special_notes"] = "First note\n\nSecond note"
    raw["takeoff_internal_cache"] = {"must": "not leak"}

    normalized = ti.normalize_takeoff_payload(raw)

    line = normalized["estimate"]["line_items"][0]
    assert line["unit_price"] == 15200.5
    assert line["quantity"] == 2
    assert normalized["estimate"]["special_notes"] == ["First note", "Second note"]
    assert "takeoff_internal_cache" not in normalized


def test_example_validates_after_identifier_is_blanked():
    normalized = ti.normalize_takeoff_payload(_example())

    assert ti.validate_takeoff_payload(normalized) == []
    estimate_flow.validate(normalized)


def test_validation_rejects_missing_client_email():
    raw = _example()
    raw["client"]["email"] = " "

    errors = ti.validate_takeoff_payload(ti.normalize_takeoff_payload(raw))

    assert any("client.email" in error for error in errors)


def test_validation_rejects_non_numeric_line_price():
    raw = _example()
    raw["estimate"]["line_items"][0]["unit_price"] = "not a price"

    errors = ti.validate_takeoff_payload(ti.normalize_takeoff_payload(raw))

    assert any("Line item 1" in error and "numeric unit_price" in error for error in errors)


def test_request_and_contract_helpers_describe_both_supported_shapes():
    raw = _example()

    assert ti.extract_takeoff_payload(raw) == raw
    assert ti.extract_takeoff_payload({"data": raw}) == raw
    contract = ti.takeoff_contract()
    assert contract["staging"] == "draft_only"
    assert contract["schema"] == "example_estimate.json"
    assert contract["endpoints"]["ui"] == "/ui/api/estimate/from-takeoff"
    assert contract["endpoints"]["automation"] == "/v1/estimate/from-takeoff"
    assert "client.email" in contract["required_fields"]


def test_service_registers_takeoff_staging_routes_without_finalize_mode():
    from app.service import app

    routes = {
        (route.path, method)
        for route in app.routes
        for method in (route.methods or set())
    }
    assert ("/ui/api/estimate/from-takeoff", "POST") in routes
    assert ("/v1/estimate/from-takeoff", "POST") in routes
    assert ("/ui/api/estimate/takeoff-contract", "GET") in routes


def test_generated_draft_record_has_safe_takeoff_id_and_blank_identifier():
    record = ti.build_draft_record(_example(), "jake@greenvalleycontractors.com")

    assert record["id"].startswith("takeoff-")
    assert estimate_drafts.valid_draft_id(record["id"])
    assert record["label"].startswith("Maxwell Construction")
    assert record["payload"]["estimate"]["identifier"] == ""
    assert record["actor"] == "jake@greenvalleycontractors.com"
    assert record["updated_at"].endswith("+00:00")


def test_flow_stages_one_shared_draft_and_never_finalizes():
    calls = []
    original = takeoff_import_flow.estimate_drafts.upsert_draft

    def fake_upsert(draft_id, *, label, payload, updated_at, actor):
        calls.append({
            "draft_id": draft_id,
            "label": label,
            "payload": payload,
            "updated_at": updated_at,
            "actor": actor,
        })
        return {
            "id": draft_id,
            "label": label,
            "payload": payload,
            "updated_at": updated_at,
            "updated_by": actor,
        }, False

    takeoff_import_flow.estimate_drafts.upsert_draft = fake_upsert
    try:
        out = takeoff_import_flow.import_takeoff_as_draft(
            _example(), "jake@greenvalleycontractors.com"
        )
    finally:
        takeoff_import_flow.estimate_drafts.upsert_draft = original

    assert out["ok"] is True
    assert out["draft"]["payload"]["estimate"]["identifier"] == ""
    assert len(calls) == 1
    assert calls[0]["actor"] == "jake@greenvalleycontractors.com"
    assert "mode" not in calls[0] and "finalize" not in calls[0]
    assert any("identifier" in warning.lower() for warning in out["warnings"])


def test_flow_returns_clear_error_when_draft_store_is_missing():
    original = takeoff_import_flow.estimate_drafts.upsert_draft

    def unavailable(*args, **kwargs):
        raise estimate_drafts.PortalStoreNotConfigured("state bucket missing")

    takeoff_import_flow.estimate_drafts.upsert_draft = unavailable
    try:
        out = takeoff_import_flow.import_takeoff_as_draft(
            _example(), "automation:takeoff"
        )
    finally:
        takeoff_import_flow.estimate_drafts.upsert_draft = original

    assert out["ok"] is False
    assert out["code"] == "STORE_NOT_CONFIGURED"
    assert "state bucket missing" in out["detail"]


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
