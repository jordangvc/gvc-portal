"""Load Field Guide procedures from ``content/fieldguide/`` (repo-shipped SoT).

Pattern mirrors ``subsystems/lien_watch/deadlines.load_rules``: committed JSON,
module-level cache, loud on malformed approved content. No GCS mutation —
authoring for v1 is git. Optional env ``GVC_FIELDGUIDE_CONTENT_DIR`` overrides
the path (tests).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from shared.paths import REPO_ROOT
from subsystems.fieldguide.schema import card_view, normalize_procedure
from subsystems.fieldguide.validate import validate_manifest, validate_procedure

DEFAULT_CONTENT_DIR = REPO_ROOT / "content" / "fieldguide"

_CACHE: dict[str, Any] = {
    "dir": None,
    "mtime_key": None,
    "catalog": None,
}


def content_dir(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    env = (os.environ.get("GVC_FIELDGUIDE_CONTENT_DIR") or "").strip()
    if env:
        return Path(env)
    return DEFAULT_CONTENT_DIR


def _mtime_key(root: Path) -> tuple:
    """Cheap invalidation: mtimes of manifest + procedure files."""
    parts: list[tuple] = []
    man = root / "manifest.json"
    if man.is_file():
        parts.append(("manifest", man.stat().st_mtime_ns))
    proc_dir = root / "procedures"
    if proc_dir.is_dir():
        for p in sorted(proc_dir.glob("*.json")):
            parts.append((p.name, p.stat().st_mtime_ns))
    return tuple(parts)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_uncached(root: Path, *, strict: bool = True) -> dict:
    """Build catalog dict: procedures, manifest, cards, by_id, jobcheck map."""
    man_path = root / "manifest.json"
    if not man_path.is_file():
        raise FileNotFoundError(f"fieldguide manifest missing: {man_path}")
    raw_manifest = _read_json(man_path)
    proc_dir = root / "procedures"
    procedures: list[dict] = []
    errors: list[str] = []
    if proc_dir.is_dir():
        for path in sorted(proc_dir.glob("*.json")):
            try:
                raw = _read_json(path)
                # Approved content must be complete when strict.
                require = strict
                proc = validate_procedure(
                    raw, require_approved_complete=require
                )
                procedures.append(proc)
            except (ValueError, TypeError, json.JSONDecodeError, OSError) as e:
                errors.append(f"{path.name}: {e}")
    if errors and strict:
        raise ValueError(
            "fieldguide procedure load errors: " + " | ".join(errors)
        )
    by_id = {p["id"]: p for p in procedures}
    manifest = validate_manifest(raw_manifest, set(by_id))
    cards = [card_view(p) for p in procedures
             if (p.get("governance") or {}).get("status") == "approved"]
    # Drafts still in by_id for admin/preview later; field catalog cards = approved
    return {
        "content_dir": str(root),
        "manifest": manifest,
        "procedures": procedures,
        "by_id": by_id,
        "cards": cards,
        "jobcheck_anchors": dict(manifest.get("jobcheck_anchors") or {}),
        "load_errors": errors,
        "counts": {
            "procedures": len(procedures),
            "approved": sum(
                1 for p in procedures
                if (p.get("governance") or {}).get("status") == "approved"
            ),
            "trades": len(manifest.get("trades") or []),
            "groups": len(manifest.get("groups") or []),
        },
    }


def load_catalog(
    path: Optional[Path] = None,
    *,
    strict: bool = True,
    force: bool = False,
) -> dict:
    """Return cached catalog; reload when content mtimes change."""
    root = content_dir(path)
    key = _mtime_key(root)
    if (
        not force
        and _CACHE["catalog"] is not None
        and _CACHE["dir"] == str(root)
        and _CACHE["mtime_key"] == key
    ):
        return _CACHE["catalog"]
    catalog = _load_uncached(root, strict=strict)
    _CACHE["dir"] = str(root)
    _CACHE["mtime_key"] = key
    _CACHE["catalog"] = catalog
    return catalog


def clear_cache() -> None:
    _CACHE["dir"] = None
    _CACHE["mtime_key"] = None
    _CACHE["catalog"] = None


def get_procedure(procedure_id: str, path: Optional[Path] = None) -> Optional[dict]:
    cat = load_catalog(path)
    return cat["by_id"].get((procedure_id or "").strip())


def list_cards(
    *,
    trade: Optional[str] = None,
    path: Optional[Path] = None,
) -> list[dict]:
    cat = load_catalog(path)
    cards = list(cat["cards"])
    if trade:
        t = trade.strip().lower()
        cards = [c for c in cards if (c.get("trade") or "") == t]
    return cards


def procedure_for_jobcheck_stage(
    stage_label: str, path: Optional[Path] = None
) -> Optional[dict]:
    cat = load_catalog(path)
    pid = (cat.get("jobcheck_anchors") or {}).get(stage_label)
    if not pid:
        # Case-insensitive fallback
        for k, v in (cat.get("jobcheck_anchors") or {}).items():
            if k.lower() == (stage_label or "").lower():
                pid = v
                break
    if not pid:
        return None
    return cat["by_id"].get(pid)


def catalog_summary(path: Optional[Path] = None) -> dict:
    """API-friendly catalog payload (no full step bodies — use get for that)."""
    cat = load_catalog(path)
    man = cat["manifest"]
    return {
        "ok": True,
        "version": man.get("version"),
        "title": man.get("title"),
        "counts": cat["counts"],
        "trades": man.get("trades") or [],
        "groups": man.get("groups") or [],
        "featured": [
            card_view(cat["by_id"][pid])
            for pid in (man.get("featured_procedure_ids") or [])
            if pid in cat["by_id"]
        ],
        "cards": cat["cards"],
        "jobcheck_anchors": cat.get("jobcheck_anchors") or {},
        "platform": {
            "content_dir": "content/fieldguide",
            "shell": "/ui/fieldguide",
            "templates": [
                "task_guide",
                "troubleshooting",
                "quick_reference",
                "checklist",
                "tools_materials",
            ],
        },
    }


# Re-export for callers that only need normalize without load
normalize_procedure_raw = normalize_procedure
