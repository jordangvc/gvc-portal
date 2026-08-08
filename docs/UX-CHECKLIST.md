# UX Consistency Checklist

**Required for every portal UI change.** Paste into the PR description or
confirm before merge. Full rules: `docs/UI-SYSTEM.md`.

## Design

- [ ] Colors / spacing / type / radius come from `web/gvc.css` tokens
- [ ] No new hex or magic px when a token exists
- [ ] No gradients; commitment actions use solid gold (`.btn--commit` / `.btn.gold`)
- [ ] Reused existing button / card / banner / savebar pattern — no new dialect
- [ ] Page opted into `data-palette="emerald"` (or documented exception)

## Shell & navigation

- [ ] `gvc-topbar` present (tools) or hub shell (hub only)
- [ ] Brand links to `/`
- [ ] Page purpose clear in header within 3 seconds
- [ ] Safe back path (hub, list, or cancel)

## Actions & flow

- [ ] One obvious primary action
- [ ] Editable pages: save/cancel or sticky commit bar
- [ ] Multi-step: next/back handling
- [ ] Dirty leave: flush autosave or confirm discard
- [ ] Success state includes a **Next:** link when another tool continues the job
- [ ] Blocked / ungranted / unconfigured states explain recovery

## States

- [ ] Loading feedback on async primary actions
- [ ] Empty state with guidance (`.gvc-empty` or equivalent)
- [ ] Error state with what to do next
- [ ] Disabled looks disabled (not a fake button)
- [ ] Partial failure names what landed vs what failed

## Mobile

- [ ] Usable at ~375px width
- [ ] Interactive targets ≥48px on phone widths
- [ ] Sticky bars respect `safe-area-inset-bottom`
- [ ] Critical actions not trapped below the fold without a sticky bar

## Validation

```bash
python scripts/ui_consistency_check.py
```

- [ ] Consistency script passes (or failures justified in PR)
- [ ] Manually clicked the primary happy path once
- [ ] Manually triggered one failure path (network / validation) and recovered
