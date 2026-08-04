"""
Portal access control — "is this person provisioned, and what can they open?"
=========================================================================
Separation of concerns:
  - auth.py   = "is this a valid, unexpired Google session?" (cookie crypto)
  - access.py = "is this email provisioned, and which features does it hold?"
  - portal_store.py = where the grant data physically lives (GCS JSON)

Backends (env var GVC_GRANTS_BACKEND):
  - "env"  (default, today/transition): the only provisioned users are those in
    GVC_PORTAL_ALLOWED_EMAILS, and they get ALL features. This reproduces current
    production behavior exactly (the env superadmin has everything), so nothing changes until we
    deliberately flip the flag.
  - "gcs": provisioned = superadmins (GVC_PORTAL_ALLOWED_EMAILS, break-glass) OR
    anyone in the portal store. Superadmins always get all features — that's the
    lockout-proof path so a superadmin can never accidentally revoke their own admin access.

Features are the unit of access. `timeoff` is the baseline every provisioned
(non-superadmin) store user receives automatically. `admin` unlocks the admin page.
A user record may hold the wildcard "*" to mean all features.

Deny-by-default everywhere: unknown email, empty store, or a backend error all
resolve to "no access".
"""
from __future__ import annotations

import os

# Canonical feature set. Order is display order on the hub.
# `lien` = Lien Watch (notice/lien deadline tracker), added 2026-07-26.
# `jobcheck` = Job Check (field-crew Monday column updates), added 2026-07-27.
# `jobstart` = Job Start (Sales → Operations handoff gate), added 2026-07-29.
# `fieldguide` = Field Manual (our own production procedures), added 2026-07-29.
# `morning` = Morning Brief / Ops huddle daily control center, added 2026-08-03
#   (docs/MORNING_BRIEF_BUILD_SPEC.md). Role overlays (NOT baseline):
#   morning_ops = Operations Team (huddle attendance), morning_gm = General
#   Manager run sheet, morning_owner = Owner Pulse (exception-only). Keyed to
#   portal grants — never hard-coded names (Donnie/Jordan/etc.).
FEATURES: tuple[str, ...] = (
    "morning", "morning_ops", "morning_gm", "morning_owner",
    "estimate", "change_order", "invoice", "check",
    "coi", "lien", "jobcheck", "jobstart", "takeoff", "timeoff", "fieldguide",
    "activity", "admin",
)
ALL_FEATURES = frozenset(FEATURES)
# Baseline = every provisioned store user gets this without an explicit grant.
# `fieldguide` is baseline on purpose: it holds no customer or financial data,
# and the whole point is that a crew member can reach it in one tap.
# `morning` is baseline for the same reason: every employee gets their private
# daily brief; role personalization is server-side, not a separate grant.
BASELINE = frozenset({"timeoff", "fieldguide", "morning"})
WILDCARD = "*"

# Backward-compatible implications: when a tool graduates to its own feature,
# the broader grant that USED to cover it keeps working, so no existing user
# loses access on the day the new permission ships. Admins can still grant the
# new feature on its own (e.g. the audit view without full admin).
#   estimate ⇒ change_order   (Change Order app, formerly under `estimate`)
#   invoice  ⇒ check          (Paid by Check, formerly under `invoice`)
#   admin    ⇒ activity       (Activity audit view, formerly under `admin`)
#   morning_gm ⇒ morning_ops  (GM is on the Operations Team)
#   morning_owner does NOT imply ops/gm — Owner Pulse is exception-only.
IMPLIES: dict[str, frozenset] = {
    "estimate": frozenset({"change_order"}),
    "invoice": frozenset({"check"}),
    "admin": frozenset({"activity"}),
    "morning_gm": frozenset({"morning_ops"}),
}


def backend() -> str:
    return (os.environ.get("GVC_GRANTS_BACKEND") or "env").strip().lower()


def superadmin_emails() -> set[str]:
    """Break-glass admins from GVC_PORTAL_ALLOWED_EMAILS — always full access."""
    raw = os.environ.get("GVC_PORTAL_ALLOWED_EMAILS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _expand(features) -> set[str]:
    """Turn a stored feature list into the effective set (wildcard + baseline +
    backward-compatible implications)."""
    f = {str(x).strip().lower() for x in (features or []) if str(x).strip()}
    if WILDCARD in f:
        return set(ALL_FEATURES)
    base = (f & ALL_FEATURES) | set(BASELINE)
    for feat in list(base):
        base |= IMPLIES.get(feat, frozenset())
    return base & ALL_FEATURES


def is_provisioned(email: str) -> bool:
    """True if this email may use the portal at all (the new 'allowlist')."""
    email = (email or "").strip().lower()
    if not email:
        return False
    if email in superadmin_emails():
        return True
    if backend() == "gcs":
        try:
            from shared import portal_store as portal_store
            return portal_store.has_user(email)
        except Exception:  # noqa: BLE001 — store error ⇒ deny (fail closed)
            return False
    return False


def effective_features(email: str) -> set[str]:
    """The set of features this email can currently use. Empty ⇒ no access."""
    email = (email or "").strip().lower()
    if not email:
        return set()
    if email in superadmin_emails():
        return set(ALL_FEATURES)
    if backend() == "gcs":
        try:
            from shared import portal_store as portal_store
            rec = portal_store.get_user(email)
        except Exception:  # noqa: BLE001 — fail closed
            return set()
        if not rec:
            return set()
        return _expand(rec.get("features"))
    return set()  # env backend, non-superadmin


def has_feature(email: str, feature: str) -> bool:
    return feature in effective_features(email)


def morning_role(email: str) -> dict:
    """
    Role flags for Morning Brief views. Never hard-code people — grants only.
    Superadmins get owner visibility so Jordan can open Owner Pulse without a
    separate grant until /ui/admin is updated.
    """
    feats = effective_features(email)
    is_owner = "morning_owner" in feats or (email or "").strip().lower() in superadmin_emails()
    return {
        "is_ops": "morning_ops" in feats or "morning_gm" in feats,
        "is_gm": "morning_gm" in feats,
        "is_owner": is_owner,
    }
