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
# a mirror. They are deliberately absent from JOBCHECK_COLUMNS below; phase-2
# slice 1 writes them on the linked Projects item via JOBCHECK_PROJECTS_TRADE_COLUMNS.
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
    {"id": "date",             "label": "Start Date",         "type": "date"},
    {"id": "text_mkz4p9tk",    "label": "Scaffolding",        "type": "text"},
    {"id": "text_mkz4q570",    "label": "Heater/Cans",        "type": "text"},
    {"id": "text_mkz49r0m",    "label": "Lock Box",           "type": "text"},
    {"id": "text_mm14mhpm",    "label": "Shower Instructions", "type": "text"},
    {"id": "color_mm02xmc0",   "label": "Window type",        "type": "status"},
    {"id": "long_text_mkpzf3je", "label": "Open questions for Ops", "type": "long_text"},
)

# Phase-2 slice 1 — trade statuses on the Projects board (NOT Ops mirrors).
# Crew edits these from Job Check; writes go to the linked Projects item via
# `link_to_projects`. status_19 here is Hanging Status on Projects — the Ops
# board's status_19 is Scheduled Day (in JOBCHECK_COLUMNS). Keep board-scoped.
#
# Slice 2 (2026-08-05) adds the tape/coat/sand/skim sequence that PR #18
# deliberately left out ("Out of scope (next slices): Taped / coats / sanded
# / skim, Completion Date, Notes"). Those 5 ids are the SAME dup__of_* chain
# already verified live against Projects board 1918846405 — documented in
# CLAUDE.md's 2026-07-27 Job Check v1 build note alongside Framing/Hanging/
# Scrapping/Finishing.
#
# Slice 3 (this PR) closes the driveway pass: Cleaned Out + Completion Date
# + Notes — the three end-of-job fields that followed Finishing Stage in the
# original v1 allowlist. notes7 is long_text (Job Start exclusions writes the
# same column as {"text": ...}).
JOBCHECK_PROJECTS_TRADE_COLUMNS: tuple[dict, ...] = (
    {"id": "color_mkza9z7c",        "label": "Framing Status",   "type": "status"},
    {"id": "status_19",             "label": "Hanging Status",   "type": "status"},
    {"id": "dup__of_hung_status1",  "label": "Scrapping Status", "type": "status"},
    {"id": "dup__of_scrapped_status", "label": "Taped Status",   "type": "status"},
    {"id": "dup__of_taped_status",  "label": "2nd Bed Coat",     "type": "status"},
    {"id": "dup__of_2nd_bed_coat",  "label": "3rd Coat",         "type": "status"},
    {"id": "dup__of_3rd_coat",      "label": "Sanded",           "type": "status"},
    {"id": "dup__of_sanded",        "label": "Text/Skim",        "type": "status"},
    {"id": "color_mkza855s",        "label": "Finishing Stage",  "type": "status"},
    {"id": "color8",                "label": "Cleaned Out",      "type": "status"},
    {"id": "date1",                 "label": "Completion Date",  "type": "date"},
    {"id": "notes7",                "label": "Notes",            "type": "long_text"},
)

# Field Manual deep-links for Job Check trade status chips — column id → anchor
# in web/fieldguide.html. Only columns with a clear matching procedure.
JOBCHECK_FIELDGUIDE_ANCHORS: dict[str, str] = {
    "color_mkza9z7c": "#framing",
    "status_19": "#hang",
    "dup__of_hung_status1": "#scrape",
    "color_mkza855s": "#finish",
}


# ---------------------------------------------------------------------------
# Morning Brief — employee daily control center (docs/MORNING_BRIEF_BUILD_SPEC.md,
# approved 2026-08-03). READ-ONLY column allowlist for the Operations board.
# Financial / billing columns are never included here and are also listed in
# MORNING_HARD_EXCLUDED_IDS so a future edit cannot accidentally expose them.
# ---------------------------------------------------------------------------
MORNING_BOARD_ID = int(os.environ.get("GVC_MONDAY_MORNING_BOARD_ID")
                       or OPERATIONS_BOARD_ID)

# Same finished-work groups Job Check hides — the brief is for active work.
MORNING_SKIP_GROUP_IDS = JOBCHECK_SKIP_GROUP_IDS

