# Job rename runbook

This is the safe order for changing old job names to:

`Street, City, ST ZIP | Builder`

Jordan should run every script in dry-run mode first. Dry-run only prints the
proposed work; it does not rename anything.

## 1. Merge the five slices in order

1. [PR #46](https://github.com/jordangvc/gvc-portal/pull/46) — shared planner
2. Projects rename slice
3. Operations rename slice
4. Bid Board rename slice
5. Drive folder rename slice

Each slice depends on the code before it. Do not merge or run a later slice
while an earlier one is still under review.

## 2. Preview every rename

From the repository root, with the normal Monday and Drive credentials loaded:

```powershell
.venv\Scripts\python scripts\backfill_job_rename_projects.py --dry-run
.venv\Scripts\python scripts\backfill_job_rename_ops.py --dry-run
.venv\Scripts\python scripts\backfill_job_rename_bids.py --dry-run
.venv\Scripts\python scripts\backfill_job_rename_drive.py --dry-run
```

Read the output before applying it. Any row that cannot be named without
guessing is skipped and should be corrected by hand in Monday first.

## 3. Apply a small batch

Start each script with no more than 20 rows:

```powershell
.venv\Scripts\python scripts\backfill_job_rename_projects.py --apply --limit 20
.venv\Scripts\python scripts\backfill_job_rename_ops.py --apply --limit 20
.venv\Scripts\python scripts\backfill_job_rename_bids.py --apply --limit 20
.venv\Scripts\python scripts\backfill_job_rename_drive.py --apply --limit 20
```

Check Monday and Drive after that first batch. If the names are correct, repeat
with a larger limit until the scripts report no remaining rename candidates.
Passing both `--apply` and `--dry-run` is safe: dry-run wins.

## What the scripts do not change

- `CO.` rows are skipped. Change Order titles need their own parent-aware pass.
- Jobs missing city, state, ZIP, or a builder/customer are skipped rather than
  guessed.
- The Customers board is not renamed.
- The Invoices ledger is not renamed.
- The Drive script only uses an existing Projects `GFolder Link`. It never
  creates folders and never searches or touches Jake's Completed Plans tree.
- A missing or invalid `GFolder Link` is skipped.

Monday keeps the pipe in job titles:

`9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek`

Drive removes the pipe when it makes the folder-safe name:

`9195 Silva Drive, Cincinnati, OH 45241 Willow Creek`

The Drive rename happens in place. The folder ID stays the same, so existing
links keep working.
