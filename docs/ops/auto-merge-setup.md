# Auto-merge setup (cloud agent PRs)

Goal: when a cloud agent opens a ready PR on `cursor/*` → `master`, it merges
itself after tests pass, then **Deploy to Cloud Run** ships production. Jordan
should not have to hunt for the latest PR and click Merge.

## Already flipped on the repo (2026-08-07)

Via GitHub (owner account):

- **Allow auto-merge** = on
- **Automatically delete head branches** = on
- Squash merge uses PR title + body

## Actions workflow (this PR)

`.github/workflows/auto-merge-cursor-prs.yml` runs on non-draft PRs whose head
branch starts with `cursor/`:

1. Same gate as deploy: `compileall` + `pytest`
2. Squash-merge into `master` + delete the branch
3. Explicitly dispatch **Deploy to Cloud Run** (GITHUB_TOKEN merges do not
   fire push-triggered workflows — GitHub recursion guard)

**Pause a PR:** mark it **Draft**, or add label `hold` / `do-not-merge`.

## Actions write — already OK on this repo

Workflow runs show `Contents: write` for `GITHUB_TOKEN`. If a future merge job
403s, flip **Settings → Actions → Workflow permissions → Read and write**.


## Agent rules (so this keeps working)

- Open PRs as **ready** (`draft: false`), not draft — drafts never auto-merge.
- Branch names stay `cursor/<topic>-685a` (or any `cursor/…`).
- Do not put `hold` / `do-not-merge` unless you intentionally want a human merge.
- After merge, trust `deploy-cloud-run.yml` on `master` (already live).

## Hold / human merge

Anything not under `cursor/` is untouched. Manual PRs and design branches stay
human-gated.
