"""
Morning Brief — heuristic Ops↔Projects / Drive-folder link suggestions.
=========================================================================
PURE name-matching only (no generative AI, no Monday/Drive I/O). Reuses the
jobstart naming Jaccard + street-number scorer so legacy dash names still
match pipe-format Monday titles.

Callers (morning_flow.suggest_links) supply candidate lists; this module only
scores and picks. An ambiguous top-2 is treated as no match — adopting the
wrong Projects item is worse than suggesting nothing.
"""
from __future__ import annotations

from typing import Any

from subsystems.jobstart import naming

# Same bar as adopt-or-create on Job Start; callers that need a stricter
# auto-link gate (e.g. ≥0.85 fill-if-empty) pass threshold explicitly.
DEFAULT_THRESHOLD = naming.MATCH_THRESHOLD
AMBIGUITY_DELTA = 0.05


def score_name_match(a: str, b: str) -> float:
    """PURE. 0.0–1.0 similarity between two job names (Jaccard + street #)."""
    return naming.match_score(a or "", b or "")


def _suggest(name: str, candidates: list, *,
             threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """
    Shared pick logic → {match, score, ambiguous, runners_up?}.

    `match` is the winning candidate dict (with `score` attached) or None.
    `ambiguous` is True when the top two are within AMBIGUITY_DELTA — then
    `match` is None even if the top score clears the threshold.
    """
    empty = {"match": None, "score": 0.0, "ambiguous": False}
    if not (name or "").strip() or not candidates:
        return dict(empty)

    target = name.strip().casefold()
    for c in candidates:
        if (c.get("name") or "").strip().casefold() == target:
            hit = {**c, "score": 1.0, "how": "exact"}
            return {"match": hit, "score": 1.0, "ambiguous": False}

    scored = sorted(
        ({**c, "score": score_name_match(name, c.get("name") or ""),
          "how": "tokens"}
         for c in candidates),
        key=lambda c: c["score"], reverse=True)
    top_score = scored[0]["score"] if scored else 0.0
    if not scored or top_score < threshold:
        return {"match": None, "score": top_score, "ambiguous": False}

    ambiguous = (len(scored) > 1
                 and scored[1]["score"] >= scored[0]["score"] - AMBIGUITY_DELTA)
    if ambiguous:
        return {
            "match": None,
            "score": top_score,
            "ambiguous": True,
            "runners_up": scored[:2],
        }
    return {"match": scored[0], "score": scored[0]["score"], "ambiguous": False}


def suggest_project_for_ops(
    ops_name: str,
    project_candidates: list,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """
    PURE. Best Projects-board match for an Operations item name.

    `project_candidates` are dicts with at least {id, name} (url optional).
    Returns {match, score, ambiguous} — match is None when below threshold
    or when the top-2 are too close to call.
    """
    return _suggest(ops_name, project_candidates or [], threshold=threshold)


def suggest_drive_folder(
    job_name: str,
    folder_candidates: list,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """
    PURE. Best Drive-folder match for a job name.

    `folder_candidates` are dicts with at least {id, name} (url optional).
    Same return shape as suggest_project_for_ops.
    """
    return _suggest(job_name, folder_candidates or [], threshold=threshold)
