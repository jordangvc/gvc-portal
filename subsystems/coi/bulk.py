"""
Bulk COI ("Annual COI List") — pure sheet parsing + run planning.
=========================================================================
Everything here is PURE (rows in → plan out) so the whole batching brain is
unit-testable without Sheets/Drive/Gmail. adapters/sheets.py does the I/O;
orchestrators/coi_flow.process_coi_bulk drives the loop.

Built against the REAL "GREEN VALLEY CONTRACTORS — COI & W-9 Mailing List"
sheet (inspected 2026-07-14), which taught us:
  - the header row is NOT row 1 (three merged banner rows sit above it),
  - headers are "Client/Builder Name | Project Name | Mailing Address |
    Contact Name | Contact Email | Sent" (synonyms handled),
  - addresses are single-line with commas, often suffixed ", USA",
  - real rows are missing addresses or emails, and some builders repeat.

Row semantics (locked; invalid-row marking revised 2026-07-16 per Joe):
  - Sent/Status == YES  → already handled; SKIP (idempotent re-runs).
  - invalid (missing name/address/email) → NOT attempted, but marked NO on
    the final chunk of a finalize run so the ledger shows every unsent row
    (was: cell left untouched — that hid skipped rows from the counts).
  - attempted and failed → NO written to the cell + error in the response.
  - attempted and drafted → YES written to the cell.
"""
from __future__ import annotations

import re
from typing import Optional

MAX_BULK_ROWS = 500  # sanity cap; the annual list is ~100


# ---------------------------------------------------------------------------
# Header detection + column mapping
# ---------------------------------------------------------------------------

def _norm(cell: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (cell or "").strip().lower()).strip()


def find_header_row(rows: list[list[str]]) -> Optional[int]:
    """
    0-based index of the header row: the first row whose cells include an
    email-ish header AND a name-ish header. Scans the top 12 rows (the real
    sheet has 3 merged banner rows + a blank above its header).
    """
    for i, row in enumerate(rows[:12]):
        cells = [_norm(c) for c in row]
        has_email = any("email" in c for c in cells)
        has_name = any(("name" in c) or ("client" in c) or ("builder" in c)
                       for c in cells)
        if has_email and has_name:
            return i
    return None


def map_columns(header: list[str]) -> dict:
    """
    Map header cells → column indexes. Returns
      {name, address, contact_name, contact_email, status, project}
    (project is optional context; the rest are required except status,
    which is required for writeback). Raises ValueError with an
    office-readable message when a required column can't be found.
    """
    cols: dict[str, Optional[int]] = {
        "name": None, "project": None, "address": None,
        "contact_name": None, "contact_email": None, "status": None,
    }
    for idx, raw in enumerate(header):
        c = _norm(raw)
        if not c:
            continue
        if cols["contact_email"] is None and "email" in c:
            cols["contact_email"] = idx
        elif cols["contact_name"] is None and "contact" in c and "name" in c:
            cols["contact_name"] = idx
        elif cols["address"] is None and "address" in c:
            cols["address"] = idx
        elif cols["project"] is None and "project" in c and "name" in c:
            cols["project"] = idx
        elif cols["name"] is None and ("client" in c or "builder" in c
                                       or c == "name" or c.startswith("name")):
            cols["name"] = idx
        elif cols["status"] is None and ("sent" in c or "status" in c):
            cols["status"] = idx

    missing = [k for k in ("name", "address", "contact_name", "contact_email")
               if cols[k] is None]
    if missing:
        raise ValueError(
            "Couldn't find these columns in the sheet's header row: "
            + ", ".join(missing)
            + ". Expected headers like: Client/Builder Name | Mailing Address "
              "| Contact Name | Contact Email | Sent."
        )
    if cols["status"] is None:
        raise ValueError(
            "The sheet needs a 'Sent' (or 'Status') column — the run writes "
            "YES/NO there so re-runs know what to skip. Add the column and retry."
        )
    return cols


# ---------------------------------------------------------------------------
# Address shaping (single-line sheet address → certificate-holder lines)
# ---------------------------------------------------------------------------

_USA_SUFFIX = re.compile(r",?\s*(usa|us|united states)\s*$", re.IGNORECASE)


def split_single_line_address(address: str) -> str:
    """
    Turn a one-line sheet address into the two-line form the certificate
    holder box uses:  "14387 Wilson Creek Rd, Lawrenceburg, IN, USA"
                   →  "14387 Wilson Creek Rd\nLawrenceburg, IN"
    Rules: strip a trailing USA/US suffix, then split street from the rest
    at the FIRST comma. Addresses that already contain newlines, or have no
    comma, pass through unchanged (minus the USA suffix).
    """
    s = " ".join((address or "").split())
    s = _USA_SUFFIX.sub("", s).strip().rstrip(",")
    if not s:
        return ""
    if "\n" in (address or ""):
        return _USA_SUFFIX.sub("", address.strip()).strip()
    if "," not in s:
        return s
    street, rest = s.split(",", 1)
    rest = rest.strip().strip(",").strip()
    return f"{street.strip()}\n{rest}" if rest else street.strip()


# ---------------------------------------------------------------------------
# Row plan
# ---------------------------------------------------------------------------

