# GVC Portal UI System

**Contract for every portal page.** Visual style still lives in
`docs/GVC-COMMAND-STYLE.md` + `web/gvc.css`. This file is the **product-system**
layer: shells, actions, flows, states, and how we stop UI drift.

Companion docs:

| Doc | Purpose |
|---|---|
| `docs/GVC-COMMAND-STYLE.md` | Tokens, art direction, Command components |
| `docs/UI-DARK-MODE.md` | Dark theme tokens, contrast rules, integration UI |
| `docs/UI-AUDIT.md` | Current visual/consistency audit |
| `docs/UI-FLOW-AUDIT.md` | Dead ends + continuation gaps |
| `docs/UX-CHECKLIST.md` | Required checklist for every UI change |

---

## 1. Design language (single source of truth)

- **Tokens:** `:root` in `web/gvc.css` (`--gvc-*` always; `--color-*` when
  `data-palette="emerald"`).
- **No new hex / px** in page `<style>` when a token exists.
- **No gradients** in portal UI (Command rule). Commitment actions use solid
  `--gvc-gold`.
- **Aliases that must stay defined:** `--gvc-radius`, `--gvc-r-md`,
  `--gvc-fs-sm`, `--gvc-green-tint`, `--bp-phone`, `--bp-tablet`,
  `--z-topbar`, `--z-savebar`, `--z-modal`.

### Button vocabulary (pick ONE pattern per role)

| Role | Preferred class | Also accepted (legacy) |
|---|---|---|
| Primary continue | `.btn--primary` or `.gvc-btn--primary` | `.btn` (money pages, green fill) |
| Commitment / Save / Accept | `.btn--commit` | `.btn.gold`, `.gvc-btn--gold` |
| Secondary | `.btn--secondary` | `.btn.secondary`, `.gvc-btn--ghost` |
| Quiet / tertiary | `.btn--ghost` | `.gvc-btn--quiet`, `.linkish` |

Do **not** invent a fourth button style for the same role.

---

## 2. Page shell standard

Every tool page (`/ui/*` except Hub) should follow:

```
gvc-topbar          brand → hub home · page title · theme · sign out
gvc-shell           page frame
  gvc-page-hd       eyebrow · h1 · one-line purpose (gvc-sub)
  status area       gvc-banner / setStatus (errors + next steps)
  main content      gvc-card / .card sections
  gvc-page-actions  or sticky .gvc-savebar / .savebar for commit
```

Rules:

1. Brand wordmark always links to `/` (hub).
2. Page purpose is obvious in the header within 3 seconds.
3. Primary action is visually obvious (green or gold commitment).
4. Editable pages use a sticky save/accept bar when the primary action must
   survive scroll (` .gvc-savebar` / `.savebar`).
5. Load `gvc.css` once; theme FOUC boot once; `gvc-theme.js` once in `<body>`.

Hub keeps its own rail/dock shell (`hub.html`) — that is intentional.

Field Manual may keep document-specific chrome, but must reuse portal tokens
for brand colors and must not introduce a second button language for portal
actions (Job Check links, etc.).

---

## 3. Flow rules (no dead ends)

Every page must answer:

- Where am I?
- What is this for?
- What do I do next?
- How do I go back?
- How do I save / continue?
- What if data is missing?
- What if the action fails?

Hard rules:

1. **Every page has a clear primary action** (or an explicit read-only reason).
2. **Every blocked state has a recovery path** (retry, open hub, ask admin,
   alternate tool link).
3. **Every async action** shows loading → success or error with a next step.
4. **Cross-tool handoffs** end with a “Next:” link (Job Start, Job Check,
   Billing, Paid by Check, hub) — see Change Order’s “Bill this CO” pattern.
5. **Dirty leave** flushes autosave or confirms discard (Job Check pattern).
6. **Ungranted / unconfigured tools** explain how to get unblocked (toast or
   banner) — never a silent dead click.

---

## 4. State completeness

For shared controls and key screens, support:

| State | Expectation |
|---|---|
| default / hover / focus / active / disabled | Token-driven |
| loading | Disable primary; show “Saving…” / spinner |
| empty | `.gvc-empty` with what to do next |
| error | Message + recovery action |
| success | Confirm what landed + next link |
| no-permission | Banner / toast → ask admin |
| partial-data | Save what can; name what failed |
| offline / backend down | Cached read when possible; else clear failure |

---

## 5. Enforcement

### Before writing new UI

1. Reuse `gvc.css` primitives — do not copy a money-page `<style>` block.
2. Prefer Command classes (`.btn--*`, `.chip`, `.card`) or legacy `.gvc-*`,
   not a new dialect.
3. Run `docs/UX-CHECKLIST.md`.
4. Run `python scripts/ui_consistency_check.py` (anti-pattern lint).

### Forbidden

- Page-specific button styles for the same role as an existing primitive
- Sticky bars with `linear-gradient`
- Critical actions only available below the fold with no sticky bar
- Primary buttons that look enabled but have no handler
- Success screens with no “what’s next” link into the money spine

### When backend is missing

Wire it, **or** disable clearly with explanation, **or** route to the page
that can finish the job. Never leave a control that silently no-ops.

---

## 6. Repair loop

1. **Audit** — UI-AUDIT + FLOW-AUDIT
2. **Normalize** — tokens / shared components
3. **Complete** — missing actions + states
4. **Validate** — UX checklist + consistency script + primary flows
5. **Refine** — polish without new dialects
6. **Re-check** — update audits; keep checklist green

---

## 7. Rollout priority

1. Tokens + button/savebar aliases in `gvc.css` (done in this system pass)
2. Dead-end flow fixes (Morning Maps, Job Start leave-save, hub dim toast, …)
3. ~~Extract remaining money-page CSS clones into `gvc.css` / shared sheet~~ **Done (r97)**
4. One HTML shell include (server-side) when we stop shipping full documents
5. Field Manual token convergence (keep content; drop private greens)
