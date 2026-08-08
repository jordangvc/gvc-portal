"""Validate Field Guide procedures + manifest (pure; raises ValueError)."""
from __future__ import annotations

from typing import Any

from subsystems.fieldguide.schema import (
    GOVERNANCE_STATUSES,
    normalize_procedure,
)


REQUIRED_PROCEDURE_FIELDS = (
    "id", "title", "trade", "summary", "steps",
)


def validate_procedure(raw: dict, *, require_approved_complete: bool = False) -> dict:
    """Normalize + validate one procedure. Returns canonical dict."""
    proc = normalize_procedure(raw)
    errors: list[str] = []
    for key in REQUIRED_PROCEDURE_FIELDS:
        if not proc.get(key):
            errors.append(f"missing required field: {key}")
    if not proc["id"].replace("-", "").replace("_", "").isalnum():
        errors.append(f"id must be slug-like: {proc['id']!r}")
    step_ids: set[str] = set()
    for step in proc["steps"]:
        if not step["text"]:
            errors.append(f"step {step['id']} has empty text")
        if step["id"] in step_ids:
            errors.append(f"duplicate step id: {step['id']}")
        step_ids.add(step["id"])
    for link in (proc.get("next_steps") or []) + (proc.get("related") or []):
        if not link.get("procedure_id"):
            errors.append(f"link missing procedure_id: {link!r}")
    gov = proc.get("governance") or {}
    if gov.get("status") not in GOVERNANCE_STATUSES:
        errors.append(f"bad governance.status: {gov.get('status')!r}")
    if require_approved_complete and gov.get("status") == "approved":
        for key in ("when_to_use", "quality_checks", "common_mistakes", "provenance"):
            if key == "provenance":
                if not (proc.get("provenance") or {}).get("note"):
                    errors.append("approved procedure needs provenance.note")
            elif not proc.get(key):
                errors.append(f"approved procedure needs {key}")
        if not proc.get("synonyms"):
            errors.append("approved procedure needs synonyms (field language)")
    if errors:
        raise ValueError("; ".join(errors))
    return proc


def _groups_from_manifest(raw: dict) -> list[dict]:
    """Accept ``groups`` or ``categories`` (authoring alias)."""
    groups = raw.get("groups")
    if groups:
        return list(groups)
    categories = raw.get("categories") or []
    out = []
    for c in categories:
        if not isinstance(c, dict):
            continue
        out.append({
            "id": c.get("id"),
            "title": c.get("title"),
            "blurb": c.get("summary") or c.get("blurb") or "",
            "procedure_ids": c.get("procedure_ids") or [],
            "trade_id": c.get("trade_id"),
            "order": c.get("order"),
        })
    return out


def validate_manifest(raw: dict, procedure_ids: set[str]) -> dict:
    """Validate catalog manifest; ensure groups only list known procedure ids."""
    if not isinstance(raw, dict):
        raise ValueError("manifest must be an object")
    version = str(raw.get("version") or "1")
    trades = raw.get("trades") or []
    groups = _groups_from_manifest(raw)
    if not groups:
        raise ValueError("manifest.groups (or categories) is required")
    seen: set[str] = set()
    clean_groups: list[dict[str, Any]] = []
    for g in groups:
        if not isinstance(g, dict):
            raise ValueError("group must be an object")
        gid = str(g.get("id") or "").strip()
        title = str(g.get("title") or "").strip()
        if not gid or not title:
            raise ValueError("group needs id + title")
        ids = []
        for pid in g.get("procedure_ids") or []:
            pid_s = str(pid).strip()
            if pid_s not in procedure_ids:
                raise ValueError(f"group {gid} lists unknown procedure {pid_s}")
            if pid_s in seen:
                raise ValueError(f"procedure {pid_s} listed in multiple groups")
            seen.add(pid_s)
            ids.append(pid_s)
        clean_groups.append({
            "id": gid,
            "title": title,
            "blurb": str(g.get("blurb") or "").strip(),
            "procedure_ids": ids,
            "trade_id": str(g.get("trade_id") or "").strip() or None,
        })
    missing = procedure_ids - seen
    jobcheck_anchors = raw.get("jobcheck_anchors") or {}
    if not isinstance(jobcheck_anchors, dict):
        jobcheck_anchors = {}
    clean_anchors = {
        str(k): str(v)
        for k, v in jobcheck_anchors.items()
        if str(v) in procedure_ids
    }
    featured = []
    for pid in raw.get("featured_procedure_ids") or []:
        pid_s = str(pid).strip()
        if pid_s in procedure_ids:
            featured.append(pid_s)
    return {
        "version": version,
        "title": str(raw.get("title") or "GVC Field Guide").strip(),
        "trades": trades if isinstance(trades, list) else [],
        "groups": clean_groups,
        "ungrouped": sorted(missing),
        "featured_procedure_ids": featured,
        "jobcheck_anchors": clean_anchors,
        "synonym_hints": raw.get("synonym_hints")
        if isinstance(raw.get("synonym_hints"), dict) else {},
    }
