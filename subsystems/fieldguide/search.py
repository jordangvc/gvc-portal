"""Field Guide search — synonym-aware, phone-first relevance.

Designed for a worker who may not know the formal procedure name
("scrapping" vs "scrape", "hang rock", "knockdown").
"""
from __future__ import annotations

from typing import Any, Optional

from subsystems.fieldguide.catalog import load_catalog
from subsystems.fieldguide.schema import card_view, procedure_search_blob


def _tokens(q: str) -> list[str]:
    return [t for t in (q or "").lower().replace(",", " ").split() if t]


def score_procedure(proc: dict, query: str) -> int:
    """Higher = better. Exact id/title/synonym beats haystack contains."""
    q = (query or "").strip().lower()
    if not q:
        return 0
    score = 0
    pid = (proc.get("id") or "").lower()
    title = (proc.get("title") or "").lower()
    stage = (proc.get("jobcheck_stage") or "").lower()
    synonyms = [s.lower() for s in (proc.get("synonyms") or [])]
    tags = [t.lower() for t in (proc.get("tags") or [])]

    if q == pid or q.replace(" ", "-") == pid:
        score += 100
    if q == title:
        score += 90
    if q in synonyms:
        score += 85
    if q in tags:
        score += 70
    if stage and q in stage:
        score += 60
    if q in title:
        score += 40
    if pid.startswith(q) or q in pid:
        score += 35

    blob = procedure_search_blob(proc)
    tokens = _tokens(q)
    if len(tokens) > 1:
        if all(t in blob for t in tokens):
            score += 25
        else:
            score += 5 * sum(1 for t in tokens if t in blob)
    elif q in blob:
        score += 15

    # Boost approved field content
    if (proc.get("governance") or {}).get("status") == "approved":
        score += 5
    return score


def search_procedures(
    query: str,
    *,
    trade: Optional[str] = None,
    limit: int = 20,
    include_drafts: bool = False,
    path=None,
) -> list[dict]:
    """Return ranked card_view dicts with ``score`` + ``match_reason``."""
    cat = load_catalog(path)
    q = (query or "").strip()
    results: list[dict] = []
    for proc in cat["procedures"]:
        gov = (proc.get("governance") or {}).get("status")
        if not include_drafts and gov not in ("approved", "stale"):
            continue
        if trade and (proc.get("trade") or "") != trade.strip().lower():
            continue
        if not q:
            card = card_view(proc)
            card["score"] = 0
            card["match_reason"] = "browse"
            results.append(card)
            continue
        sc = score_procedure(proc, q)
        if sc <= 0:
            continue
        card = card_view(proc)
        card["score"] = sc
        card["match_reason"] = _match_reason(proc, q)
        results.append(card)
    results.sort(key=lambda c: (-int(c.get("score") or 0), c.get("title") or ""))
    return results[: max(1, min(limit, 50))]


def _match_reason(proc: dict, query: str) -> str:
    q = query.strip().lower()
    if q == (proc.get("id") or "").lower():
        return "id"
    if q in [s.lower() for s in (proc.get("synonyms") or [])]:
        return "synonym"
    if q in (proc.get("title") or "").lower():
        return "title"
    if q in (proc.get("jobcheck_stage") or "").lower():
        return "jobcheck_stage"
    if q in [t.lower() for t in (proc.get("tags") or [])]:
        return "tag"
    return "content"


def related_suggestions(procedure_id: str, *, limit: int = 6, path=None) -> list[dict]:
    """People-also-look-up: next_steps + related + same-trade siblings."""
    cat = load_catalog(path)
    proc = cat["by_id"].get((procedure_id or "").strip())
    if not proc:
        return []
    seen = {proc["id"]}
    out: list[dict] = []
    for link in (proc.get("next_steps") or []) + (proc.get("related") or []):
        pid = link.get("procedure_id")
        if not pid or pid in seen:
            continue
        other = cat["by_id"].get(pid)
        if not other:
            continue
        seen.add(pid)
        card = card_view(other)
        card["relation"] = "next" if link in (proc.get("next_steps") or []) else "related"
        card["why"] = link.get("why") or link.get("label") or ""
        out.append(card)
        if len(out) >= limit:
            return out
    trade = proc.get("trade")
    for other in cat["procedures"]:
        if other["id"] in seen:
            continue
        if other.get("trade") != trade:
            continue
        if (other.get("governance") or {}).get("status") != "approved":
            continue
        seen.add(other["id"])
        card = card_view(other)
        card["relation"] = "same_trade"
        card["why"] = f"Also in {trade}"
        out.append(card)
        if len(out) >= limit:
            break
    return out


def search_payload(query: str, **kwargs: Any) -> dict:
    hits = search_procedures(query, **kwargs)
    return {
        "ok": True,
        "query": (query or "").strip(),
        "count": len(hits),
        "results": hits,
    }
