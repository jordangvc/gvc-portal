"""
Deterministic Field Manual checklist coach — pure helpers only.

Suggests next steps and related procedures from curated stand-behind content.
No Monday writes, no Slack, no LLM.
"""
from __future__ import annotations

from typing import Any, Optional

COACH_BANS: tuple[str, ...] = (
    "Does not write Job Check",
    "Does not authorize CO/extra work",
    "Does not post to #operations",
)

# Stage aliases → procedure id (optional ?stage= hint).
STAGE_ALIASES: dict[str, str] = {
    "framing": "framing",
    "hang": "hang",
    "hanging": "hang",
    "scrape": "scrape",
    "scraping": "scrape",
    "finish": "finish",
    "finishing": "finish",
    "taped": "finish",
    "tape": "finish",
    "bed": "finish",
    "coat": "finish",
    "sanded": "finish",
    "skim": "level5-skim",
    "level5": "level5-skim",
    "level-5": "level5-skim",
    "text-skim": "level5-skim",
    "cleanout": "cleanout",
    "clean-out": "cleanout",
    "act": "act",
    "ceiling": "act",
    "firestop": "firestop",
    "firestopping": "firestop",
    "preboard": "preboard-walk",
    "pre-board": "preboard-walk",
    "rated": "ratedwalls",
    "ratedwalls": "ratedwalls",
    "shaft": "ratedwalls",
    "changeorder": "changeorder",
    "co": "changeorder",
    "stock": "stock-drywall",
    "stock-drywall": "stock-drywall",
    "qc": "qc-walk",
    "jobstart": "jobstart-firstday",
    "firstday": "jobstart-firstday",
    "closeout": "closeout-rhythm",
    "closeout-rhythm": "closeout-rhythm",
    "scaffold": "scaffold-lifts",
    "scaffold-lifts": "scaffold-lifts",
    "lifts": "scaffold-lifts",
    "job-conditions": "job-conditions",
    "conditions": "job-conditions",
    "heat": "job-conditions",
    "window-returns": "window-returns",
    "windows": "window-returns",
}

