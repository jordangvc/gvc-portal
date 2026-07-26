"""Canonical Monday.com board IDs — single source of truth.

Each honours its env override (set per Cloud Run revision) and otherwise falls
back to the production board id. Subsystem/adapter modules import these instead
of re-declaring the literals.
"""
from __future__ import annotations

import os

PROJECTS_BOARD_ID = int(os.environ.get("GVC_MONDAY_PROJECTS_BOARD_ID", "1918846405"))
CUSTOMERS_BOARD_ID = int(os.environ.get("GVC_MONDAY_CUSTOMERS_BOARD_ID", "1919766765"))
INVOICES_SENT_BOARD_ID = int(os.environ.get("GVC_MONDAY_INVOICES_BOARD_ID", "1931784889"))
SUBITEMS_BOARD_ID = int(os.environ.get("GVC_MONDAY_PROJECTS_SUBITEMS_BOARD_ID", "1918846408"))
# Bid Board — renamed from "Opportunities" in Monday 2026-07-02 (same board id).
# The legacy env override is still honored so existing revisions/config can't break.
BID_BOARD_ID = int(os.environ.get("GVC_MONDAY_BID_BOARD_ID")
                   or os.environ.get("GVC_OPPORTUNITIES_BOARD_ID")
                   or 1918846027)
# Operations board — crew/office task tracking. A finalized Change Order adds a
# task here (new CO Monday model, Joe 2026-07-16).
OPERATIONS_BOARD_ID = int(os.environ.get("GVC_MONDAY_OPERATIONS_BOARD_ID", "1920364853"))