# Column ids the Morning Brief API may READ (never write in slice 1).
MORNING_COL_OPS_OWNER = "multiple_person_mm1ht2vj"
MORNING_COL_SCHEDULED = "status_19"
MORNING_COL_STAGE = "status"
MORNING_COL_STAGE_DETAIL = "color_mm1hmwdm"
MORNING_COL_BLOCKED = "color_mm1hrm6z"
MORNING_COL_NEEDS_FROM_JORDAN = "color_mm1gemtq"  # Ops status — migrate → Action Requests
MORNING_COL_OVERDUE = "color_mm1x2172"
MORNING_COL_LOCATION = "lookup_mknf1rdw"
MORNING_COL_PROJECT_LINK = "link_to_projects"
MORNING_COL_PROJECT_STATUS = "mirror3"
MORNING_COL_PROGRESS = "lookup_mkpeqd8w"  # mirrored Progress — may be empty

MORNING_READ_COLUMN_IDS: tuple[str, ...] = (
    MORNING_COL_OPS_OWNER,
    MORNING_COL_SCHEDULED,
    MORNING_COL_STAGE,
    MORNING_COL_STAGE_DETAIL,
    MORNING_COL_BLOCKED,
    MORNING_COL_NEEDS_FROM_JORDAN,
    MORNING_COL_OVERDUE,
    MORNING_COL_LOCATION,
    MORNING_COL_PROJECT_LINK,
    MORNING_COL_PROJECT_STATUS,
    MORNING_COL_PROGRESS,
)

# A blocked/overdue item with no meaningful change in this many days demotes
# from "Needs attention today" to "Long-term holds" (spec: Blocked and
# Overdue Work). Measured against the item's own `updated_at`.
MORNING_LONG_TERM_HOLD_DAYS = int(os.environ.get("GVC_MORNING_LONG_TERM_HOLD_DAYS", "7"))

# Money / billing — never in employee Morning Brief payloads.
# (Do NOT reuse JOBCHECK_HARD_EXCLUDED_IDS wholesale — that set also blocks
# write-only automation columns like Overdue that the brief must READ.)
MORNING_HARD_EXCLUDED_IDS = frozenset({
    "board_counts",       # Board Count — billing basis
    "numeric_mm3fcjmn",   # Pay App #
    "numeric_mm5ahj91",   # CO Amount
    "color_mm2xd40t",     # BIllable
    "date_mm3zry96",      # Ready for Invoice Date
})

# Projects-board GFolder Link (Drive root for job-site Pictures uploads).
# Used by Job Check photo upload and Morning Brief Drive open/upload.
MORNING_PROJECTS_GFOLDER_COL = os.environ.get(
    "GVC_MONDAY_PROJECTS_GFOLDER_COL") or "link_mkwr6ef9"
PROJECTS_GFOLDER_COL = MORNING_PROJECTS_GFOLDER_COL

# Action Requests board — create via scripts/create_action_requests_board.py.
# 0 = GCS-only SoT (portal/morning/action-requests.json). Spec retires the old
# Ops-board "Needs from Jordan" column after migration.
ACTION_REQUESTS_BOARD_ID = int(
    os.environ.get("GVC_MONDAY_ACTION_REQUESTS_BOARD_ID") or "0")


# ---------------------------------------------------------------------------
# Job Start — the Sales → Operations handoff contract (designed 2026-07-29;
# docs/portal-job-start-design.md). Jake's ask, Jordan's calls: portal-hosted,
# HARD GATE, history left alone.
#
# JOBSTART_FIELDS below IS the contract: one spec drives the form, the gate,
# and the Monday writes. Adding a field to the handoff is a config edit here,
# not a code change — same principle as JOBCHECK_COLUMNS above.
#
# Each entry:
#   key       stable form/draft key (never reuse one for a different meaning)
#   label     what Jake sees
#   type      status | text | long_text | date | number | link
#   targets   ((board, column_id), ...) — board is "projects" or "operations".
#             A field may write to MORE than one board; GVC genuinely carries
#             the same fact in two places (Scaffold lives on both boards).
#   required  True ⇒ part of the gate. Keep this set SMALL — over-requiring a
#             hard gate is how gates get routed around (design doc §The gate).
#   prefill   Bid Board column id to prefill from, or None.
#   help      one line of field guidance shown under the input.
#
# Column ids/types verified live against boards 1918846405 (Projects) and
# 1920364853 (Operations) via get_board_info on 2026-07-29.
# ---------------------------------------------------------------------------

# Where a handed-off job lands. Projects group = the same one the legacy Bid
# Board automation targets, so an adopted item stays where the team expects it.
JOBSTART_PROJECTS_GROUP = os.environ.get(
    "GVC_MONDAY_JOBSTART_PROJECTS_GROUP", "new_group25317__1")   # New Projects (Not Started)