PROCEDURE_COACH: dict[str, dict[str, Any]] = {
    "framing": {
        "title": "Metal Stud Framing",
        "summary": "Layout the whole floor first, then batch bottom track, top track, and stud fill by operation.",
        "next_steps": [
            {"text": "Snap control lines and mark every wall type on the slab before any track goes down.",
             "anchor": "#framing"},
            {"text": "Build the head of wall per the approved deflection detail — studs short of the track web.",
             "anchor": "#framing/sys-deflection"},
            {"text": "Install bridging or CRC and anchor the ends before rock closes the cavity.",
             "anchor": "#framing/sys-bridging"},
            {"text": "QC plumb, ROs, backing, and head-of-wall photos before hang mobilizes.",
             "anchor": "#framing"},
        ],
        "related": [
            {"id": "preboard-walk", "title": "Pre-Board Walk",
             "why": "Catch backing and RO issues before board covers them."},
            {"id": "ratedwalls", "title": "Fire Walls & Shaftwall",
             "why": "Rated partitions need the listed head-of-wall and liner sequence."},
            {"id": "firestop", "title": "Firestopping",
             "why": "Dynamic head-of-wall joints need a listed joint system, not caulk."},
        ],
    },
    "hang": {
        "title": "Hanging Drywall",
        "summary": "Hang from a passed pre-board walk — correct board type, orientation, and fastening pattern.",
        "next_steps": [
            {"text": "Confirm the pre-board walk is done and rated hold-point photos are captured.",
             "anchor": "#preboard-walk"},
            {"text": "Run boards perpendicular to framing; stagger end joints and keep factory edges on outside corners.",
             "anchor": "#hang"},
            {"text": "Cut penetrations tight — sloppy holes break firestop annular-space limits.",
             "anchor": "#hang"},
            {"text": "Leave the room ready for scrape: no proud screws, no leftover mud ridges.",
             "anchor": "#scrape"},
        ],
        "related": [
            {"id": "preboard-walk", "title": "Pre-Board Walk",
             "why": "The gate before the first sheet goes up."},
            {"id": "stock-drywall", "title": "Stocking Drywall",
             "why": "Right board staged at the wall saves transitions."},
            {"id": "inspection-hold", "title": "Hold Points / Inspection Photos",
             "why": "Photo rated layers before cover."},
        ],
    },
    "scrape": {
        "title": "Scraping",
        "summary": "Remove squeeze-out and ridges while mud is still soft so finishers are not sanding your hang.",
        "next_steps": [
            {"text": "Scrape every joint and screw before tape — starved beds and proud fasteners fail in finish.",
             "anchor": "#scrape"},
            {"text": "Knife off squeeze-out at corners and boxes; do not leave a ridge for the taper to sand.",
             "anchor": "#scrape"},
            {"text": "Walk with a light at a low angle before calling the room ready for tape.",
             "anchor": "#scrape"},
            {"text": "Hand off only when the room is scraped — starting tape in an unscraped room is rework.",
             "anchor": "#finish"},
        ],
        "related": [
            {"id": "hang", "title": "Hanging Drywall",
             "why": "Proud screws and gaps upstream become scrape debt."},
            {"id": "finish", "title": "Finishing Drywall",
             "why": "The next Job Check stages after scrape."},
            {"id": "cleanout", "title": "Clean-Out Between Stages",
             "why": "Dust on fresh mud is a finish killer."},
        ],
    },
    "finish": {
        "title": "Finishing Drywall",
        "summary": "Three coats, each wider and thinner than the last, only over fully dry mud — finish to the specified GA-214 level.",
        "next_steps": [
            {"text": "Confirm the room is scraped and gaps are pre-filled with setting compound where needed.",
             "anchor": "#finish"},
            {"text": "Bed tape with a full coat behind it; squeeze excess out before it hardens.",
             "anchor": "#finish"},
            {"text": "Second and third coats only over dry mud — each pass wider, scraping ridges between.",
             "anchor": "#finish"},
            {"text": "Sand with a raking light; if spec calls for Level 5 or critical lighting, open level5-skim.",
             "anchor": "#finish/fin-levels"},
        ],
        "related": [
            {"id": "level5-skim", "title": "Level 5 / Text-Skim",
             "why": "Full skim when spec or raking light demands Level 5."},
            {"id": "qc-walk", "title": "QC Walk Before Done",
             "why": "Pass/fix before Job Check advances."},
            {"id": "scrape", "title": "Scraping",
             "why": "Unscraped hang becomes unpaid sanding."},
        ],
    },
    "level5-skim": {
        "title": "Level 5 / Text-Skim",
        "summary": "Full skim or specified texture only when the job is priced and specified for it — not a free upgrade over Level 4.",
        "next_steps": [
            {"text": "Confirm Level 5 or text/skim is in writing (spec, CO, or Job Check note) before skimming.",
             "anchor": "#level5-skim"},
            {"text": "Skim the whole plane, not spot patches — raking light shows partial skims.",
             "anchor": "#level5-skim"},
            {"text": "Prime every skimmed surface before paint; bare compound flashes.",
             "anchor": "#touchup-drywall"},
            {"text": "Match specified texture on tie-ins; sample and get approval.",
             "anchor": "#finish"},
        ],
        "related": [
            {"id": "finish", "title": "Finishing Drywall",
             "why": "Coat sequence and GA-214 levels live here."},
            {"id": "qc-walk", "title": "QC Walk Before Done",
             "why": "Raking-light pass before you claim skim complete."},
            {"id": "changeorder", "title": "Spotting a Change Order",
             "why": "Level 5 on a Level 4 bid is a CO, not a favor."},
        ],
    },
    "cleanout": {
        "title": "Clean-Out Between Stages",
        "summary": "Remove scrap, dust, and protection between trades so the next pass starts on a clean substrate.",
        "next_steps": [
            {"text": "Vacuum and scrape the floor; mud dust on fresh tape causes fisheyes and bond failure.",
             "anchor": "#cleanout"},
            {"text": "Pull protection only where the next trade needs access — leave floor protection until paint is done.",
             "anchor": "#cleanout"},
            {"text": "Bundle scrap and stack off the floor; paths clear for the next crew.",
             "anchor": "#cleanout"},
            {"text": "QC walk after clean-out before advancing Job Check to the next stage.",
             "anchor": "#qc-walk"},
        ],
        "related": [
            {"id": "qc-walk", "title": "QC Walk Before Done",
             "why": "Clean room + raking light before you claim done."},
            {"id": "finish", "title": "Finishing Drywall",
             "why": "Dust control between coat passes."},
            {"id": "scrape", "title": "Scraping",
             "why": "Upstream stage that feeds finish quality."},
        ],
    },
    "act": {
        "title": "Acoustical Ceilings",
        "summary": "Layout decides border tile size; level grid and square diagonals before any tile drops in.",
        "next_steps": [
            {"text": "Work tile layout from centerlines — equal borders, no sliver strips.",
             "anchor": "#act"},
            {"text": "Laser wall angle on a level line all the way around the room.",
             "anchor": "#act"},
            {"text": "Hang wires to structure only; three full wraps within 3 inches.",
             "anchor": "#act/act-maintee"},
            {"text": "Square the grid with diagonals before tile; fixtures independently supported.",
             "anchor": "#act"},
        ],
        "related": [
            {"id": "framing", "title": "Metal Stud Framing",
             "why": "Soffits and bulkheads need framing before grid."},
            {"id": "insulation", "title": "Insulation",
             "why": "Above-ceiling batts before tile if spec requires."},
            {"id": "qc-walk", "title": "QC Walk Before Done",
             "why": "Border tiles and level show from the doorway."},
        ],
    },
    "firestop": {
        "title": "Firestopping",
        "summary": "Build the listed system number exactly — dynamic at moving head-of-wall joints, photograph before cover.",
        "next_steps": [
            {"text": "Read the UL system on the drawings — HW-D means dynamic head-of-wall, not generic caulk.",
             "anchor": "#firestop/fs-numbers"},
            {"text": "Measure annular space; hole size must fall inside the listing min/max.",
             "anchor": "#firestop/fs-install"},
            {"text": "Pack mineral wool to listed depth, then fill with the specified sealant class.",
             "anchor": "#firestop/fs-install"},
            {"text": "Label and photograph every firestop before board or ceiling covers it.",
             "anchor": "#firestop"},
        ],
        "related": [
            {"id": "ratedwalls", "title": "Fire Walls & Shaftwall",
             "why": "Assembly type drives which joint systems apply."},
            {"id": "framing", "title": "Metal Stud Framing",
             "why": "Deflection track at the head-of-wall must move with the structure."},
            {"id": "hang", "title": "Hanging Drywall",
             "why": "Tight cutouts preserve annular space for penetrations."},
        ],
    },
    "preboard-walk": {
        "title": "Pre-Board Walk",
        "summary": "Gate before hang: backing, ROs, MEP conflicts, rated photo plan, and stop criteria written down.",
        "next_steps": [
            {"text": "Walk with drawings — verify backing, RO dimensions, and firestop access.",
             "anchor": "#preboard-walk"},
            {"text": "Mark hold points for rated walls, shaftliner, and insulation before second side.",
             "anchor": "#inspection-hold"},
            {"text": "Stop on missing backing, live drawing conflicts, or firestop not ready — RFI or CO path.",
             "anchor": "#preboard-walk"},
            {"text": "Only release hang when the walk checklist is complete and photos are planned.",
             "anchor": "#hang"},
        ],
        "related": [
            {"id": "hang", "title": "Hanging Drywall",
             "why": "Next trade stage after a passed walk."},
            {"id": "ratedwalls", "title": "Fire Walls & Shaftwall",
             "why": "Rated work needs photos before cover."},
            {"id": "changeorder", "title": "Spotting a Change Order",
             "why": "Extras found on the walk are not silent scope."},
        ],
    },
    "ratedwalls": {
        "title": "Fire Walls & Shaftwall",
        "summary": "Build the listed assembly exactly — liner/stud sequence on shafts, continuity per life safety plan.",
        "next_steps": [
            {"text": "Confirm wall type and height against the life safety plan, not habit.",
             "anchor": "#ratedwalls"},
            {"text": "Shaftwall: J-runner, stud, liner, stud — one bay at a time from the room side.",
             "anchor": "#ratedwalls/rw-shaft-install"},
            {"text": "Head-of-wall is a listed dynamic joint system where the partition moves.",
             "anchor": "#firestop"},
            {"text": "Photograph base layer, head-of-wall, and shaft side before anything is unreachable.",
             "anchor": "#inspection-hold"},
        ],
        "related": [
            {"id": "firestop", "title": "Firestopping",
             "why": "Joints and penetrations must match listed systems."},
            {"id": "framing", "title": "Metal Stud Framing",
             "why": "Deflection and bracing details feed rated heads."},
            {"id": "hang", "title": "Hanging Drywall",
             "why": "Layer count, offsets, and fasteners come from the listing."},
        ],
    },
    "changeorder": {
        "title": "Spotting a Change Order",
        "summary": "Stop, photograph, write it down, and call — extra work needs written authorization, not a verbal promise.",
        "next_steps": [
            {"text": "Run the three-question test: on drawings, in contract, or already ours?",
             "anchor": "#changeorder"},
            {"text": "Stop work on the extra scope; photo conditions same day.",
             "anchor": "#changeorder"},
            {"text": "Draft T&M with hours, materials, and signatures before leaving site.",
             "anchor": "#changeorder"},
            {"text": "Never advance Job Check to cover work that is not authorized.",
             "anchor": "#portal-field-tools"},
        ],
        "related": [
            {"id": "jobstart-firstday", "title": "Job Start → First Day",
             "why": "Open questions and exclusions start in the handoff packet."},
            {"id": "qc-walk", "title": "QC Walk Before Done",
             "why": "Punch items are not free extras."},
            {"id": "ai-field-rules", "title": "How We Work With AI",
             "why": "Assistants flag — humans authorize."},
        ],
    },
    "stock-drywall": {
        "title": "Stocking Drywall",
        "summary": "Stage the right board at the wall, flat and supported — floor load and edge damage show up in hang.",
        "next_steps": [
            {"text": "Read the stack label — thickness, type, and edge before you carry.",
             "anchor": "#stock-drywall"},
            {"text": "Stock standing on edge, tight to the wall line, not in traffic paths.",
             "anchor": "#stock-drywall"},
            {"text": "Use two-person carries on 5/8 and 12-foot sheets; protect finished corners.",
             "anchor": "#stock-drywall"},
            {"text": "Confirm hang crew has the board they need before they climb — walking is the tax.",
             "anchor": "#hang"},
        ],
        "related": [
            {"id": "stock-general", "title": "Stocking Any Material",
             "why": "General staging rules for all trades."},
            {"id": "hang", "title": "Hanging Drywall",
             "why": "Consumer of what you staged."},
            {"id": "preboard-walk", "title": "Pre-Board Walk",
             "why": "Verify board types match spec before first sheet."},
        ],
    },
    "qc-walk": {
        "title": "QC Walk Before Done",
        "summary": "Raking light across every plane — pass or fix before Job Check moves to the next stage.",
        "next_steps": [
            {"text": "Hold a bright light nearly parallel to the surface; one wall at a time.",
             "anchor": "#qc-walk"},
            {"text": "Mark outside defects with tape or pencil — never marker on finished board.",
             "anchor": "#touchup-drywall"},
            {"text": "Sort fixes: touch-up, skim, or send back to the owning trade.",
             "anchor": "#qc-walk"},
            {"text": "Re-walk after fixes; only then update Job Check from the field tablet.",
             "anchor": "#portal-field-tools"},
        ],
        "related": [
            {"id": "finish", "title": "Finishing Drywall",
             "why": "Most QC defects are finish or scrape debt."},
            {"id": "touchup-drywall", "title": "Drywall Touch-Up",
             "why": "Fix the wall before paint."},
            {"id": "cleanout", "title": "Clean-Out Between Stages",
             "why": "Dust hides defects in flat light."},
        ],
    },
    "jobstart-firstday": {
        "title": "Job Start → First Day",
        "summary": "Read the Ops-accepted handoff — lock box, exclusions, open questions, and scope before the crew mobilizes.",
        "next_steps": [
            {"text": "Open the Job Start packet PDF — exclusions and open questions are not optional reading.",
             "anchor": "#jobstart-firstday"},
            {"text": "Confirm lock box, scaffold, and heat/can notes match what Ops accepted.",
             "anchor": "#jobstart-firstday"},
            {"text": "Walk the site against scope — verbal adds go to change order, not Job Check fiction.",
             "anchor": "#changeorder"},
            {"text": "First Job Check save is logistics and trade status only — never invent Monday values.",
             "anchor": "#portal-field-tools"},
        ],
        "related": [
            {"id": "changeorder", "title": "Spotting a Change Order",
             "why": "Handoff open questions become COs when ignored."},
            {"id": "preboard-walk", "title": "Pre-Board Walk",
             "why": "Site readiness gate before board."},
            {"id": "portal-field-tools", "title": "Portal / Job Check",
             "why": "How crew updates trade status without inventing scope."},
        ],
    },
    "closeout-rhythm": {
        "title": "Closeout Rhythm",
        "summary": "Finish the job the way the office expects: punch, photos, clean-out, and honest dates — not a surprise Completion Date.",
        "next_steps": [
            {"text": "Run a QC walk with raking light before anyone sets Completion Date.",
             "anchor": "#qc-walk"},
            {"text": "Clean-out and photo the punch list; leave paths clear for paint and final.",
             "anchor": "#cleanout"},
            {"text": "Capture hold-point / after photos while conditions still match the work.",
             "anchor": "#inspection-hold"},
            {"text": "Only then update Job Check dates — never invent a finish date to clear a chip.",
             "anchor": "#closeout-rhythm"},
        ],
        "related": [
            {"id": "qc-walk", "title": "QC Walk Before Done",
             "why": "Pass/fail before closeout claims."},
            {"id": "cleanout", "title": "Clean-Out Between Stages",
             "why": "Final clean is part of closeout."},
            {"id": "portal-field-tools", "title": "Portal / Job Check",
             "why": "Where Completion Date and notes land."},
        ],
    },
    "scaffold-lifts": {
        "title": "Scaffold & Lifts",
        "summary": "Stage access for the work — inspect daily, never overload, and keep scrap off platforms.",
        "next_steps": [
            {"text": "Confirm the right access for the height and load before the crew climbs.",
             "anchor": "#scaffold-lifts"},
            {"text": "Daily visual: base plates, braces, guardrails, tag current.",
             "anchor": "#scaffold-lifts"},
            {"text": "Keep platforms clear of board scrap and mud buckets that tip.",
             "anchor": "#scaffold-lifts"},
            {"text": "Report damaged equipment — do not \"make it work\" for one more bay.",
             "anchor": "#safety-orient"},
        ],
        "related": [
            {"id": "safety-orient", "title": "Safety Orientation",
             "why": "Ladders, lifts, and hard stops."},
            {"id": "stock-drywall", "title": "Stocking Drywall",
             "why": "Stage material so lifts are not used as carts."},
            {"id": "hang", "title": "Hanging Drywall",
             "why": "Access height drives hang sequence."},
        ],
    },
    "job-conditions": {
        "title": "Job Conditions (Heat / Cans)",
        "summary": "Temperature and temporary power decide whether mud, ACT, and paint can go — flag bad conditions early.",
        "next_steps": [
            {"text": "Check heat, power, and weather protection before mobilizing finish or ACT.",
             "anchor": "#job-conditions"},
            {"text": "If the building is too cold/hot/wet for the product, stop and escalate — do not hope.",
             "anchor": "#escalate"},
            {"text": "Record heater/can needs in Job Check logistics so Ops can stage them.",
             "anchor": "#portal-field-tools"},
            {"text": "Protect finished work from overnight temp swings and dust.",
             "anchor": "#protection"},
        ],
        "related": [
            {"id": "escalate", "title": "When to Escalate",
             "why": "Conditions that block production."},
            {"id": "finish", "title": "Finishing Drywall",
             "why": "Mud chemistry cares about temperature."},
            {"id": "act", "title": "Acoustical Ceilings",
             "why": "Grid and tile hate wet/cold rooms."},
        ],
    },
    "window-returns": {
        "title": "Window Returns",
        "summary": "Returns are priced and specified — match the Job Start detail; extras are a change order.",
        "next_steps": [
            {"text": "Confirm return depth and wrap from Job Start / drawings before framing the opening.",
             "anchor": "#window-returns"},
            {"text": "Build returns plumb and square; backing where hardware or blinds land.",
             "anchor": "#window-returns"},
            {"text": "Photo the return detail before hang closes it when the opening is rated or atypical.",
             "anchor": "#inspection-hold"},
            {"text": "If the GC asks for a deeper wrap than bid, stop and write the CO.",
             "anchor": "#changeorder"},
        ],
        "related": [
            {"id": "framing", "title": "Metal Stud Framing",
             "why": "RO and return framing live here."},
            {"id": "changeorder", "title": "Spotting a Change Order",
             "why": "Return upgrades are classic extras."},
            {"id": "jobstart-firstday", "title": "Job Start / First Day",
             "why": "Where return decisions should already be recorded."},
        ],
    },
}

