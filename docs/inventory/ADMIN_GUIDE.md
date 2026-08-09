# Inventory — Admin Guide

Console: `/ui/inventory/admin` (grant `inventory_manage`). Everything the
office does lives here; everything writes an audit event.

## Items
- **Create/edit**: name, category, base unit, package conversions
  (1 case = 12 rolls), aliases (the words the crew actually says —
  aliases are what make search work), manufacturer barcodes, min-qty
  rules per location (drives the low-stock attention feed), photo URL.
- **Tracking mode is permanent** (quantity / asset / kit). Picked wrong?
  Archive and recreate — history stays with the original.
- **Merge duplicates**: Merge into… → the source's names/barcodes move to
  the target, the source is archived with a pointer, and any remaining
  stock transfers as audited adjustments. Nothing in history is rewritten.
- **Unknown items** (crew submissions) arrive in Attention with photo,
  crew name, qty, location. "Create item from this" prefills the editor;
  keep their word as an alias. Match-to-existing = add the alias to the
  existing item and resolve the submission.

## Assets and kits
- Assets = individually tracked things (ladders, vacs, specialty tools):
  add under an asset-tracked item with serial/make/model; each gets an
  opaque QR token and a custody trail. Damaged / needs-repair / retired /
  lost are condition changes with notes+photos, all reversible by a
  manager with a reason.
- Kits: create a kit-template item with expected components, then
  assemble instances from loose stock at a location (components leave
  loose stock — never double-counted). Kits move as one unit; inspect
  completeness on the kit card; disassemble all or part back to loose.

## Locations
Tree of storage → zones/shelves, plus trucks, job sites, employee
custody, repair, disposal. Each gets a QR label. Deactivating requires
the location to be empty — use "View contents → move all to…" first.
**Rotate token** kills a compromised label instantly (reprint after).

## Imports (initial inventory or bulk adds)
Import → download the CSV template → fill → Preview (nothing writes; you
get a row-by-row error report) → Commit (all-or-nothing). Balances land
as INITIAL_LOAD ledger transactions — never direct edits. Import history
is kept.

## Counts
- **Quick count**: you count, see variance, post — for spot checks.
- **Blind audit**: assign to a counter who cannot see expected numbers;
  they submit; YOU review variances and approve/reject. Approval posts
  auditable adjustments and files a count_discrepancy attention record.
  The counter cannot approve their own blind audit.

## Corrections
Never edit history. Wrong transaction → Reports → find it → **Reverse**
with a reason; the pair stays linked forever. Stock override (letting a
pick-up exceed the recorded balance) is manager-only, reason required,
and lands in Attention — treat each one as a count you owe.

## Labels
Add locations/assets/items to the label basket → Generate PDF →
Avery 5160 sheet (30/page): QR + typed-fallback code + name.

## Reports
Balances by location · item locations · asset custodian · full history
(filter by item/location/person/type/job) · count variances · low stock
· damaged/lost/retired. Every view exports CSV. Totals reconcile to the
ledger by construction; `/ui/api/inventory/reconcile` (admin) proves it.
