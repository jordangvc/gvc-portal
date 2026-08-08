# Auto-merge setup (cloud agent PRs)

Goal: when a cloud agent opens a PR on `cursor/*` → `master`, it merges itself
after tests pass, then **Deploy to Cloud Run** ships production. Jordan should
not have to hunt for the latest PR and click Merge.

## Already flipped on the repo (2026-08-07)

Via GitHub (owner account):

- **Allow auto-merge** = on
- **Automatically delete head branches** = on
- Squash merge uses PR title + body

## Actions workflow

`.github/workflows/auto-merge-cursor-prs.yml` runs on PRs whose head branch
starts with `cursor/` (draft **or** ready):

1. Same gate as deploy: `compileall` + `pytest`
2. Mark draft → ready (Cursor agents often leave `draft: true`)
3. Squash-merge into `master` + delete the branch
4. Explicitly dispatch **Deploy to Cloud Run** (GITHUB_TOKEN merges do not
   fire push-triggered workflows — GitHub recursion guard)

A **cron every 20 minutes** (+ `workflow_dispatch`) marks eligible drafts ready
so a PR that was left draft forever still wakes up and lands.

**Pause a PR:** add label `hold` or `do-not-merge`. Draft alone does **not**
pause (agents default to draft).

## Actions write — already OK on this repo

Workflow runs show `Contents: write` for `GITHUB_TOKEN`. If a future merge job
403s, flip **Settings → Actions → Workflow permissions → Read and write**.

## Agent rules (so this keeps working)

- Prefer `draft: false` when opening the PR, but drafts still auto-merge after
  the gate — do not babysit the ready toggle.
- Branch names stay `cursor/<topic>-d74a` / `cursor/<topic>-685a` (any `cursor/…`).
- Put `hold` / `do-not-merge` only when a human must review before land.
- Conflicts (`DIRTY`) fail the merge job loudly — rebase onto `master` and push.
- After merge, trust the dispatched `deploy-cloud-run.yml` on `master`.

## Hold / human merge

Anything not under `cursor/` is untouched. Manual PRs and design branches stay
human-gated.