FALLBACK_COACH: dict[str, Any] = {
    "title": "Field Manual",
    "summary": "Pick a procedure from the home tiles or open the checklist coach from any procedure page.",
    "next_steps": [
        {"text": "Return to the home tiles and choose the trade procedure you are on.",
         "anchor": "#home"},
        {"text": "Read How We Work With AI for what assistants may and may not do on site.",
         "anchor": "#ai-field-rules"},
        {"text": "Use Job Check only for explicit taps — never let a coach or bot advance a column.",
         "anchor": "#portal-field-tools"},
    ],
    "related": [
        {"id": "onboard-week1", "title": "Your First Week",
         "why": "Orientation path for new crew."},
        {"id": "dont", "title": "Don't Do This",
         "why": "Common failure modes with links to fixes."},
        {"id": "ai-field-rules", "title": "How We Work With AI",
         "why": "Hard rules for assists on site."},
    ],
}


def normalize_procedure_id(raw: Optional[str]) -> Optional[str]:
    """Strip hash, lower-case, map empty to None."""
    if not raw:
        return None
    proc = raw.strip().lower()
    if proc.startswith("#"):
        proc = proc[1:]
    if "/" in proc:
        proc = proc.split("/", 1)[0]
    return proc or None


def resolve_procedure_id(
    procedure: Optional[str] = None,
    stage: Optional[str] = None,
    column_id: Optional[str] = None,
    board: Optional[str] = None,
    anchor_resolver=None,
) -> Optional[str]:
    """
    Pick the best procedure id from explicit id, stage alias, or Job Check column.
    `anchor_resolver(column_id, board)` returns an anchor like '#hang' or None.
    """
    proc = normalize_procedure_id(procedure)
    if proc and proc in PROCEDURE_COACH:
        return proc

    if stage:
        alias = STAGE_ALIASES.get(stage.strip().lower())
        if alias:
            return alias

    if column_id and anchor_resolver:
        brd = (board or "projects").strip().lower()
        anchor = anchor_resolver(column_id.strip(), brd)
        if anchor:
            from_col = normalize_procedure_id(anchor)
            if from_col:
                return from_col

    if proc:
        return proc
    return None


