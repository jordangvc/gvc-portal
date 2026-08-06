"""
Field Manual checklist coach — thin orchestration over pure coach helpers
and shared Job Check → Field Manual anchor maps.
"""
from __future__ import annotations

from typing import Optional

from orchestrators import jobcheck_flow
from shared import boards
from subsystems.fieldguide import coach as fg_coach

_COLUMN_LABELS: dict[tuple[str, str], str] = {}


def _build_column_label_index() -> dict[tuple[str, str], str]:
    if _COLUMN_LABELS:
        return _COLUMN_LABELS
    for col in boards.JOBCHECK_COLUMNS:
        _COLUMN_LABELS[(col["id"], "ops")] = col["label"]
    for col in boards.JOBCHECK_PROJECTS_TRADE_COLUMNS:
        _COLUMN_LABELS[(col["id"], "projects")] = col["label"]
    return _COLUMN_LABELS


def column_label(column_id: str, board: str) -> Optional[str]:
    """Human label for a Job Check column, if known."""
    return _build_column_label_index().get((column_id, board))


def get_coach(
    procedure: Optional[str] = None,
    stage: Optional[str] = None,
    column_id: Optional[str] = None,
    board: Optional[str] = None,
) -> dict:
    """Return deterministic coach JSON for the Field Manual API."""
    return fg_coach.build_coach_response(
        procedure=procedure,
        stage=stage,
        column_id=column_id,
        board=board,
        anchor_resolver=jobcheck_flow.fieldguide_anchor,
        column_label_lookup=column_label,
    )
