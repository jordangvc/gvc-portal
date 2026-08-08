# Auto-merge setup (cloud agent PRs)

Goal: when a cloud agent opens a PR on `cursor/*` → `master`, it merges itself
after tests pass, then **Deploy to Cloud Run** ships production. Jordan should
not hunt for the latest PR or click Merge / Ready.

## Already flipped on the repo (2026-08-07)

Via GitHub (owner account):

- **Allow auto-merge** = on
- **Automatically delete head branches** = on
- Squash merge uses PR title + body

## Actions workflow

`.github/workflows/auto-merge-cursor-prs.yml`:

1. On PR open/sync/ready/unlabel (and every **20 minutes** via cron)
2. `cursor/*` head only; skip if label `hold` / `do-not-merge`
3. **Auto-mark draft → ready** (draft alone no longer blocks)
4. Gate: `compileall` + `pytest`
5. Squash-merge + delete branch
6. Explicitly dispatch **Deploy to Cloud Run**

**Pause a PR:** add label `hold` or `do-not-merge`.

## Actions write — already OK on this repo

Workflow runs show `Contents: write` for `GITHUB_TOKEN`. If a future merge job
403s, flip **Settings → Actions → Workflow permissions → Read and write**.

## Agent rules

- Branch names stay `cursor/<topic>-…`.
- Prefer `draft: false` when opening PRs; drafts still auto-ready for `cursor/*`.
- Do not add `hold` / `do-not-merge` unless you intentionally want a human merge.
- **Conflicts block merge** — rebase onto master; cron retries once MERGEABLE.

## Hold / human merge

Anything not under `cursor/` is untouched. Manual PRs and design branches stay
human-gated.
