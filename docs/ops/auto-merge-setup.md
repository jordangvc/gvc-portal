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

**Pause a PR:** mark it **Draft**, or add label `hold` / `do-not-merge`.

## One click you may still need (Actions write)

If the merge job fails with a permission error (`Resource not accessible by
integration` / cannot merge), GitHub Actions default workflow tokens are
read-only. Fix once:

1. Open https://github.com/jordangvc/gvc-portal/settings/actions
2. Under **Workflow permissions**, choose **Read and write permissions**
3. Save
4. Re-run the failed **Auto-merge cursor PRs** workflow (or push an empty
   commit on the PR)

No new secrets. Uses `GITHUB_TOKEN` only.

## Agent rules (so this keeps working)

- Open PRs as **ready** (`draft: false`), not draft — drafts never auto-merge.
- Branch names stay `cursor/<topic>-685a` (or any `cursor/…`).
- Do not put `hold` / `do-not-merge` unless you intentionally want a human merge.
- After merge, trust `deploy-cloud-run.yml` on `master` (already live).

## Hold / human merge

Anything not under `cursor/` is untouched. Manual PRs and design branches stay
human-gated.