def _cell(row: list[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def build_plan(rows: list[list[str]]) -> dict:
    """
    Parse the whole sheet into a run plan:
      {
        header_row: int (0-based),
        columns: {...},
        entries: [{row_number (1-based sheet row), name, project, address,
                   contact_name, contact_email, status,
                   state: "ready"|"skip_sent"|"invalid",
                   reasons: [..], duplicate_of: row_number|None}],
        counts: {ready, skip_sent, invalid, total}
      }
    Raises ValueError when the header can't be found / mapped.
    """
    if len(rows) > MAX_BULK_ROWS + 20:
        raise ValueError(
            f"The sheet has more than {MAX_BULK_ROWS} rows — that's beyond the "
            "bulk cap. Split the list or raise MAX_BULK_ROWS deliberately."
        )
    hdr_idx = find_header_row(rows)
    if hdr_idx is None:
        raise ValueError(
            "Couldn't find the header row (looked for Name + Email headers in "
            "the first 12 rows). Is this the Annual COI List sheet?"
        )
    cols = map_columns(rows[hdr_idx])

    entries: list[dict] = []
    seen_names: dict[str, int] = {}
    for i in range(hdr_idx + 1, len(rows)):
        row = rows[i]
        row_number = i + 1  # 1-based, matches what users see in Sheets
        name = _cell(row, cols["name"])
        project = _cell(row, cols["project"])
        address = _cell(row, cols["address"])
        contact_name = _cell(row, cols["contact_name"])
        contact_email = _cell(row, cols["contact_email"])
        status = _cell(row, cols["status"]).upper()

        if not any([name, project, address, contact_name, contact_email]):
            continue  # fully blank row — layout noise, not data

        entry = {
            "row_number": row_number,
            "name": name,
            "project": project,
            "address": address,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "status": status,
            "reasons": [],
            "duplicate_of": None,
        }

        if status == "YES":
            entry["state"] = "skip_sent"
        else:
            if not name:
                entry["reasons"].append("missing Client/Builder Name")
            if not address:
                entry["reasons"].append("missing Mailing Address")
            if not contact_email or "@" not in contact_email:
                entry["reasons"].append("missing/invalid Contact Email")
            # A missing contact NAME is a warning-grade gap — fall back to
            # the builder name so the greeting still reads sensibly.
            entry["state"] = "invalid" if entry["reasons"] else "ready"

        key = name.strip().lower()
        if key and entry["state"] == "ready":
            if key in seen_names:
                entry["duplicate_of"] = seen_names[key]
                entry["reasons"].append(
                    f"duplicate of row {seen_names[key]} — same builder name; "
                    "the later row overwrites the earlier draft/PDF"
                )
            else:
                seen_names[key] = row_number

        entries.append(entry)

    counts = {
        "ready": sum(1 for e in entries if e["state"] == "ready"),
        "skip_sent": sum(1 for e in entries if e["state"] == "skip_sent"),
        "invalid": sum(1 for e in entries if e["state"] == "invalid"),
        "total": len(entries),
    }
    return {"header_row": hdr_idx, "columns": cols, "entries": entries,
            "counts": counts}


def entry_to_coi_payload(entry: dict) -> dict:
    """One plan entry → the single-COI flow's input shape."""
    return {
        "holder": {
            "name": entry["name"],
            "address": split_single_line_address(entry["address"]),
        },
        "contact": {
            "name": entry["contact_name"] or entry["name"],
            "email": entry["contact_email"],
        },
    }


def _row_list(rows: list[int]) -> str:
    shown = ", ".join(map(str, rows[:15]))
    return shown + (f" (+{len(rows) - 15} more)" if len(rows) > 15 else "")


def bulk_summary_message(counts: dict, *, expiry_label: Optional[str] = None,
                         failed_rows: Optional[list[int]] = None,
                         invalid_rows: Optional[list[int]] = None) -> str:
    """
    PURE: the one-per-run Slack summary, posted when the batch finishes.
    Counts come from a FRESH read of the sheet after the run (the stateless
    truth across chunked calls): succeeded = every YES row (may include
    earlier runs); failed = every NO row, split into draft attempts that
    errored ('no') vs rows skipped for missing contact info ('invalid').
    Row numbers are always prefixed with 'row(s)' — a bare number reads as
    a count (the 2026-07-16 'Failed rows: 87' confusion).
    """
    yes = counts.get("yes", 0)
    n_failed = counts.get("no", 0)
    n_invalid = counts.get("invalid", 0)
    parts = [
        "📜 *Annual COI run finished* — new drafts are waiting in hello@ "
        "to review & send.",
        f"• {yes} succeeded (marked YES) · {n_failed + n_invalid} failed "
        f"(marked NO)",
    ]
    if n_failed:
        where = f": row{'s' if n_failed != 1 else ''} {_row_list(failed_rows)}" \
            if failed_rows else ""
        parts.append(f"• {n_failed} draft attempt{'s' if n_failed != 1 else ''} "
                     f"errored — will retry on the next run{where}")
    if n_invalid:
        where = f": row{'s' if n_invalid != 1 else ''} {_row_list(invalid_rows)}" \
            if invalid_rows else ""
        parts.append(f"• {n_invalid} skipped — missing name/address/email, "
                     f"fix the sheet to include them{where}")
    if expiry_label:
        parts.append(f"• Certificate: {expiry_label}")
    return "\n".join(parts)
