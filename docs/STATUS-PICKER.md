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

## Monday hygiene (human)

Still worth cleaning on the board itself (duplicates, Detail values in Stage,
Meeting/Admin/OOO on Stage). The picker hides the wall; retiring bad labels makes
“Next up” more accurate.
