"""Inventory constants, ids, and the shared validation error type."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

TRACKING_MODES = ("quantity", "asset", "kit")

TXN_TYPES = (
    "RECEIVE",            # drop off into a location
    "ISSUE",              # pick up out of a location
    "TRANSFER",           # location -> location
    "COUNT_ADJUSTMENT",   # variance from a count session
    "MANUAL_ADJUSTMENT",  # manager correction with reason
    "ASSET_ASSIGNMENT",   # asset custody move
    "CONDITION_CHANGE",   # asset/kit condition transition
    "KIT_ASSEMBLY",       # loose components -> kit instance
    "KIT_DISASSEMBLY",    # kit instance -> loose components
    "REVERSAL",           # compensating entry linked to an original
    "INITIAL_LOAD",       # opening balances (imports / first counts)
)

# Types whose lines may subtract stock from a source location.
OUTBOUND_TYPES = frozenset({"ISSUE", "TRANSFER", "KIT_ASSEMBLY"})

CONDITIONS = (
    "available", "in_use", "damaged", "needs_repair",
    "missing_components", "quarantined", "retired", "lost",
)
# Conditions that take the asset out of movable circulation.
INACTIVE_CONDITIONS = frozenset({"retired", "lost"})
# Conditions whose report should prominently ask for a photo/note.
FLAG_CONDITIONS = frozenset({"damaged", "needs_repair", "missing_components"})

LOCATION_KINDS = (
    "storage", "zone", "shelf", "office", "shop",
    "truck", "job_site", "employee", "repair", "disposal",
)
# Kinds that may hold stock but cannot have children.
LEAF_ONLY_KINDS = frozenset({"truck", "employee", "repair", "disposal"})

ATTENTION_KINDS = (
    "unknown_item", "damage", "negative_override", "low_stock",
    "count_discrepancy", "incomplete_kit", "sync_failure", "asset_lost",
)

_TOKEN_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"  # no 0/O/1/l/i lookalikes


class InventoryError(Exception):
    """Domain validation failure → HTTP 4xx with {code, detail, advice}."""

    def __init__(self, code: str, detail: str, *, field: str = "",
                 advice: str = ""):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.field = field
        self.advice = advice

    def envelope(self) -> dict:
        out = {"ok": False, "code": self.code, "detail": self.detail}
        if self.field:
            out["field"] = self.field
        if self.advice:
            out["advice"] = self.advice
        return out


def now_iso() -> str:
    """UTC ISO-8601 to the second — every stored timestamp uses this."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_token(prefix: str) -> str:
    """Opaque, revocable scan token, e.g. L-x7k2mfp9. Human-typeable —
    the printed label carries it as the manual-entry fallback."""
    body = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(8))
    return f"{prefix}-{body}"


def new_uuid() -> str:
    return secrets.token_hex(16)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-") or "x"


def require(cond: bool, code: str, detail: str, *, field: str = "",
            advice: str = "") -> None:
    if not cond:
        raise InventoryError(code, detail, field=field, advice=advice)
