# GVC Portal — Dark Mode & Contrast Contract

Companion to `docs/UI-SYSTEM.md` and `docs/GVC-COMMAND-STYLE.md`.

Theme toggle lives in `web/gvc-theme.js`. Every emerald page sets
`data-palette="emerald"` and `data-theme="light|dark"` on `<html>`.

## Rules (non-negotiable)

1. **Normal text ≥ 4.5:1** against its actual background. Large text ≥ 3:1.
2. **No hard-coded text colors** in page CSS/JS when a semantic token exists.
3. **Dark mode is not light inverted.** Soft status surfaces keep readable *ink*
   tokens (`--color-*-ink`), not light-mode hexes like `#B91C1C` / `#9A3412`.
4. **Inputs use `--color-input-bg`**, never bare `#fff`.
5. **Status / sync / writeback copy** uses `.gvc-msg--danger|warn|muted|ok|info`.
6. **Soft panels** (mismatch, multi-check, link prompts) use `.gvc-callout--warn|danger|ok|info`.
7. Never invent page-local aliases like `--warn-bg` / `--line` / `--muted` outside
   Field Manual — lint fails them. Use `--gvc-*` / `--color-*`.
8. Test **both** themes before shipping UI.

## Semantic tokens (emerald)

| Role | Token | Notes |
|---|---|---|
| Page / surface | `--color-bg`, `--color-surface`, `--color-surface-2` | Hierarchy, not one flat gray |
| Text | `--color-text`, `--color-text-muted`, `--color-text-faint`, `--color-text-disabled` | Faint must stay readable |
| On fills | `--color-on-primary`, `--color-on-accent` | Button label ink |
| Status fills | `--color-*-soft` | Chip / banner backgrounds |
| Status ink | `--color-danger-ink`, `--color-warn-ink`, `--color-info-ink`, `--color-ok-ink` | Text *on* soft fills |
| Status borders | `--color-*-border` | Chip / banner edges |
| Forms | `--color-input-bg` | Light = white; dark = surface-2 |

Legacy `--gvc-*` bridges remapped under emerald. Prefer `--color-*` for new work.

## Integration surfaces (Monday / Fireflies / GitHub)

| Source | Portal surfaces | Pattern |
|---|---|---|
| **monday.com** | Job Check status, money writebacks, Job Start, Lien, Billing | Same chips/banners as rest of portal; board label hex only via luminance helper for *status control fill*, not body text |
| **Fireflies** | Morning Brief, GM huddle cards | Same card / chip / muted meta as Hub tools — no transcript-only palette |
| **GitHub** | No in-portal UI today | Keep it that way; docs/workflows only |

Do **not** invent a second visual language for sync errors, IDs, timestamps, or
outbound “Open in Monday” links. Use `.gvc-msg--*`, `.gvc-chip--*`, `.gvc-pill--*`,
and shared page shell.

## Repeatable audit

```bash
# Static anti-patterns (gradients, missing shell, light-only hex traps)
python scripts/ui_consistency_check.py

# Manual pass (every changed screen)
# 1. Toggle theme (topbar) light → dark
# 2. Check: body, muted, labels, placeholders, chips, buttons, tables,
#    banners, empty/error, focus ring, modals/drawers
# 3. Integration panels: Monday writeback lines, Fireflies notes, sync fail
```

### Fail the PR if you see

- `background:#fff` / `color:#fff` on content (print `@media` exempt)
- Inline `style="color:#b91c1c"` / `#96763B` / `#777` / `#7A1F1F`
- `.badge.active` / `.pill.open` still on `#e0f2fe` / `#1A72B8`
- Warn/danger chip ink stuck on light-mode oranges/reds in dark theme
- One-off `@media (prefers-color-scheme)` fighting `data-theme`

## Preferred fix order

1. Shared tokens + utilities in `web/gvc.css`
2. Kill page hex clones → tokens / `.gvc-msg--*`
3. Extend `scripts/ui_consistency_check.py`
4. Field Manual / large private palettes last (`fieldguide.html`)
