# Job Check — design (Jordan, Jul 27, 2026)

Jordan: field guys open the portal, pick an active project from a dropdown
of everything on Monday, "it pulls up everything on there, and then they
can go through and check boxes that will update all of the columns or fill
in all of the columns that they need on Monday — really simple, easy and
quick to use." Priority: "start laying that out and working on it right
away. That's really valuable."

## What it is

The portal's first WRITE surface into Monday: a mobile-first quality-check
/ work-completion page. Monday stays the source of truth — this is a
faster pair of hands updating it, replacing "open the Monday app, find the
item, hunt the column" with three taps in the portal.

## Flow

1. `/ui/jobcheck` (grant key `jobcheck`; field-crew friendly, big targets).
2. Dropdown = active items from the Projects board (reuse the paged
   read-only fetch built for lien_watch; exclude closed/dead groups).
3. Pick a job → the portal renders that item's EDITABLE columns as a
   checklist form: status columns as tap-to-cycle chips, checkbox columns
   as big checkboxes, date columns as date pickers, text/long-text as
   inputs, numbers as numeric fields. Read-only context (address, builder,
   links) shown at top.
4. Column allowlist + display order live in `shared/boards.py` config —
   NOT auto-everything: start with the columns the crew actually fills on
   a quality check / completion pass (Jordan/Andrea confirm the list from
   the board; ship with a sensible default and an easy config edit).
5. "Save to Monday" (one button, gold commitment CTA): writes via
   `change_item_column_values` in one mutation, then re-reads the item and
   shows the confirmed values. No silent partial writes — failures list
   exactly which columns didn't land.
6. Every save logs to the portal activity store (who, item, columns
   changed, old→new) — the audit trail Monday's own log won't give us
   per-portal-user.

## Guardrails

- Writes happen ONLY on the user's explicit Save tap — no background
  writes, no automation, no drafts-to-approve needed (a signed-in human
  updating the source of truth through a form is the intended use).
- The page never creates or deletes items in v1 — column updates on
  existing items only.
- Grant-gated like every tool; crew members get `jobcheck` without
  getting billing tools.
- Column allowlist prevents the form from touching money/contract columns
  (Contract Value, invoice links) even if Monday would allow it.

## Later (not v1)

- Photo attach on a check item (crew photo → Drive folder + Monday update).
- Punch-list mode (repeating check templates per job type).
- Takeoff-app tie-in: surface job-check status on the drafts tile.
