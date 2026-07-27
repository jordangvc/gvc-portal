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
# task here (new CO Monday model, decided 2026-07-16).
OPERATIONS_BOARD_ID = int(os.environ.get("GVC_MONDAY_OPERATIONS_BOARD_ID", "1920364853"))


# ---------------------------------------------------------------------------
# Job Check — Projects-board column allowlist (the portal's first WRITE
# surface into Monday, designed 2026-07-27; docs/portal-job-check-design.md).
#
# JOBCHECK_COLUMNS is the config Jordan/Andrea edit: which Projects-board
# columns the field crew can fill on a quality-check / completion pass, in
# display order. Each entry: {"id": Monday column id, "label": what the crew
# sees, "type": render type}. Render types the form knows how to draw:
#   status | checkbox | date | text | long_text | number
#
# Column ids/types verified live against board 1918846405 via get_board_info
# on 2026-07-27 (433 items). The default set below is the drywall trade
# sequence the crew actually walks on a check pass — Framing → Hang → Scrap →
# Tape/coats/sand → Finishing Stage → Clean-out — plus Completion Date and
# the free-text Notes column. Edit freely; the validator re-checks every save.
#
# HARD EXCLUSIONS are not config: money/contract/link/relation columns can
# never be written through Job Check even if someone adds them to
# JOBCHECK_COLUMNS. Enforced by TYPE (Monday column type) and by ID (money-ish
# columns whose type would otherwise pass, e.g. Board Count drives per-board
# billing). See orchestrators/jobcheck_flow.validate_values().
# ---------------------------------------------------------------------------

# Monday column TYPES Job Check may never write, regardless of config.
JOBCHECK_HARD_EXCLUDED_TYPES = frozenset({
    "board_relation", "mirror", "lookup", "link", "file", "button",
    "formula", "auto_number", "creation_log", "last_updated", "doc",
    "direct_doc", "subtasks", "people", "tags", "progress", "timeline",
    "location", "dependency", "integration",
})

# Money/billing columns on the Projects board whose plain type (text/numbers)
# would otherwise pass the type gate. Never writable through Job Check.
JOBCHECK_HARD_EXCLUDED_IDS = frozenset({
    "board_counts",       # "Board Count" — the per-board billing basis
    "numeric_mm3fcjmn",   # "Pay App #" — AIA billing sequence (office-owned)
    "numeric_mm5ahj91",   # "CO Amount" — change-order dollars
})

# Render types the Job Check form supports (and the only values the config
# may declare). "number" maps to Monday's "numbers" column type.
JOBCHECK_RENDER_TYPES = ("status", "checkbox", "date", "text", "long_text",
                         "number")

# Default allowlist — display order IS this order. Jordan/Andrea: edit here.
JOBCHECK_COLUMNS: tuple[dict, ...] = (
    {"id": "color_mkza9z7c",          "label": "Framing Status",   "type": "status"},
    {"id": "status_19",               "label": "Hanging Status",   "type": "status"},
    {"id": "dup__of_hung_status1",    "label": "Scrapping Status", "type": "status"},
    {"id": "dup__of_scrapped_status", "label": "Taped Status",     "type": "status"},
    {"id": "dup__of_taped_status",    "label": "2nd Bed Coat",     "type": "status"},
    {"id": "dup__of_2nd_bed_coat",    "label": "3rd Coat",         "type": "status"},
    {"id": "dup__of_3rd_coat",        "label": "Sanded",           "type": "status"},
    {"id": "dup__of_sanded",          "label": "Text/Skim",        "type": "status"},
    {"id": "color_mkza855s",          "label": "Finishing Stage",  "type": "status"},
    {"id": "color8",                  "label": "Cleaned Out",      "type": "status"},
    {"id": "date1",                   "label": "Completion Date",  "type": "date"},
    {"id": "notes7",                  "label": "Notes",            "type": "long_text"},
)
