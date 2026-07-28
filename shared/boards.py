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

# Job Check targets the OPERATIONS board (moved 2026-07-28). Evidence: over 14
# days the Operations board took 788 column edits — Scheduled Day 178, Stage
# Detail 95, Stage Completion 75, Stage 72, Needs-from-Jordan 34, Blocked 24 —
# with the field PMs (Mark W, Robert R) as the top editors, while the twelve
# Projects-board columns Job Check used to write took just 29 edits between
# them. The crew already lives here.
#
# ⚠ The trade-status columns (Framing/Hanging/Scrapping/Finishing…) exist on
# Operations only as MIRRORS of the Projects board, and Monday cannot write to
# a mirror. They are deliberately absent from the allowlist below; writing them
# through the `link_to_projects` relation is phase 2.
JOBCHECK_BOARD_ID = int(os.environ.get("GVC_MONDAY_JOBCHECK_BOARD_ID")
                        or OPERATIONS_BOARD_ID)

# Operations groups the crew picker hides: finished work and the office's
# invoicing queue. Everything else (In-Progress, Upcoming) is offered — an
# exclusion set, not an allowlist, so a NEW group can never silently vanish
# from the picker.
JOBCHECK_SKIP_GROUP_IDS = frozenset({
    "new_group",          # "Completed Tasks"
    "group_mm3zq4q2",     # "Ready to Invoice" — office-owned from here on
})

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
    # Projects board
    "board_counts",       # "Board Count" — the per-board billing basis
    "numeric_mm3fcjmn",   # "Pay App #" — AIA billing sequence (office-owned)
    "numeric_mm5ahj91",   # "CO Amount" — change-order dollars
    # Operations board
    "color_mm2xd40t",     # "BIllable" — a billing decision, not a crew call
    "date_mm3zry96",      # "Ready for Invoice Date" — triggers the office
    "color_mm1x2172",     # "Overdue" — an automation owns this column
})

# Render types the Job Check form supports (and the only values the config
# may declare). "number" maps to Monday's "numbers" column type.
JOBCHECK_RENDER_TYPES = ("status", "checkbox", "date", "text", "long_text",
                         "number")

# Default allowlist — OPERATIONS board (1920364853), display order IS this
# order. Column ids/types verified live via get_board_info 2026-07-28. Ordered
# the way a PM reports from a driveway: where the job is, what's stopping it,
# when it's due back, then the site-logistics notes. Jordan/Andrea: edit here.
JOBCHECK_COLUMNS: tuple[dict, ...] = (
    {"id": "status",           "label": "Stage",              "type": "status"},
    {"id": "color_mm1hmwdm",   "label": "Stage Detail",       "type": "status"},
    {"id": "color_mm1hrm6z",   "label": "Blocked",            "type": "status"},
    {"id": "color_mm1gemtq",   "label": "Needs from Jordan",  "type": "status"},
    {"id": "status_19",        "label": "Scheduled Day",      "type": "status"},
    {"id": "date_mm1kwzf9",    "label": "Stage Completion",   "type": "date"},
    {"id": "date_mm1ghszy",    "label": "Full Completion",    "type": "date"},
    {"id": "text_mkz4p9tk",    "label": "Scaffolding",        "type": "text"},
    {"id": "text_mkz4q570",    "label": "Heater/Cans",        "type": "text"},
    {"id": "text_mkz49r0m",    "label": "Lock Box",           "type": "text"},
    {"id": "text_mm14mhpm",    "label": "Shower Instructions", "type": "text"},
)