def coach_payload(procedure_id: Optional[str]) -> dict[str, Any]:
    """Build the coach content dict for one procedure (or fallback)."""
    if procedure_id and procedure_id in PROCEDURE_COACH:
        entry = PROCEDURE_COACH[procedure_id]
        return {
            "procedure": procedure_id,
            "title": entry["title"],
            "summary": entry["summary"],
            "next_steps": list(entry["next_steps"]),
            "related": list(entry["related"]),
        }
    fb = FALLBACK_COACH
    return {
        "procedure": procedure_id or "unknown",
        "title": fb["title"],
        "summary": fb["summary"],
        "next_steps": list(fb["next_steps"]),
        "related": list(fb["related"]),
    }


def build_coach_response(
    procedure: Optional[str] = None,
    stage: Optional[str] = None,
    column_id: Optional[str] = None,
    board: Optional[str] = None,
    *,
    anchor_resolver=None,
    column_label_lookup=None,
) -> dict[str, Any]:
    """Assemble the full API response envelope."""
    proc_id = resolve_procedure_id(
        procedure=procedure,
        stage=stage,
        column_id=column_id,
        board=board,
        anchor_resolver=anchor_resolver,
    )
    body = coach_payload(proc_id)
    known = proc_id is not None and proc_id in PROCEDURE_COACH

    jobcheck_hint = None
    if column_id and anchor_resolver:
        brd = (board or "projects").strip().lower()
        anchor = anchor_resolver(column_id.strip(), brd)
        if anchor:
            label = None
            if column_label_lookup:
                label = column_label_lookup(column_id.strip(), brd)
            jobcheck_hint = {
                "column_id": column_id.strip(),
                "label": label or column_id.strip(),
                "anchor": anchor,
            }

    return {
        "ok": True,
        "known": known,
        **body,
        "jobcheck_hint": jobcheck_hint,
        "bans": list(COACH_BANS),
    }
