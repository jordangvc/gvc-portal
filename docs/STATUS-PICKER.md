# Status Picker (Job Check)

Replaces the wall of chips on Job Check status columns with a one-value-per-field
summary that opens into a searchable, grouped picker.

**Live:** `web/gvc-status-picker.js` · CSS `.sp*` in `web/gvc.css` · wired in
`web/jobcheck.html`. Tokens from `docs/GVC-COMMAND-STYLE.md`.

## Pattern

- **Closed:** label · gold current chip · Change
- **Open:** search → **Next up** (green) → phase groups (collapsed; current group open + gold dot)
- Tap active value to clear; selecting a value closes the panel
- **Colors:** gold = selected · green = next · neutral = else — never Monday hex

## Groups

Configured for the three Ops columns that had the unreadable walls:

| fieldKey | Column |
|---|---|
| `ops:status` | Stage |
| `ops:color_mm1hmwdm` | Stage Detail |
| `ops:color_mm1hrm6z` | Blocked (QUICK suggestions) |

Other status columns (trade statuses, Scheduled Day, Window type, …) get a single
**All statuses** group from live Monday labels. Any live label missing from config
lands in **Other** (and warns in the console).

## Row rhythm (r43)

Closed rows are a three-part flex line so every `Change` button shares the same
x-position:

1. `.sp__meta` is `flex: 1 1 auto` with `min-width: 0` (takes leftover width)
2. `.sp__toggle` is `flex: 0 0 auto` (never grows / never wraps)
3. Labels use the mono uppercase `.sp__label` kicker

Date fields (Stage Completion / Full Completion / Start Date / trade Completion
Date) use the same shell: mono kicker over a pill `.sp__date` input — not
square-cornered controls next to rounded chips.

## Monday hygiene (human)

Still worth cleaning on the board itself (duplicates, Detail values in Stage,
Meeting/Admin/OOO on Stage). The picker hides the wall; retiring bad labels makes
“Next up” more accurate.
