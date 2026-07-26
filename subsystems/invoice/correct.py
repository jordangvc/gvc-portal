"""
Invoice correction logic — the "fix a mistake" lane that sits beside the
duplicate guard.

Background (incident 2026-06-23): Andrea billed an invoice to the wrong client
email, the send bounced, and every attempt to re-run the corrected invoice threw
the scary UNEXPECTED error. Root cause was twofold and is fixed elsewhere:
  - the dedupe guard was keyed to the customer's email, so editing the email made
    a re-run look like a brand-new invoice (fixed: invoice.preflight_stripe now
    finds the invoice by gvc_invoice_id metadata — "email-proof");
  - the Stripe idempotency key is scoped to the invoice identifier, so a corrected
    re-run collided with the original and raised IdempotencyError, which the error
    translator mis-reported as a possible partial charge (fixed: service.
    _friendly_error now classifies it as a safe IDEMPOTENCY_CONFLICT).

This module adds the missing capability: a deliberate correction, in two shapes.

  recipient_only   The invoice itself is correct; only the recipient was wrong
                   (the common case — a bounced send). NO new Stripe invoice and
                   NO void: update the Stripe customer's email and regenerate the
                   Gmail draft to the right address. Same identifier, same hosted
                   URL, same ledger row.

  void_and_reissue The invoice content was wrong (amount, scope, the wrong client
                   entirely). Void the old Stripe invoice, mark the Monday ledger
                   row Void, and issue a clean REVISION under a new identifier
                   ("… Rev N") — a fresh idempotency root, so no collision.

Everything in this module is PURE (no network, no Stripe/Monday/Gmail imports) so
it unit-tests cleanly. The service layer performs the actual writes using these
plans and the existing process_one / Stripe / Monday primitives.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

# The two supported correction shapes. Keep this the single source of truth;
# the service endpoint validates the incoming `kind` against it.
KIND_RECIPIENT_ONLY = "recipient_only"
KIND_VOID_AND_REISSUE = "void_and_reissue"
KINDS = (KIND_RECIPIENT_ONLY, KIND_VOID_AND_REISSUE)

# Matches a trailing " Rev N" suffix (case-insensitive, any whitespace run).
_REV_RE = re.compile(r"^(?P<base>.*?)\s+rev\s+(?P<n>\d+)\s*$", re.IGNORECASE)


def next_revision_identifier(identifier: str) -> str:
    """
    Return the next revision identifier, per the locked convention
    "revisions keep the number + ' Rev N'":

        "GVC-2026-MV-007"        -> "GVC-2026-MV-007 Rev 1"
        "GVC-2026-MV-007 Rev 1"  -> "GVC-2026-MV-007 Rev 2"
        "GVC-2026-MV-007 Rev 9"  -> "GVC-2026-MV-007 Rev 10"

    Whitespace is normalised; an empty/blank identifier raises (a revision needs
    something to revise).
    """
    base_id = (identifier or "").strip()
    if not base_id:
        raise ValueError("Cannot revise a blank invoice identifier.")
    m = _REV_RE.match(base_id)
    if m:
        base = m.group("base").strip()
        n = int(m.group("n"))
        return f"{base} Rev {n + 1}"
    return f"{base_id} Rev 1"


def base_identifier(identifier: str) -> str:
    """Strip any trailing ' Rev N' to recover the original document number."""
    m = _REV_RE.match((identifier or "").strip())
    return m.group("base").strip() if m else (identifier or "").strip()


def _deep_merge(base: dict, overrides: dict) -> dict:
    """
    Return a deep copy of `base` with `overrides` merged in. Nested dicts merge
    key-by-key; every other value (including lists like line_items) REPLACES the
    base value wholesale — corrections are explicit, never a partial list patch.
    """
    out = copy.deepcopy(base)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize_corrections(corrections: dict) -> dict:
    """
    Accept either the nested shape ({"client": {"email": ...}}) or a few flat
    conveniences and return the nested override shape used by build_corrected_payload.

    Flat conveniences:
        email           -> client.email
        cc              -> client.cc
        contact_name    -> client.contact_name
        customer_name   -> client.name
        billing_address -> client.billing_address
        email_context   -> invoice.email_context
    Unknown flat keys are ignored (the caller validates upstream).
    """
    c = dict(corrections or {})
    nested: dict[str, Any] = {}
    # Preserve any already-nested sections first.
    for section in ("client", "job", "invoice"):
        if isinstance(c.get(section), dict):
            nested[section] = copy.deepcopy(c[section])

    flat_to_path = {
        "email": ("client", "email"),
        "cc": ("client", "cc"),
        "contact_name": ("client", "contact_name"),
        "customer_name": ("client", "name"),
        "billing_address": ("client", "billing_address"),
        "email_context": ("invoice", "email_context"),
    }
    for flat, (section, key) in flat_to_path.items():
        if flat in c and c[flat] is not None:
            nested.setdefault(section, {})[key] = c[flat]
    return nested


def build_corrected_payload(original: dict, *, kind: str, corrections: dict) -> dict:
    """
    Produce the invoice JSON for the correction.

      recipient_only   -> deep copy + corrections, SAME identifier.
      void_and_reissue -> deep copy + corrections, identifier bumped to the next
                          " Rev N" (unless corrections explicitly override
                          invoice.identifier).

    Pure: returns a new dict, never mutates `original`.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown correction kind: {kind!r}. Expected one of {KINDS}.")
    if not isinstance(original, dict) or "invoice" not in original:
        raise ValueError("Original invoice payload is missing its 'invoice' section.")

    overrides = normalize_corrections(corrections)
    payload = _deep_merge(original, overrides)

    if kind == KIND_VOID_AND_REISSUE:
        # Bump the identifier unless the caller deliberately set one.
        overrode_identifier = bool(overrides.get("invoice", {}).get("identifier"))
        if not overrode_identifier:
            current = original.get("invoice", {}).get("identifier", "")
            payload.setdefault("invoice", {})["identifier"] = next_revision_identifier(current)
    return payload


