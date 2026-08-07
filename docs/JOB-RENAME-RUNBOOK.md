# Job rename runbook — catch up to the final naming standard

**Locked standard (Jordan 2026-08-06):**

```
[Street Number Name], [City], [ST] [ZIP] | [Builder] | [Job Title]
```

Examples:

- Residential: `9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence`
- Commercial: `300 Tiger Blvd, Lawrenceburg, IN 47025 | Maxwell Construction | First Financial Bank`

Rules:

- Pipe `|` separators (never dash/underscore in the title).
- City + state + ZIP required on the left.
- Job Title is the **required third** segment.
  - Residential → `{Last name} residence`
  - Commercial → business / tenant name (not the GC unless they *are* the job)
- Missing pieces → **ask, don’t guess**. Scripts skip incomplete rows.
- `CO.n - …` rows cascade from the parent title after the parent is standard.

---

## Catch-up process (final naming)

### Phase A — Preview (no writes)

From the repository root, with Monday + Drive credentials loaded:

```powershell
.venv\Scripts\python scripts\backfill_job_rename_projects.py --dry-run
.venv\Scripts\python scripts\backfill_job_rename_ops.py --dry-run
.venv\Scripts\python scripts\backfill_job_rename_bids.py --dry-run
.venv\Scripts\python scripts\backfill_job_rename_drive.py --dry-run
```

Read the dry-run output. Expect three buckets:

| Action | Meaning |
|--------|---------|
| `rename` | Script can build a full 3-part name from Monday location / builder / hints |
| `skip_standard` | Already `Address \| Builder \| Job Title` with complete geo |
| `skip_incomplete` | Still missing city/ZIP, builder, **or Job Title** — fix in Monday by hand |

**Job Title is the new common gap.** Rows that already have
`Street, City, ST ZIP | Builder` will show `skip_incomplete` / “Missing job title”
until someone adds the third segment (or a residential person-like Customer
lets the planner format `Last residence`).

### Phase B — Fill incomplete Job Titles in Monday

For each `skip_incomplete` that only needs Job Title:

1. Open the Bid / Project item.
2. Append `| {Job Title}` using the residential / commercial rule above.
3. Re-run the dry-run for that board until the incomplete list is only true unknowns.

Do **not** invent a commercial business name from the GC column.

### Phase C — Apply small batches (Projects → Ops → Bids → Drive)

Order matters so Ops can mirror Projects and Drive follows GFolder links.

```powershell
.venv\Scripts\python scripts\backfill_job_rename_projects.py --apply --limit 20
.venv\Scripts\python scripts\backfill_job_rename_ops.py --apply --limit 20
.venv\Scripts\python scripts\backfill_job_rename_bids.py --apply --limit 20
.venv\Scripts\python scripts\backfill_job_rename_drive.py --apply --limit 20
```

Check Monday + Drive after the first batch. If correct, raise `--limit` and
repeat until dry-run reports no `rename` candidates. Passing both `--apply` and
`--dry-run` is safe: dry-run wins.

### Phase D — CO cascade

Re-run Projects (and Ops if needed) so `CO.n - …` rows pick up
`CO.n - {standard parent}`. Parents must be standard first.

---

## What the scripts do not change

- Jobs still missing city/state/ZIP, builder, or Job Title (skipped, not guessed).
- Customers board and Invoices ledger names.
- Drive: only renames folders that already have a Projects `GFolder Link` — never
  creates folders, never touches Jake’s Completed Plans tree.
- Matching across old ↔ new titles stays token-based so adopt-or-create does not
  duplicate during the transition.

Monday keeps the pipes:

`9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence`

Drive folder-safe name drops the pipes:

`9195 Silva Drive, Cincinnati, OH 45241 Willow Creek Smith residence`

`slug_for_path` allows up to **200** characters (Drive’s limit is 255). If a
title is still longer, it keeps a street head + Job Title tail so two long
commercial names don’t collapse to the same folder slug. Folder IDs stay the
same so existing links keep working.

---

## New work going forward

Job Start, Estimate, and Change Order write paths share
`subsystems/jobstart/naming.py`. New handoffs should land already in 3-part form;
the Job Start “Job name” field asks when Job Title is missing.
