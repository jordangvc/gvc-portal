"""
Monday item-title rename — shared by bulk job-rename backfills.
=========================================================================
Uses change_multiple_column_values with column id `name` (same pattern as
adapters/monday/co._update_item). Never creates/deletes items.
"""
from __future__ import annotations

import json
from typing import Optional


def rename_item_name(mc, board_id: int, item_id: int, new_name: str) -> None:
    """
    Set an item's title to `new_name`.

    `mc` is a MondayClient (needs `_query`). Raises on GraphQL errors.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("new_name is required")
    query = """
    mutation ($boardId: ID!, $itemId: ID!, $values: JSON!) {
      change_multiple_column_values(
        board_id: $boardId, item_id: $itemId,
        column_values: $values) { id }
    }
    """
    mc._query(query, {
        "boardId": str(board_id),
        "itemId": str(item_id),
        "values": json.dumps({"name": new_name}),
    })


def linked_item_names(column_value: Optional[dict]) -> list[str]:
    """
    Best-effort extract of linked item display names from a board-relation
    column_values entry (text is often comma-separated names).
    """
    if not column_value:
        return []
    text = (column_value.get("text") or "").strip()
    if not text:
        return []
    return [p.strip() for p in text.split(",") if p.strip()]
