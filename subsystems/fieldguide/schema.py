"""Field Guide content schemas — pure data shapes + coercion helpers.

These types are the contract for ``content/fieldguide/`` JSON. The live
``web/fieldguide.html`` shell stays the field UX; this layer makes content
searchable, reviewable, and expandable without editing a 500KB HTML file.

Authoring JSON may use either the canonical keys or the richer aliases used
in procedure files (``trade_id``, ``plain_words``, ``search_tags``, etc.).
``normalize_procedure`` always returns the canonical shape.
"""
from __future__ import annotations

from typing import Any

# Publish states — draft never surfaces in field search by default.
GOVERNANCE_STATUSES = frozenset({"draft", "review", "approved", "stale", "archived"})
WARNING_KINDS = frozenset({"stop", "warn", "note", "tip", "money", "safety"})
PROVENANCE_TIERS = frozenset({"standard", "benchmark", "gvc", "gvc_practice"})


def _str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return str(v).strip()


def _str_list(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    out: list[str] = []
    for item in v:
        if isinstance(item, dict):
            # tools/materials objects, quality_checks, mistakes, etc.
            s = _str(
                item.get("text")
                or item.get("prompt")
                or item.get("name")
                or item.get("title")
                or item.get("wrong")
            )
        else:
            s = _str(item)
        if s:
            out.append(s)
    return out


def _named_list(v: Any) -> list[str]:
    """Tools/materials: string or {name, optional} → display strings."""
    if not v:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    out: list[str] = []
    for item in v:
        if isinstance(item, dict):
            name = _str(item.get("name") or item.get("title"))
            if not name:
                continue
            if item.get("optional"):
                name = f"{name} (optional)"
            out.append(name)
        else:
            s = _str(item)
            if s:
                out.append(s)
    return out


def normalize_step(raw: dict, *, procedure_id: str, index: int) -> dict:
    sid = _str(raw.get("id")) or f"{procedure_id}-s{index + 1:02d}"
    text = _str(raw.get("text") or raw.get("body"))
    title = _str(raw.get("title"))
    if title and text and not text.lower().startswith(title.lower()):
        text = f"{title} — {text}" if text else title
    elif title and not text:
        text = title
    return {
        "id": sid,
        "text": text,
        "title": title,
        "expert": bool(raw.get("expert")),
        "checklist": bool(raw.get("checklist", True)),
        "order": int(raw.get("order") or (index + 1)),
    }


def normalize_warning(raw: dict) -> dict:
    kind = _str(raw.get("kind") or raw.get("severity") or "note").lower()
    # Map authoring severities onto WARNING_KINDS
    if kind == "warning":
        kind = "warn"
    if kind not in WARNING_KINDS:
        kind = "note"
    title = _str(raw.get("title"))
    body = _str(raw.get("text") or raw.get("body"))
    text = f"{title}: {body}" if title and body else (title or body)
    return {"kind": kind, "title": title, "text": text}


def normalize_link(raw: dict) -> dict:
    return {
        "label": _str(raw.get("label") or raw.get("title")),
        "procedure_id": _str(raw.get("procedure_id") or raw.get("id")),
        "why": _str(raw.get("why")),
    }


def normalize_variation(raw: dict) -> dict:
    guidance = _str(raw.get("guidance") or raw.get("body"))
    changes = raw.get("changes")
    if not guidance and isinstance(changes, list):
        guidance = "; ".join(_str(c) for c in changes if _str(c))
    return {
        "id": _str(raw.get("id")),
        "title": _str(raw.get("title")),
        "when": _str(raw.get("when")),
        "guidance": guidance,
    }


def normalize_mistake(raw: Any) -> dict:
    if isinstance(raw, str):
        return {"title": "", "wrong": raw, "right": "", "why": "", "text": raw}
    if not isinstance(raw, dict):
        return {"title": "", "wrong": "", "right": "", "why": "", "text": _str(raw)}
    title = _str(raw.get("title"))
    wrong = _str(raw.get("wrong"))
    right = _str(raw.get("right"))
    why = _str(raw.get("why"))
    parts = [p for p in (title, wrong and f"Wrong: {wrong}", right and f"Right: {right}", why and f"Why: {why}") if p]
    return {
        "id": _str(raw.get("id")),
        "title": title,
        "wrong": wrong,
        "right": right,
        "why": why,
        "text": " — ".join(parts) if parts else _str(raw.get("text")),
    }


def normalize_quality_check(raw: Any) -> dict:
    if isinstance(raw, str):
        return {"id": "", "prompt": raw, "severity": "required", "text": raw}
    if not isinstance(raw, dict):
        return {"id": "", "prompt": _str(raw), "severity": "required", "text": _str(raw)}
    prompt = _str(raw.get("prompt") or raw.get("text") or raw.get("title"))
    return {
        "id": _str(raw.get("id")),
        "prompt": prompt,
        "severity": _str(raw.get("severity") or "required").lower() or "required",
        "text": prompt,
    }


def normalize_troubleshooting(raw: dict) -> dict:
    return {
        "id": _str(raw.get("id")),
        "symptom": _str(raw.get("symptom")),
        "likely_cause": _str(raw.get("likely_cause") or raw.get("cause")),
        "fix": _str(raw.get("fix")),
    }


def normalize_expert(raw: dict) -> dict:
    return {
        "id": _str(raw.get("id")),
        "tag": _str(raw.get("tag")),
        "summary": _str(raw.get("summary") or raw.get("body")),
    }


def normalize_diagram(raw: dict) -> dict:
    return {
        "id": _str(raw.get("id")),
        "caption": _str(raw.get("caption")),
        "svg_path": _str(raw.get("svg_path") or raw.get("path")),
    }


def normalize_provenance(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"tiers": [], "note": _str(raw), "sources": [], "kind": ""}
    tiers = []
    for t in _str_list(raw.get("tiers")):
        tl = t.lower()
        if tl in PROVENANCE_TIERS:
            tiers.append(tl)
    kind = _str(raw.get("kind")).lower()
    if kind and kind in PROVENANCE_TIERS and kind not in tiers:
        tiers.append(kind)
    if kind == "gvc_practice" and "gvc" not in tiers:
        tiers.append("gvc")
    sources = _str_list(raw.get("sources"))
    return {
        "tiers": tiers,
        "kind": kind,
        "note": _str(raw.get("note")),
        "sources": sources,
    }


def normalize_governance(raw: Any, *, flat: dict | None = None) -> dict:
    """Accept nested ``governance`` or flat authoring fields on the procedure."""
    if not isinstance(raw, dict):
        raw = {}
    flat = flat or {}
    status = _str(
        raw.get("status") or flat.get("review_status") or "draft"
    ).lower()
    if status not in GOVERNANCE_STATUSES:
        status = "draft"
    cycle = raw.get("review_cycle_days", flat.get("review_cycle_days", 180))
    try:
        cycle_i = int(cycle)
    except (TypeError, ValueError):
        cycle_i = 180
    return {
        "status": status,
        "owner": _str(raw.get("owner") or flat.get("owner") or "field"),
        "last_reviewed": _str(
            raw.get("last_reviewed") or flat.get("last_reviewed")
        ),
        "review_cycle_days": max(30, cycle_i),
        "notes": _str(raw.get("notes")),
    }


def normalize_procedure(raw: dict) -> dict:
    """Return a canonical procedure dict (does not validate completeness)."""
    if not isinstance(raw, dict):
        raise TypeError("procedure must be an object")
    pid = _str(raw.get("id") or raw.get("slug"))
    steps_in = raw.get("steps") or []
    steps = [
        normalize_step(
            s if isinstance(s, dict) else {"text": s},
            procedure_id=pid or "proc",
            index=i,
        )
        for i, s in enumerate(steps_in)
    ]
    warnings = [
        normalize_warning(w if isinstance(w, dict) else {"text": w})
        for w in (raw.get("warnings") or [])
    ]
    mistakes_raw = raw.get("common_mistakes") or []
    mistakes = [
        normalize_mistake(m) for m in mistakes_raw
    ]
    qc_raw = raw.get("quality_checks") or []
    quality = [
        normalize_quality_check(q) for q in qc_raw
    ]
    related = []
    for x in (raw.get("related") or raw.get("related_guides") or []):
        if isinstance(x, dict):
            related.append(normalize_link(x))
        elif isinstance(x, str) and x.strip():
            related.append(normalize_link({"id": x.strip(), "label": x.strip()}))
    for rid in raw.get("related_ids") or []:
        rid_s = _str(rid)
        if rid_s and not any(r["procedure_id"] == rid_s for r in related):
            related.append(normalize_link({"id": rid_s, "label": rid_s}))

    next_steps = []
    for x in (raw.get("next_steps") or []):
        if isinstance(x, dict):
            next_steps.append(normalize_link(x))
        elif isinstance(x, str) and x.strip():
            next_steps.append(normalize_link({"id": x.strip(), "label": x.strip()}))

    when = raw.get("when_to_use")
    if isinstance(when, list):
        when_s = "; ".join(_str(x) for x in when if _str(x))
    else:
        when_s = _str(when)

    lede = _str(
        raw.get("lede")
        or raw.get("plain_words")
        or raw.get("short_answer")
    )
    summary = _str(raw.get("summary") or raw.get("short_answer") or lede)

    tags = [t.lower() for t in _str_list(raw.get("tags") or raw.get("search_tags"))]
    synonyms = [s.lower() for s in _str_list(raw.get("synonyms"))]
    roles = [
        r.lower()
        for r in _str_list(raw.get("roles") or raw.get("role_tags"))
    ]

    diagram_ids = _str_list(raw.get("diagram_ids"))
    diagrams = [
        normalize_diagram(d) for d in (raw.get("diagrams") or [])
        if isinstance(d, dict)
    ]
    for did in diagram_ids:
        if not any(d.get("id") == did for d in diagrams):
            diagrams.append(normalize_diagram({"id": did}))

    troubleshooting = [
        normalize_troubleshooting(t)
        for t in (raw.get("troubleshooting") or [])
        if isinstance(t, dict)
    ]

    return {
        "id": pid,
        "slug": _str(raw.get("slug") or pid),
        "title": _str(raw.get("title")),
        "trade": _str(raw.get("trade") or raw.get("trade_id") or "general").lower(),
        "category": _str(raw.get("category") or raw.get("category_id")),
        "template": _str(raw.get("template") or "task_guide"),
        "stage_line": _str(raw.get("stage_line") or raw.get("jobcheck_stage")),
        "summary": summary,
        "short_answer": _str(raw.get("short_answer") or summary),
        "lede": lede,
        "roles": roles,
        "jobcheck_stage": _str(raw.get("jobcheck_stage")),
        "when_to_use": when_s,
        "prerequisites": _str_list(raw.get("prerequisites")),
        "tools": _named_list(raw.get("tools")),
        "materials": _named_list(raw.get("materials")),
        "steps": steps,
        "warnings": warnings,
        "variations": [
            normalize_variation(v) for v in (raw.get("variations") or [])
            if isinstance(v, dict)
        ],
        "quality_checks": [q["text"] for q in quality if q.get("text")],
        "quality_check_items": quality,
        "common_mistakes": [m["text"] for m in mistakes if m.get("text")],
        "common_mistake_items": mistakes,
        "troubleshooting": troubleshooting,
        "next_steps": next_steps,
        "related": related,
        "tags": tags,
        "synonyms": synonyms,
        "experts": [
            normalize_expert(e) for e in (raw.get("experts") or [])
            if isinstance(e, dict)
        ],
        "diagrams": diagrams,
        "provenance": normalize_provenance(raw.get("provenance")),
        "governance": normalize_governance(
            raw.get("governance"), flat=raw
        ),
    }


def procedure_search_blob(proc: dict) -> str:
    """Lowercased haystack for simple field search."""
    parts: list[str] = [
        proc.get("id") or "",
        proc.get("slug") or "",
        proc.get("title") or "",
        proc.get("trade") or "",
        proc.get("category") or "",
        proc.get("summary") or "",
        proc.get("short_answer") or "",
        proc.get("lede") or "",
        proc.get("when_to_use") or "",
        proc.get("jobcheck_stage") or "",
        proc.get("stage_line") or "",
    ]
    parts.extend(proc.get("tags") or [])
    parts.extend(proc.get("synonyms") or [])
    parts.extend(proc.get("roles") or [])
    parts.extend(proc.get("tools") or [])
    parts.extend(proc.get("materials") or [])
    parts.extend(proc.get("common_mistakes") or [])
    parts.extend(proc.get("quality_checks") or [])
    parts.extend(proc.get("prerequisites") or [])
    for step in proc.get("steps") or []:
        parts.append(step.get("text") or "")
        parts.append(step.get("title") or "")
    for var in proc.get("variations") or []:
        parts.append(var.get("title") or "")
        parts.append(var.get("when") or "")
        parts.append(var.get("guidance") or "")
    for ts in proc.get("troubleshooting") or []:
        parts.append(ts.get("symptom") or "")
        parts.append(ts.get("fix") or "")
    for w in proc.get("warnings") or []:
        parts.append(w.get("text") or "")
    return " ".join(p for p in parts if p).lower()


def card_view(proc: dict) -> dict:
    """Compact card for home/search results — phone-scannable."""
    gov = proc.get("governance") or {}
    return {
        "id": proc.get("id"),
        "title": proc.get("title"),
        "trade": proc.get("trade"),
        "category": proc.get("category"),
        "summary": proc.get("summary"),
        "short_answer": proc.get("short_answer"),
        "stage_line": proc.get("stage_line"),
        "roles": list(proc.get("roles") or []),
        "jobcheck_stage": proc.get("jobcheck_stage") or None,
        "tags": list(proc.get("tags") or []),
        "synonyms": list(proc.get("synonyms") or []),
        "href": f"/ui/fieldguide#{proc.get('id')}",
        "status": gov.get("status"),
        "step_count": len(proc.get("steps") or []),
        "has_diagrams": bool(proc.get("diagrams")),
        "next_steps": list(proc.get("next_steps") or []),
        "template": proc.get("template") or "task_guide",
    }
