"""
Monday write for outbound COIs — PLACEHOLDER (2026-07-14).
=========================================================================
The destination board hasn't been decided yet ("GC Billing Profiles" was
considered and parked — it needs cleanup and may not be the right home; see
docs/portal-coi-design.md §Monday). Per the standing rule we do NOT assume a
board or columns. Until GVC_MONDAY_COI_BOARD_ID is set this module skips
cleanly, and when it IS set it writes the safest possible record: a bare item
(name only, default group) — no column IDs assumed.

When the board is decided: add real column mapping here (holder, contact,
sent date, Drive link, expiry) exactly like adapters/monday/estimate.py does,
and extend log_coi's payload. The flow-side call signature is already final.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Optional


def coi_board_id() -> Optional[int]:
    raw = (os.environ.get("GVC_MONDAY_COI_BOARD_ID") or "").strip()
    return int(raw) if raw.isdigit() else None


def item_name(holder_name: str, expiry_label: Optional[str]) -> str:
    """PURE: the Monday item name for one outbound COI."""
    parts = [holder_name.strip() or "COI"]
    if expiry_label:
        parts.append(expiry_label.strip())
    parts.append(date.today().isoformat())
    return " — ".join(parts)


def log_coi(
    *,
    holder_name: str,
    contact_email: str,
    expiry_label: Optional[str] = None,
    drive_url: Optional[str] = None,
) -> dict:
    """
    Record an outbound COI on the (future) Monday COI board. Returns writeback
    keys only — NEVER raises past a clean status (the caller treats Monday as
    strictly non-fatal). contact_email/drive_url are accepted now so the call
    signature doesn't change when real column mapping lands.
    """
    board_id = coi_board_id()
    if board_id is None:
        return {"monday_status": (
            "SKIPPED — COI board not decided yet (placeholder). Set "
            "GVC_MONDAY_COI_BOARD_ID once the board exists."
        )}

    try:
        from adapters.monday.client import MondayClient

        mc = MondayClient()
        q = """
        mutation ($board_id: ID!, $name: String!) {
          create_item (board_id: $board_id, item_name: $name) { id }
        }
        """
        out = mc._query(q, {
            "board_id": str(board_id),
            "name": item_name(holder_name, expiry_label),
        })
        new_id = (((out or {}).get("data") or {}).get("create_item") or {}).get("id")
        return {
            "monday_item_id": new_id,
            "monday_status": ("logged (name-only placeholder item — column "
                              "mapping pending board decision)"),
        }
    except Exception as e:  # noqa: BLE001 — non-fatal by contract
        return {"monday_status": f"FAILED — {type(e).__name__}: {e}"}
