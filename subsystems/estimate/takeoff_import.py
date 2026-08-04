"""Pure Takeoff -> portal estimate-draft contract helpers.

Takeoff exports the canonical ``example_estimate.json`` shape.  Imports are
normalized into a fresh estimate payload and staged as drafts only; this module
does not perform I/O or finalize an estimate.
"""
from __future__ import annotations

import math
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from subsystems.estimate.number import ESTIMATE_NUMBER_RE


CANONICAL_TOP_LEVEL_KEYS = ("prepared_by", "client", "job", "estimate", "company")
REQUIRED_FIELDS = (
    "client.name",
    "client.email",
    "job.name",
    "estimate.line_items[].description",
    "estimate.line_items[].unit_price",
)


class TakeoffPayloadInvalid(ValueError):
    """Raised when a normalized Takeoff export cannot become an estimate draft."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _mapping(value: Any) -> dict:
    return deepcopy(value) if isinstance(value, dict) else {}


def _coerce_number(value: Any) -> int | float | Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    if not isinstance(value, str):
        return value
    cleaned = value.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return value.strip()
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _normalize_notes(value: Any) -> list[str]:
    if value is None:
        return []
    values = value.splitlines() if isinstance(value, str) else value
    if not isinstance(values, list):
        values = [values]
    return [str(item).strip() for item in values
            if item is not None and str(item).strip()]


def _strip_string_fields(section: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        if isinstance(section.get(field), str):
            section[field] = section[field].strip()


def _has_text(value: Any) -> bool:
    return value is not None and bool(str(value).strip())


def normalize_takeoff_payload(raw: Any) -> dict:
    """Return a deep-copied canonical estimate payload.

    Unsupported top-level fields are intentionally dropped.  Every supplied
    estimate identifier is cleared so a person finalizing the reviewed draft
    receives a fresh portal-assigned ``YYYY-MMDD-NNN``.
    """
    source = raw if isinstance(raw, dict) else {}
    normalized = {
        key: _mapping(source.get(key))
        for key in CANONICAL_TOP_LEVEL_KEYS
        if key != "company" or isinstance(source.get(key), dict)
    }
    normalized.setdefault("prepared_by", {})
    normalized.setdefault("client", {})
    normalized.setdefault("job", {})
    normalized.setdefault("estimate", {})

    _strip_string_fields(normalized["prepared_by"], ("name", "email", "phone"))
    _strip_string_fields(
        normalized["client"], ("name", "contact_name", "email", "phone")
    )
    _strip_string_fields(
        normalized["job"],
        ("name", "location", "scope_summary", "project_type"),
    )

    estimate = normalized["estimate"]
    _strip_string_fields(estimate, ("identifier", "date", "expiry_date", "notes"))
    # A Takeoff import is always a NEW estimate draft, never a revision.  Even a
    # syntactically valid portal number must not survive staging: the portal
    # assigns the next number only after a person reviews and finalizes it.
    estimate["identifier"] = ""

    raw_items = estimate.get("line_items")
    if not isinstance(raw_items, list):
        raw_items = []
    items: list[dict] = []
    for raw_item in raw_items:
        item = _mapping(raw_item)
        _strip_string_fields(
            item,
            ("description", "detail", "scope_key", "scope_trade",
             "scope_title", "scope_detail"),
        )
        item["unit_price"] = _coerce_number(item.get("unit_price"))
        quantity = _coerce_number(item.get("quantity", 1))
        item["quantity"] = (
            quantity
            if isinstance(quantity, (int, float)) and not isinstance(quantity, bool)
            and quantity > 0
            else 1
        )
        if "optional" in item:
            item["optional"] = bool(item["optional"])
        items.append(item)
    estimate["line_items"] = items
    estimate["special_notes"] = _normalize_notes(estimate.get("special_notes"))
    return normalized


def validate_takeoff_payload(data: Any) -> list[str]:
    """Return actionable validation errors aligned with estimate_flow.validate."""
    if not isinstance(data, dict):
        return ["Estimate payload must be an object."]
    client = data.get("client") if isinstance(data.get("client"), dict) else {}
    job = data.get("job") if isinstance(data.get("job"), dict) else {}
    estimate = (
        data.get("estimate") if isinstance(data.get("estimate"), dict) else {}
    )
    errors: list[str] = []
    if not _has_text(client.get("name")):
        errors.append("client.name is required.")
    if not _has_text(client.get("email")):
        errors.append("client.email is required (used for the Gmail draft).")
    if not _has_text(job.get("name")):
        errors.append("job.name (project name) is required.")

    identifier = estimate.get("identifier") or ""
    if identifier and (
        not isinstance(identifier, str)
        or not ESTIMATE_NUMBER_RE.fullmatch(identifier.strip())
    ):
        errors.append(
            f"estimate.identifier {identifier!r} is not EST-YYYY-MMDD-NNN "
            "(leave it blank to auto-assign)."
        )

    items = estimate.get("line_items")
    if not isinstance(items, list) or not items:
        errors.append("At least one line item is required.")
        return errors
    for index, item in enumerate(items, 1):
        item = item if isinstance(item, dict) else {}
        if not str(item.get("description") or "").strip():
            errors.append(f"Line item {index}: description is required.")
        price = item.get("unit_price")
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(float(price))
        ):
            errors.append(
                f"Line item {index}: a numeric unit_price is required."
            )
    return errors


def draft_label(data: dict) -> str:
    client = str(((data.get("client") or {}).get("name") or "")).strip()
    job = str(((data.get("job") or {}).get("name") or "")).strip()
    return " — ".join(part for part in (client, job) if part) or "Takeoff estimate"


def new_draft_id() -> str:
    return f"takeoff-{uuid.uuid4().hex}"


def build_draft_record(data: Any, actor: str) -> dict:
    payload = normalize_takeoff_payload(data)
    return {
        "id": new_draft_id(),
        "label": draft_label(payload),
        "payload": payload,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "actor": (actor or "").strip() or "unknown",
    }


def normalization_warnings(raw: Any, data: dict) -> list[str]:
    warnings: list[str] = []
    source = raw if isinstance(raw, dict) else {}
    raw_estimate = source.get("estimate")
    raw_identifier = (
        raw_estimate.get("identifier")
        if isinstance(raw_estimate, dict)
        else ""
    )
    normalized_identifier = (data.get("estimate") or {}).get("identifier")
    if raw_identifier and not normalized_identifier:
        warnings.append(
            f"Cleared supplied estimate identifier {raw_identifier!r}; the "
            "portal will auto-assign a fresh number at finalize."
        )
    unknown = sorted(set(source) - set(CANONICAL_TOP_LEVEL_KEYS))
    if unknown:
        warnings.append(
            "Ignored unsupported top-level field(s): " + ", ".join(unknown) + "."
        )
    return warnings


def extract_takeoff_payload(body: Any) -> Any:
    """Accept either ``{"data": <estimate>}`` or the raw estimate object."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body


def takeoff_contract() -> dict:
    """Machine-readable hints for Takeoff exporters and automation clients."""
    return {
        "version": 1,
        "schema": "example_estimate.json",
        "staging": "draft_only",
        "accepted_body_shapes": ["raw_estimate_object", "data_wrapped_object"],
        "required_fields": list(REQUIRED_FIELDS),
        "identifier_policy": (
            "Leave estimate.identifier blank. Legacy EST-* values are cleared; "
            "the portal assigns YYYY-MMDD-NNN only when a person finalizes."
        ),
        "endpoints": {
            "ui": "/ui/api/estimate/from-takeoff",
            "automation": "/v1/estimate/from-takeoff",
            "contract": "/ui/api/estimate/takeoff-contract",
        },
    }