# ---------------------------------------------------------------------------
# Diff-driven correction ("git diff for the invoice")
#
# The office edits a copy of the original invoice; we diff the two, show the
# field-by-field changes for confirmation, and AUTO-ROUTE the fix:
#
#   in_place   Only non-monetary fields changed (recipient email, contact, phone,
#              billing address, the email note). The finalized Stripe invoice's
#              MONEY is untouched, so we edit in place — same invoice, same
#              number, same hosted URL — and just refresh our PDF / Drive / Monday
#              / Gmail draft.
#   revision   A monetary or document field changed (line items, amounts,
#              retainage, discount, dates, type, job/customer name, identifier).
#              A finalized Stripe invoice is immutable for these (tax-retention
#              rule), so we issue a Stripe REVISION (from_invoice) under "… Rev N",
#              which auto-voids the original on finalize.
# ---------------------------------------------------------------------------

ROUTE_NOOP = "noop"
ROUTE_IN_PLACE = "in_place"
ROUTE_REVISION = "revision"

# Dotted paths that can be corrected WITHOUT reissuing — they don't touch the
# finalized Stripe invoice's immutable money/amount fields. Everything else routes
# to a revision. Conservative on purpose: a revision always works; in-place is the
# narrow optimization for the common "wrong recipient / contact" fixes.
IN_PLACE_SAFE_PATHS = (
    "client.email",
    "client.cc",
    "client.contact_name",
    "client.phone",
    "client.billing_address",
    "invoice.email_context",
)

# The scalar fields we surface in a diff, by section. line_items is handled
# specially (list compare) below.
_DIFF_SCALAR_PATHS = (
    "client.name", "client.email", "client.cc", "client.contact_name",
    "client.phone", "client.billing_address",
    "job.name", "job.location", "job.scope_summary",
    "invoice.identifier", "invoice.issue_date", "invoice.due_date",
    "invoice.invoice_type", "invoice.pay_app_number", "invoice.period_end_date",
    "invoice.email_context",
)