JOBSTART_OPS_GROUP = os.environ.get(
    "GVC_MONDAY_JOBSTART_OPS_GROUP", "group_mm3khfvc")           # Upcoming Projects (Not Started)

# Bid Board stage that means "won". Job Start WRITES this (and moves the item to
# the Won Deals group) when Sales sends a packet — it no longer merely filters on
# it, because a bid used to be invisible to Job Start until somebody flipped the
# stage in Monday first (Jordan, 2026-07-29).
JOBSTART_ACCEPTED_STAGE = os.environ.get("GVC_MONDAY_JOBSTART_STAGE", "Accepted")

# Bid Board groups. Ids recorded 2026-06-29 (estimate flow's New→Open promotion):
#   New Deals `new_group__1` · Open Deals `topics`
#   Won `duplicate_of_active_deals__1` · Lost `closed`
# Empty value ⇒ never move the item, only write the stage.
JOBSTART_BID_WON_GROUP = os.environ.get(
    "GVC_MONDAY_BID_WON_GROUP", "duplicate_of_active_deals__1")

# Stage labels that take a bid OUT of the Job Start picker. Substring match,
# case-insensitive — the Bid Board's labels are hand-edited in Monday, so an
# exact list would silently hide any bid carrying a label we hadn't seen. Only
# genuinely dead states belong here; "open" is the default for everything else.
JOBSTART_DEAD_STAGE_WORDS: tuple[str, ...] = tuple(
    w.strip().lower()
    for w in os.environ.get("GVC_MONDAY_JOBSTART_DEAD_STAGES",
                            "lost,cancel").split(",")
    if w.strip())

# Render types the Job Start form supports (and the only ones the spec may use).
JOBSTART_RENDER_TYPES = ("status", "text", "long_text", "date", "number", "link")

# Columns Job Start may NEVER write, whatever the config says. Contract/money
# columns are owned by the estimate + invoice flows; a handoff form must not be
# able to restate the contract value.
JOBSTART_HARD_EXCLUDED_IDS = frozenset({
    "lookup_mm40txvs",    # Projects "Contract Value (from Opportunity)" (mirror)
    "lookup_mkzm8dqa",    # Projects "Estimate $" (mirror)
    "numeric_mm3fcjmn",   # Projects "Pay App #" — AIA billing sequence
    "numeric_mm5ahj91",   # Projects "CO Amount"
    "date_mm3zry96",      # Operations "Ready for Invoice Date" — triggers billing
    "color_mm2xd40t",     # Operations "BIllable" — set by the flow, not the form
})

# Types that can never be written through the packet form (people columns need
# Monday user ids; relations/mirrors are set by the flow's own link logic).
JOBSTART_HARD_EXCLUDED_TYPES = frozenset({
    "people", "multiple_person", "board_relation", "mirror", "lookup",
    "formula", "auto_number", "creation_log", "last_updated", "button",
    "subtasks", "file", "progress", "timeline", "integration", "location",
})

