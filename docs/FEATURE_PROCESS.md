# GVC Portal — Feature Process

**Canonical entry point.** Every change to this portal follows this process.
Read `docs/DESIGN_SYSTEM.md` first for the UI rules; this file is the workflow
and the definition of done.

## 1. How changes land

- **Branch, never master.** Branches named `cursor/<topic>` are squash-merged
  by `.github/workflows/auto-merge-cursor-prs.yml` **only after**
  `compileall` + the full pytest suite pass in CI. Master auto-deploys to
  Cloud Run (`deploy-cloud-run.yml`).
- To **pause** auto-merge on a PR: label `hold` or `do-not-merge`.
- Small, logical commits. Bump the hub footer `rN` (and its pinned test
  assertions in `tests/test_hub_home.py` / `tests/test_hub_stash.py`) in the
  same commit as any user-visible change, and add a dated note to `CLAUDE.md`.
- Deploys are reversible: Cloud Run → Revisions → route 100% to the previous.

## 2. Before you write code

1. Read `docs/DESIGN_SYSTEM.md` and this file.
2. Read `docs/APP_MAP.md` — know which **roles** the change touches and what
   grant guards each affected route.
3. Respect the layering: `app → orchestrators → subsystems/adapters → shared`
   (contracts in each package's `__init__.py`; orientation in `AGENTS.md`).
4. Check the **locked architecture** list in `AGENTS.md` ("do not change
   without explicit ask") — drafts-only Gmail, no Stripe emails, Monday as
   source of truth, retainage rules, and the rest. These override everything.

## 3. Definition of done (paste into the PR)

- [ ] **Tokens only** — styling from `gvc-ui.css` tokens/components (+
      `gvc-forms.css` on money forms); no new hex/px where a token exists; no
      page links legacy `gvc.css`; any new component added to
      `DESIGN_SYSTEM.md` §5 in the same PR
- [ ] **Mobile baseline holds** — the concerns in
      `tests/test_mobile_baseline.py`: safe-area viewport meta on every page,
      ≥16px inputs on phones, ≥44px targets (48px primary), no horizontal
      page scroll; usable at 375px
- [ ] **Roles & permissions** — every new/changed route enforces its grant
      **server-side** (`require_feature` / `require_admin`); named which roles
      the change affects; `docs/APP_MAP.md` updated when routes, grants, or
      presets change
- [ ] **Role-home invariant holds** — `tests/test_role_home_reachable.py`
      green (every admin preset lands on a page it can open); if you touched
      `resolve_role`, `ROLE_PRESETS`, or any page guard, extend that test's
      `PAGE_FEATURE` map
- [ ] **States** — loading / empty / error / partial-failure present per
      `DESIGN_SYSTEM.md` §7; success links the next tool on the money spine;
      stale or cached data carries an honest "as of" stamp
- [ ] **Template tokens** — any `{{TOKEN}}` added to a page is substituted by
      its route (guarded by `tests/test_forms_redesign.py` on money forms —
      extend the pattern for other pages)
- [ ] **Tests** — new logic has tests; **full suite green in CI** (the gate
      runs it; don't merge around it)
- [ ] **Consistency lint** — `python scripts/ui_consistency_check.py` passes
      or failures are justified in the PR
- [ ] **UI changes: screenshot evidence** at the four viewports
      (375×667 / 390×844 / 768×1024 / 1280×800) for touched screens —
      `python scripts/screenshot_portal.py` (optionally `--roles <ids>`),
      output committed under `docs/screenshots/` (latest run only)

## 4. Feature request template

```
WHAT       one sentence — what it does
WHO        which roles use it (owner / gm / office / sales / field)
WHERE      which screens/routes it touches (new routes: name the grant)
DATA       what it reads/writes (Monday? GCS? Drive? Stripe? Gmail?)
DONE WHEN  how we'll know it works (the observable outcome, not "code merged")
```

## 5. Verification commands

```bash
# full suite (CI runs this on every PR)
python -m pytest -q

# targeted invariants
python -m pytest tests/test_mobile_baseline.py tests/test_role_home_reachable.py -q

# UI lint
python scripts/ui_consistency_check.py

# Monday latency instrumentation (hub/billing JSON gains monday_trace)
GVC_MONDAY_TRACE=1
```

```bash
# screenshot evidence — per-role, four viewports → docs/screenshots/
# one-time setup: pip install -r requirements-dev.txt && python -m playwright install chromium
python scripts/screenshot_portal.py               # full matrix (wipes prior run)
python scripts/screenshot_portal.py --roles ops   # touched roles only
python scripts/screenshot_portal.py --throttle    # 4G evidence pass (role homes)
```

The harness runs the app locally with a throwaway session secret and
harness-process-only grant patches (approved design, 2026-08-09) — production
auth is never involved. Pages render degraded/empty states locally by design;
`docs/screenshots/INDEX.md` records findings, console errors, and 4G timings.

## 6. Things that are always true

- A Slack/Gmail/Monday integration is verified by a **delivered result**,
  never by configuration looking right.
- `/health` must live-probe a new integration (a real call), not check env
  presence.
- Never seed a secret from a doc — docs redact; pull from the source system.
- Ephemeral and durable GCS objects never share a lifecycle-ruled bucket.
- If a page can be reached by a role that can't use it, that's a bug in the
  role model, not a UX nit (see the ops-preset incident, `CLAUDE.md` r107).