def _get_path(d: dict, path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _line_items_signature(inv: dict) -> list:
    """A comparable, order-sensitive view of the billable content."""
    items = (inv or {}).get("line_items") or []
    sig = []
    for li in items:
        sig.append({
            "description": li.get("description"),
            "amount": li.get("amount"),
            "quantity": li.get("quantity"),
            "unit_price": li.get("unit_price"),
            "kind": li.get("kind"),
        })
    return sig


def merge_corrections(original: dict, corrections: dict) -> dict:
    """
    Apply corrections (nested or flat — see normalize_corrections) onto a deep
    copy of `original`. Does NOT bump the identifier — routing decides that.
    """
    return _deep_merge(original, normalize_corrections(corrections))


def diff_payload(original: dict, corrected: dict) -> list[dict]:
    """
    Return the list of changes from `original` to `corrected`, each as
    {path, from, to, monetary}. Scalars are compared field-by-field; the line
    items, retainage, and discount are compared as units and flagged monetary.
    Pure; the basis of the confirmation screen.
    """
    changes: list[dict] = []
    for path in _DIFF_SCALAR_PATHS:
        a, b = _get_path(original, path), _get_path(corrected, path)
        if a != b:
            changes.append({"path": path, "from": a, "to": b, "monetary": False})

    oinv, cinv = original.get("invoice") or {}, corrected.get("invoice") or {}

    if _line_items_signature(oinv) != _line_items_signature(cinv):
        changes.append({
            "path": "invoice.line_items",
            "from": f"{len(oinv.get('line_items') or [])} line(s)",
            "to": f"{len(cinv.get('line_items') or [])} line(s)",
            "monetary": True,
        })
    if (oinv.get("retainage") or None) != (cinv.get("retainage") or None):
        changes.append({"path": "invoice.retainage", "from": oinv.get("retainage"),
                        "to": cinv.get("retainage"), "monetary": True})
    if (oinv.get("discount") or None) != (cinv.get("discount") or None):
        changes.append({"path": "invoice.discount", "from": oinv.get("discount"),
                        "to": cinv.get("discount"), "monetary": True})
    return changes


def _is_in_place_safe(path: str) -> bool:
    return any(path == p or path.startswith(p + ".") for p in IN_PLACE_SAFE_PATHS)


def route_for_changes(changes: list[dict]) -> str:
    """
    Decide the route from a diff: noop (nothing changed), in_place (every change
    is in the non-monetary safe set), or revision (anything else).
    """
    if not changes:
        return ROUTE_NOOP
    if all((not c.get("monetary")) and _is_in_place_safe(c["path"]) for c in changes):
        return ROUTE_IN_PLACE
    return ROUTE_REVISION


def plan_for_route(route: str, changes: list[dict], *,
                   existing_status: Optional[str] = None,
                   new_identifier: Optional[str] = None) -> dict:
    """
    Human-readable plan for a routed correction — drives the confirm screen.
    Returns {route, voids_original, creates_new_invoice, steps, caveats, changes}.
    """
    status = (existing_status or "").strip().lower()
    caveats: list[str] = []

    if route == ROUTE_NOOP:
        return {"route": route, "voids_original": False, "creates_new_invoice": False,
                "steps": ["Nothing changed — no correction needed."], "caveats": [],
                "changes": changes}

    if route == ROUTE_IN_PLACE:
        if status == "paid":
            caveats.append("The invoice is already marked Paid — confirm you really "
                           "need to resend before changing the recipient.")
        return {
            "route": route, "voids_original": False, "creates_new_invoice": False,
            "steps": [
                "Update the customer details on the existing Stripe invoice (no money change).",
                "Re-render the GVC PDF and replace it in Drive (same invoice number + hosted link).",
                "Update the existing Invoices Sent row.",
                "Refresh the Gmail draft to the corrected recipient (replaces it in place).",
            ],
            "caveats": caveats, "changes": changes,
        }

    # revision
    if status == "paid":
        caveats.append("The original invoice is PAID — issuing a revision voids it, which "
                       "un-does a recorded payment. Confirm this is a correction, not a refund.")
    monetary = any(c.get("monetary") for c in changes)
    if monetary:
        caveats.append("A monetary/line-item field changed — a finalized Stripe invoice can't "
                       "be edited in place (tax-retention rule), so this issues a revision.")
    return {
        "route": route, "voids_original": True, "creates_new_invoice": True,
        "new_identifier": new_identifier,
        "steps": [
            f"Issue a Stripe revision ({new_identifier or '… Rev N'}) of the original "
            "(Stripe links it and auto-voids the original on finalize).",
            "Mark the original Invoices Sent row Void and record the revision.",
            "File the revised PDF in Drive (supersedes the original).",
            "Delete the original Gmail draft and create the revision's draft.",
        ],
        "caveats": caveats, "changes": changes,
    }


def plan_correction(kind: str, *, existing_status: Optional[str] = None) -> dict:
    """
    Describe what a correction of this `kind` will do, as an ordered, render-ready
    plan. Drives both the dry-run preview the office confirms and the live
    execution order. `existing_status` is the current Stripe status of the
    original invoice ("open"/"paid"/"void"/None) — used only to annotate caveats.

    Returns:
        {
          "kind": str,
          "voids_original": bool,
          "creates_new_invoice": bool,
          "steps": [ "human-readable step", ... ],
          "caveats": [ "warning", ... ],
        }
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown correction kind: {kind!r}. Expected one of {KINDS}.")

    status = (existing_status or "").strip().lower()
    caveats: list[str] = []

    if kind == KIND_RECIPIENT_ONLY:
        if status == "paid":
            caveats.append(
                "The original invoice is already marked Paid in Stripe — double-check "
                "you actually need to resend before correcting the recipient."
            )
        return {
            "kind": kind,
            "voids_original": False,
            "creates_new_invoice": False,
            "steps": [
                "Update the customer's email on the existing Stripe invoice.",
                "Re-render the GVC invoice PDF (same number, same hosted payment link).",
                "Regenerate the Gmail draft to the corrected address (updates the "
                "existing draft in place — no duplicate).",
            ],
            "caveats": caveats,
        }

    # void_and_reissue
    if status == "paid":
        caveats.append(
            "The original invoice is PAID — voiding it will un-do a recorded payment. "
            "Confirm this is really a mistake correction, not a refund."
        )
    if status == "void":
        caveats.append(
            "The original invoice is already Void in Stripe; the reissue will proceed "
            "and the void step is a no-op."
        )
    return {
        "kind": kind,
        "voids_original": True,
        "creates_new_invoice": True,
        "steps": [
            "Void the original Stripe invoice.",
            "Mark the original row Void on the Invoices Sent board.",
            "Issue a clean revision under a new number (… Rev N) with the corrections.",
            "Create the revision's Gmail draft for review.",
        ],
        "caveats": caveats,
    }