JOBSTART_FIELDS: tuple[dict, ...] = (
    # ---- The gate: what ops genuinely cannot start a job without -----------
    {"key": "project_type", "label": "Project type", "type": "status",
     "targets": (("projects", "status"),), "required": True,
     "prefill": "status",
     "help": "Residential or Commercial — drives crew, rates and billing."},

    {"key": "builder", "label": "Who is the builder?", "type": "text",
     "targets": (("projects", "text"),), "required": True, "prefill": None,
     "help": "The GC, builder or homeowner we're working for on site."},

    {"key": "supervisor", "label": "Site supervisor / contact", "type": "text",
     "targets": (("projects", "text5"),), "required": True, "prefill": None,
     "help": "Who the crew calls from the driveway. Name and number."},

    # Packet-only, optional: used when Sales drafts the GC scope-confirmation
    # email. Never part of the send/accept gate — skip anytime. Getting the GC
    # to reconcile our scope in writing BEFORE we mobilize is still the cheapest
    # change order we'll ever avoid when they do use it.
    {"key": "gc_pm", "label": "GC project manager", "type": "text",
     "targets": (), "required": False, "prefill": None,
     "help": "Name of the GC's PM — the person who signs off on scope. "
             "Optional; only needed if you draft the scope confirmation."},

    {"key": "gc_email", "label": "GC PM email", "type": "text",
     "targets": (), "required": False, "prefill": "mirror34",
     "help": "Where the scope confirmation goes (optional). Prefilled from "
             "the bid's customer record when we have it."},

    {"key": "super_email", "label": "Site super email", "type": "text",
     "targets": (), "required": False, "prefill": None,
     "help": "Optional CC on the scope confirmation, so the super can't say "
             "they never saw it."},

    {"key": "scope", "label": "Scope of work", "type": "long_text",
     "targets": (("projects", "details"),), "required": True,
     "prefill": "details",
     "help": "What we sold, in the crew's language. Prefilled from the bid — "
             "edit it for the field."},

    # The single highest-value field in the packet. Ops finding out mid-job what
    # was NOT sold is breakage #1 in the handoff standard — this is the line
    # that stops us eating the difference.
    {"key": "exclusions", "label": "What we did NOT sell", "type": "long_text",
     "targets": (("projects", "notes7"),), "required": True, "prefill": None,
     "help": "Exclusions, in writing. The part that saves us when the GC "
             "assumes it was included."},

    {"key": "start_date", "label": "Start date", "type": "date",
     "targets": (("projects", "date"), ("operations", "date")), "required": True,
     "prefill": None,
     "help": "Best known start. Ops reschedules — this just gets it on the board."},

    {"key": "board_count", "label": "Board count", "type": "text",
     "targets": (("projects", "board_counts"),), "required": True,
     "prefill": "numbers0",
     "help": "Sheets of board. Ops stocks from this number."},

    {"key": "lock_box", "label": "Lock box / site access", "type": "text",
     "targets": (("operations", "text_mkz49r0m"),), "required": True,
     "prefill": None,
     "help": "Code, key location, or who lets the crew in. Day-one blocker "
             "when it's missing — say \"none needed\" if the site is open."},

    # ---- From the scope review, not typed --------------------------------
    # Jake's scope review flags these itself with [NEEDS CLARIFICATION]. Per
    # Jake (2026-07-29): the scope review "lists a lot of things that Rob might
    # have minor questions on". They are the items that become change orders
    # when they reach the field unread, so they ride the packet to Ops.
    {"key": "open_questions", "label": "Open questions for Ops",
     "type": "long_text", "targets": (("operations", "long_text_mkpzf3je"),),
     "required": False, "prefill": None,
     "help": "Pulled from the [NEEDS CLARIFICATION] lines in the scope review. "
             "Answer what you can before handing off."},

    {"key": "allowances", "label": "Allowances & walkthrough notes",
     "type": "long_text", "targets": (), "required": False, "prefill": None,
     "help": "Pulled from the scope review's walkthrough notes — allowance "
             "line items, decisions, anything agreed on site."},

    # ---- Prompted but optional -------------------------------------------
    {"key": "expected_finish", "label": "Expected finish", "type": "date",
     "targets": (("projects", "date_mm1gnhtf"),), "required": False,
     "prefill": None, "help": "If the builder gave a date to hit."},

    {"key": "lot", "label": "Lot # / type", "type": "text",
     "targets": (("projects", "text_mm47fvr7"),), "required": False,
     "prefill": "text23", "help": "Subdivision lot number, where there is one."},

    {"key": "ceiling_finish", "label": "Ceiling finish", "type": "status",
     "targets": (("projects", "color_mkzab319"),), "required": False,
     "prefill": None, "help": "Knockdown, smooth, etc."},

    {"key": "garage_finish", "label": "Garage finish", "type": "status",
     "targets": (("projects", "color_mkzaf1eq"),), "required": False,
     "prefill": None, "help": "How far the garage goes — a classic rework cause."},

    {"key": "window_type", "label": "Window type", "type": "status",
     "targets": (("operations", "color_mm02xmc0"),), "required": False,
     "prefill": None, "help": "Drywall, wood, or both."},

    {"key": "window_returns", "label": "Window returns", "type": "text",
     "targets": (("projects", "status7"),), "required": False,
     "prefill": None, "help": "Any special return detail the crew should expect."},

    {"key": "scaffold", "label": "Scaffold", "type": "text",
     "targets": (("projects", "text76"), ("operations", "text_mkz4p9tk")),
     "required": False, "prefill": "text7",
     "help": "Needed? Whose? Written to both boards."},

    {"key": "heater_cans", "label": "Heater / cans", "type": "text",
     "targets": (("operations", "text_mkz4q570"),), "required": False,
     "prefill": None, "help": "Heat on site, or what the crew needs to bring."},

    {"key": "shower", "label": "Shower instructions", "type": "text",
     "targets": (("operations", "text_mm14mhpm"),), "required": False,
     "prefill": None, "help": "Tile/shower detail if this job has one."},

    {"key": "takeoff_link", "label": "Take-off / stocking sheet", "type": "link",
     "targets": (("projects", "link"),), "required": False, "prefill": "link_1",
     "help": "Google Sheet link, if a takeoff was done."},

    # Packet-only: no Monday column, appears on the PDF when used. Optional —
    # skip anytime; never part of the send/accept gate.
    {"key": "gc_confirmed_on", "label": "Scope emailed to the GC on",
     "type": "date", "targets": (), "required": False, "prefill": None,
     "help": "Optional. Fills in by itself once a scope confirmation actually "
             "leaves hello@ (checked every 10 minutes). Only type it for a "
             "send that happened outside the portal."},
)


