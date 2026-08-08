# UI System Audit — GVC Portal

**Date:** 2026-08-08  
**Scope:** All `web/*.html` portal surfaces + `web/gvc.css`  
**Status:** Living doc — update when a system fix lands.

---

## 1. Current audit findings

### Design system state

The portal **has** a design system (`web/gvc.css` + `docs/GVC-COMMAND-STYLE.md`)
but is **mid-migration**. Live pages still ship large page-local `<style>`
blocks and three overlapping button/card dialects.

| Layer | Reality |
|---|---|
| Tokens | Strong `--gvc-*` + Command `--color-*` (emerald) |
| Shared CSS | One file: `gvc.css` (~1.7k+ lines) |
| HTML includes | **None** — every page is a full document |
| Hub shell | Custom rail/dock (`hub.html`) |
| Tool shell | `gvc-topbar` + `gvc-shell` (most tools) |
| Field Manual | Parallel private token fork inside `fieldguide.html` |

### UI surfaces

Hub, Morning (+ GM / Owner), Estimate, Invoice, Change Order, Billing, Paid by
Check, Job Start, Job Check, COI, Lien, Field Manual, Training, Time Off,
Admin, Activity. PDF templates under `templates/` are **out of scope** for
portal chrome.

---

## 2. UI inconsistencies found

1. **Three button systems:** `.btn--primary` (Command), `.btn.gold` (money),
   `.gvc-btn--*` (admin/billing).
2. **Page-local `.btn` overrides** shadow Command styles on money pages.
3. **Dual cards:** `.card` vs `.gvc-card`.
4. **Sticky bars disagree:** `.gvc-savebar` vs `.savebar`; Job Check used a
   gold **gradient** (forbidden by Command).
5. **Two shells:** Hub rail vs tool topbar — tool discovery depends on hub.
6. **Money-form CSS cloned** across estimate/invoice/CO/check/coi/jobstart.
7. **Theme script loaded twice** on many pages; Training lacked `gvc-theme.js`.
8. **Undefined token aliases** historically referenced (`--gvc-radius`,
   `--gvc-fs-sm`) — now aliased in `gvc.css`.
9. **Breakpoint soup:** 640 / 720 / 767 / 46rem.
10. **Field Manual private palette** (`--ink`, `--green`, …) vs portal tokens.

---

## 3. Flow blockers found

See `docs/UI-FLOW-AUDIT.md` for the full list. Highest impact:

- Morning Optimize did not sync Google Maps button
- Job Start “Change bid” dropped debounced autosave
- Hub dimmed tools were silent spans (no toast)
- Billing activity empty copy claimed the feed “isn't wired”
- Config-gated pages (Time Off / COI template / Admin GCS) can open as cliffs

---

## 4. System rules to enforce

Documented in `docs/UI-SYSTEM.md` §1–5 and `docs/UX-CHECKLIST.md`.

---

## 5. Proposed fixes (system-level first)

| Priority | Fix | Status |
|---|---|---|
| P0 | Token aliases + shared `.btn--commit` / `.savebar` | This PR |
| P0 | Dead-end flow fixes (Morning / Job Start / Hub / Billing) | This PR |
| P1 | Kill Job Check gradient; reuse shared savebar | This PR |
| P1 | Training theme bootstrap parity | This PR |
| P1 | `scripts/ui_consistency_check.py` | This PR |
| P2 | Extract money-form shared CSS from page clones | Next |
| P2 | Single server-side HTML shell include | Next |
| P3 | Field Manual token convergence | Later |
| P3 | Compact tool-nav drawer on tool pages | Later |

---

## 6. Code changes (this pass)

- `web/gvc.css` — aliases, commit buttons, savebar/empty/error helpers
- `web/morning.html` — Maps sync + mutation error feedback
- `web/jobstart.html` — flush autosave on leave; self-sent recovery copy
- `web/hub.html` — toast on dimmed tool tap
- `web/billing.html` — honest activity empty/error copy
- `web/jobcheck.html` — solid gold commit (no gradient)
- `web/training.html` — theme script + FOUC parity
- Docs + `scripts/ui_consistency_check.py`

---

## 7. Validation checks

```bash
python scripts/ui_consistency_check.py
# smoke primary pages in browser: Hub → Morning → Job Check → Job Start
```

---

## 8. Remaining risks / gaps

- Money pages still redefine `.btn` locally (JS class names) — visual drift risk
  until CSS is extracted.
- No HTML partials yet — shell drift can return with copy-paste.
- Field Manual remains a large private system.
- Config cliffs (Time Off URL, COI template, Admin GCS) need hub “needs setup”
  badges (not done in this pass).