# --- Bid Board source columns (reads + the post-handoff stamp) --------------
# Verified live 2026-07-29. The stamp columns are the ones the legacy automation
# never wrote: Accepted Date is null on EVERY won deal to date.
JOBSTART_BID_STAGE_COL = "deal_stage"
JOBSTART_BID_ACCEPTED_DATE_COL = "date6"          # "Accepted Date"
JOBSTART_BID_PROJECT_LINK_COL = "connect_boards4"  # "Projects"
JOBSTART_BID_CUSTOMER_COL = "connect_boards5"      # "Customer Name"
JOBSTART_BID_LOCATION_COL = "location5"
JOBSTART_BID_ESTIMATE_NUM_COL = "numbers18"
JOBSTART_BID_ESTIMATE_TOTAL_COL = "number"
JOBSTART_BID_SERVICES_COL = "dropdown"
JOBSTART_BID_ESTIMATE_PDF_COL = "file_mkvk7hyz"

# The Bid Board has TWO relation columns pointing at Operations —
# connect_boards1 ("Team Tasks") and board_relation_mm44jdnw ("Operations").
# Both are empty on 100% of accepted bids and `settings_str` does NOT expose a
# column's reciprocal partner, so neither the data nor the API can break the
# tie (checked 2026-07-29). Resolved by naming + id provenance:
#   • board_relation_mm44jdnw is literally titled "Operations"
#   • its Operations-side counterpart board_relation_mm44mja shares the `mm44`
#     generation prefix — Monday mints reciprocal pairs together, so these two
#     were created as a pair, while "Team Tasks"/connect_boards1 is an older
#     column from the subitem-era naming scheme
# Both sides stay env-overridable: the first live handoff proves it, because a
# true pair shows the link on BOTH boards from a single write.
JOBSTART_BID_OPS_LINK_COL = os.environ.get(
    "GVC_MONDAY_BID_OPS_LINK_COL", "board_relation_mm44jdnw")

# --- Projects/Operations columns the FLOW owns (not packet fields) ----------
JOBSTART_P_COL_CUSTOMER = "connect_boards9"
JOBSTART_P_COL_LOCATION = "location5"
JOBSTART_P_COL_OPPORTUNITY = "board_relation_mm40rg52"
JOBSTART_P_COL_PROJECT_STATUS = "deal_stage"
JOBSTART_P_COL_INVOICE_STATUS = "status0"
# Canonical Project # on the Projects board — same column Invoice "Look up &
# fill" keys on (adapters/monday/client.py COL_PROJECT_NUMBER / text_mm4fvj91).
# Job Start fills this from Bid Board Estimate # on Ops accept (fill-if-empty).
JOBSTART_P_COL_PROJECT_NUMBER = os.environ.get(
    "GVC_MONDAY_COL_PROJECT_NUMBER", "text_mm4fvj91")
JOBSTART_P_NOT_STARTED_LABEL = "Not Started"

JOBSTART_OPS_COL_STAGE = "status"
JOBSTART_OPS_COL_BILLABLE = "color_mm2xd40t"
JOBSTART_OPS_COL_LINK_PROJECTS = "link_to_projects"
# Paired with JOBSTART_BID_OPS_LINK_COL above (the `mm44` generation). The
# Operations board ALSO has an older board_relation_mkzt3d6b with the identical
# title "link to Opportunities" — deliberately not used, so the bid↔ops link
# lives on exactly one pair of columns instead of being split across two.
JOBSTART_OPS_COL_LINK_OPPORTUNITY = os.environ.get(
    "GVC_MONDAY_OPS_OPPORTUNITY_LINK_COL", "board_relation_mm44mja")
JOBSTART_OPS_STAGE_LABEL = "Upcoming"
JOBSTART_OPS_BILLABLE_LABEL = "Yes"
