"""
Standard-scope catalog — GVC's menu of offerings, in the portal state bucket.
=========================================================================
Powers the estimate form's "Scope Selection" section: the checkboxes, the
default scope text that prefills each editable box, and the optional default
price. Also drives the estimate PDF's "Additional Services" page (the full
menu of what GVC does).

SINGLE SOURCE OF TRUTH: one JSON object in the portal state bucket
(GVC_PORTAL_STATE_BUCKET, falling back to the preview bucket — same resolution
as portal_store; the state bucket has versioning ON and NO lifecycle rule per
the 2026-07-02 standing rule):

    portal/estimate/scope-catalog.json   (env: GVC_ESTIMATE_SCOPE_CATALOG_OBJECT)

Admins edit it in-app from the estimate page (Manage standard scopes), so
wording and prices change WITHOUT a redeploy. Unlike the COI blank (which has
no sensible default and hard-fails when missing), this catalog ships with a
sensible DEFAULT_CATALOG seeded from GVC's current offerings doc — so
`load_catalog()` NEVER raises and the tool works the moment it deploys, before
any admin edit. Once an admin saves, the stored object wins.

Object shape:

    {
      "version": 1,
      "updated_by": "jordan@greenvalleycontractors.com",
      "updated_at": "2026-07-14T15:00:00+00:00",
      "trades": [
        {"id": "drywall", "name": "Drywall", "scopes": [
          {"id": "drywall-58-board", "title": "5/8\" Board",
           "default_scope": "Provide labor and materials ...",
           "default_price": null}
        ]}
      ]
    }

Reuses portal_store's GCS plumbing (bucket/creds/blob) — one place knows how to
talk to the bucket. Reads are cached per instance for a short TTL (the catalog
changes rarely). This module knows nothing about HTTP or the estimate flow;
it's pure data + a thin GCS layer, so it's unit-testable without either.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from shared import portal_store as portal_store

PortalStoreNotConfigured = portal_store.PortalStoreNotConfigured

DOC_VERSION = 1
CATALOG_OBJECT_DEFAULT = "portal/estimate/scope-catalog.json"
MAX_CATALOG_BYTES = 512 * 1024  # generous: the whole menu is a few KB of text


class ScopeCatalogInvalid(ValueError):
    """The submitted catalog isn't a usable structure."""


# ---------------------------------------------------------------------------
# DEFAULT seed — GVC's current offerings. Transcribed from the offerings doc
# (Drywall / Metal Framing / ACT / Insulation filled; FRP / Door & Hardware /
# Tectum stubbed with a title but no scope text — admins fill them in-app).
# The "Edward Jones" section of that doc is a project sample, NOT an offering,
# so it is deliberately omitted.
# ---------------------------------------------------------------------------

_DRYWALL_HALF = (
    "Provide labor and materials for installation of half-inch drywall and "
    "mold-resistant (MD) board where noted per plans. Scope includes hanging, "
    "taping, mudding, sanding, and finishing to the specified level. Coordinate "
    "work with other trades and perform all work in accordance with project "
    "drawings, specifications, and applicable codes."
)
_DRYWALL_58 = (
    "Provide labor and materials for installation of 5/8\" Type X drywall and "
    "mold-resistant (MD) board where noted per plans. Scope includes hanging, "
    "taping, mudding, sanding, and finishing to the specified level. Coordinate "
    "work with other trades and perform all work in accordance with project "
    "drawings, specifications, and applicable codes."
)
_DRYWALL_HANG_FINISH = (
    "Provide and install 5/8\" gypsum wallboard at all designated wall and "
    "ceiling locations in accordance with the project plans and specifications. "
    "All installed drywall surfaces shall be taped, mudded, and sanded to achieve "
    "a Level 5 finish, unless otherwise noted on the drawings or specified in "
    "writing. Work shall include all standard fasteners, joint treatment, corner "
    "beads, and accessories required for a complete and code-compliant installation."
)
_METAL_FRAMING = (
    "Provide and install interior light gauge metal framing and associated "
    "blocking as required by the project plans and specifications. Work includes "
    "framing for interior partitions and typical support conditions, along with "
    "necessary bracing and reinforcement to accommodate wall assemblies and "
    "project requirements. Framing layout and installation shall generally follow "
    "applicable plan references and standard construction practices."
)
_ACT = (
    "Furnish and install suspended acoustical ceiling grid and acoustical ceiling "
    "tile systems in accordance with project plans and specifications. Scope "
    "includes installation of Armstrong Fine Fissured acoustical ceiling panels "
    "with Armstrong Prelude XL ceiling grid, including all required suspension "
    "components, hangers, and perimeter wall angle. Ceiling layout, elevations, "
    "and system configuration shall generally follow applicable plan callouts and "
    "standard installation practices."
)
_INSULATION = (
    "Install 3 ½\" sound attenuation insulation within newly framed wall "
    "assemblies where indicated. Installation shall be performed as required to "
    "support the project's acoustical performance criteria and applicable "
    "construction requirements."
)

DEFAULT_TRADES: list[dict] = [
    {
        "id": "drywall",
        "name": "Drywall",
        "scopes": [
            {"id": "drywall-half-board", "title": "1/2\" Board",
             "default_scope": _DRYWALL_HALF, "default_price": None},
            {"id": "drywall-58-board", "title": "5/8\" Board",
             "default_scope": _DRYWALL_58, "default_price": None},
            {"id": "drywall-hang-finish", "title": "Drywall Hang and Finish",
             "default_scope": _DRYWALL_HANG_FINISH, "default_price": None},
        ],
    },
    {
        "id": "metal-framing",
        "name": "Metal Framing",
        "scopes": [
            {"id": "metal-framing-light-gauge",
             "title": "Light Gauge Metal Framing and Blocking",
             "default_scope": _METAL_FRAMING, "default_price": None},
        ],
    },
    {
        "id": "act",
        "name": "ACT",
        "scopes": [
            {"id": "act-standard", "title": "Acoustic Ceiling Systems (ACT)",
             "default_scope": _ACT, "default_price": None},
        ],
    },
    {
        "id": "insulation",
        "name": "Insulation",
        "scopes": [
            {"id": "insulation-sound-attenuation",
             "title": "Sound Attenuation Insulation",
             "default_scope": _INSULATION, "default_price": None},
        ],
    },
    # --- Stubs: offering exists, scope text to be filled in-app by an admin ---
    {
        "id": "frp",
        "name": "FRP",
        "scopes": [
            {"id": "frp-panels", "title": "FRP Panels",
             "default_scope": "", "default_price": None},
        ],
    },
    {
        "id": "doors-hardware",
        "name": "Door and Hardware Labor",
        "scopes": [
            {"id": "doors-hardware-install",
             "title": "Door and Hardware Installation",
             "default_scope": "", "default_price": None},
        ],
    },
    {
        "id": "tectum",
        "name": "Tectum Panels",
        "scopes": [
            {"id": "tectum-panels", "title": "Tectum Panels",
             "default_scope": "", "default_price": None},
        ],
    },
]


def default_catalog() -> dict:
    """A fresh copy of the shipped default catalog (safe to mutate)."""
    return {
        "version": DOC_VERSION,
        "updated_by": None,
        "updated_at": None,
        "trades": json.loads(json.dumps(DEFAULT_TRADES)),
    }


def empty_catalog() -> dict:
    return {"version": DOC_VERSION, "updated_by": None,
            "updated_at": None, "trades": []}


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-tested directly)
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "item"


def _coerce_price(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        p = float(value)
    except (TypeError, ValueError):
        raise ScopeCatalogInvalid(f"Price {value!r} isn't a number.")
    if p < 0:
        raise ScopeCatalogInvalid("Price can't be negative.")
    return round(p, 2)


def validate_catalog(raw: Any) -> dict:
    """
    Validate + normalize a catalog structure. Returns a clean catalog dict
    (version/trades, ids slugified + de-duplicated, prices coerced). Raises
    ScopeCatalogInvalid with an office-readable message. Metadata
    (updated_by/at) is set by put_catalog, not here.
    """
    if not isinstance(raw, dict):
        raise ScopeCatalogInvalid("Catalog must be an object.")
    trades_in = raw.get("trades")
    if not isinstance(trades_in, list):
        raise ScopeCatalogInvalid("Catalog.trades must be a list.")

    trades_out: list[dict] = []
    seen_trade_ids: set[str] = set()
    for ti, trade in enumerate(trades_in, 1):
        if not isinstance(trade, dict):
            raise ScopeCatalogInvalid(f"Trade #{ti} must be an object.")
        name = (trade.get("name") or "").strip()
        if not name:
            raise ScopeCatalogInvalid(f"Trade #{ti}: a name is required.")
        tid = slugify(trade.get("id") or name)
        base_tid = tid
        n = 2
        while tid in seen_trade_ids:
            tid = f"{base_tid}-{n}"
            n += 1
        seen_trade_ids.add(tid)

        scopes_in = trade.get("scopes")
        if not isinstance(scopes_in, list) or not scopes_in:
            raise ScopeCatalogInvalid(
                f"Trade '{name}': at least one scope is required.")
        scopes_out: list[dict] = []
        seen_scope_ids: set[str] = set()
        for si, scope in enumerate(scopes_in, 1):
            if not isinstance(scope, dict):
                raise ScopeCatalogInvalid(
                    f"Trade '{name}', scope #{si} must be an object.")
            title = (scope.get("title") or "").strip()
            if not title:
                raise ScopeCatalogInvalid(
                    f"Trade '{name}', scope #{si}: a title is required.")
            sid = slugify(scope.get("id") or f"{tid}-{title}")
            base_sid = sid
            n = 2
            while sid in seen_scope_ids:
                sid = f"{base_sid}-{n}"
                n += 1
            seen_scope_ids.add(sid)
            scopes_out.append({
                "id": sid,
                "title": title,
                "default_scope": (scope.get("default_scope") or "").strip(),
                "default_price": _coerce_price(scope.get("default_price")),
            })
        trades_out.append({"id": tid, "name": name, "scopes": scopes_out})

    return {"version": DOC_VERSION, "trades": trades_out}


def find_scope(catalog: dict, trade_id: str, scope_id: str) -> Optional[dict]:
    for trade in catalog.get("trades", []):
        if trade.get("id") == trade_id:
            for scope in trade.get("scopes", []):
                if scope.get("id") == scope_id:
                    return scope
    return None


# --- Sentence -> bullet splitting (for the Scope Details page) --------------
# A hard line break is a FORCED bullet boundary (estimator override); within a
# line, prose is split into sentences. Conservative on abbreviations and
# decimals/measurements (5/8", 3.5, No. 5) so we don't shred construction text.

_ABBREVIATIONS = {
    "e.g", "i.e", "etc", "inc", "no", "approx", "vs", "dept", "co", "ltd",
    "mr", "mrs", "ms", "dr", "st", "ave", "ft", "min", "max", "qty", "ea",
}
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=["\'(\[A-Z0-9])')
_LEADING_GLYPH = re.compile(r'^[\-•–—\*·]\s*')


def _split_sentences(line: str) -> list[str]:
    parts = _SENTENCE_BOUNDARY.split(line)
    if len(parts) <= 1:
        return parts
    merged: list[str] = []
    for frag in parts:
        if merged:
            prev = merged[-1].rstrip()
            last_word = re.split(r"\s+", prev)[-1] if prev else ""
            key = last_word.rstrip(".!?\"')").lower()
            if key in _ABBREVIATIONS:
                merged[-1] = prev + " " + frag.lstrip()
                continue
        merged.append(frag)
    return merged


def split_scope_bullets(text: str) -> list[str]:
    """Turn a scope paragraph into sub-bullets. Hard newlines force a break;
    otherwise sentences split into one bullet each."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    bullets: list[str] = []
    for line in text.split("\n"):
        line = _LEADING_GLYPH.sub("", line.strip()).strip()
        if not line:
            continue
        for sent in _split_sentences(line):
            sent = sent.strip()
            if sent:
                bullets.append(sent)
    return bullets


def group_scope_details(items: list[dict]) -> list[dict]:
    """
    Group scope-bearing line items into the 3-level Scope Details hierarchy:
    Trade -> selected scope -> descriptor bullets. Order-preserving (first time
    a trade appears sets its position; scopes keep input order within it).

    `items` are line-item dicts that may carry scope_trade / scope_title /
    scope_detail. Items without scope_detail are skipped. Falls back to the
    line description for the scope title, and "General" for a missing trade.
    """
    groups: list[dict] = []
    index: dict[str, dict] = {}
    for li in items or []:
        detail = (li.get("scope_detail") or "").strip()
        if not detail:
            continue
        trade = (li.get("scope_trade") or "").strip() or "General"
        title = (li.get("scope_title") or li.get("description") or "").strip() or "Scope"
        grp = index.get(trade)
        if grp is None:
            grp = {"trade": trade, "scopes": []}
            index[trade] = grp
            groups.append(grp)
        grp["scopes"].append({"title": title, "bullets": split_scope_bullets(detail)})
    return groups


def build_additional_services(catalog: dict) -> list[dict]:
    """
    The "Additional Services" page menu: every trade + its scope titles, with a
    one-line summary (first sentence of the scope text) when available. Shows
    stubs too (title only) so the full menu of GVC capabilities is presented.
    """
    services: list[dict] = []
    for trade in catalog.get("trades", []):
        scopes = []
        for scope in trade.get("scopes", []):
            bullets = split_scope_bullets(scope.get("default_scope") or "")
            scopes.append({
                "title": scope.get("title", ""),
                "summary": bullets[0] if bullets else "",
            })
        if scopes:
            services.append({"trade": trade.get("name", ""), "scopes": scopes})
    return services


def catalog_counts(catalog: dict) -> tuple[int, int]:
    trades = catalog.get("trades", [])
    return len(trades), sum(len(t.get("scopes", [])) for t in trades)


# ---------------------------------------------------------------------------
# GCS I/O (reuses portal_store._blob for bucket/creds). Never raises on read —
# a missing/unconfigured/corrupt object falls back to the shipped default so the
# estimate flow keeps working.
# ---------------------------------------------------------------------------

_CACHE: dict[str, Any] = {"catalog": None, "source": None, "at": 0.0}


def _catalog_object() -> str:
    return os.environ.get("GVC_ESTIMATE_SCOPE_CATALOG_OBJECT") or CATALOG_OBJECT_DEFAULT


def _cache_ttl() -> float:
    try:
        return float(os.environ.get("GVC_ESTIMATE_SCOPE_CATALOG_CACHE_TTL") or "30")
    except ValueError:
        return 30.0


def invalidate_cache() -> None:
    _CACHE.update({"catalog": None, "source": None, "at": 0.0})


def _read_stored() -> Optional[tuple[dict, int]]:
    """Return (catalog, generation) from GCS, or None when there's no object.
    Raises PortalStoreNotConfigured when the bucket/creds are absent."""
    from google.api_core.exceptions import NotFound

    blob = portal_store._blob(_catalog_object())
    try:
        blob.reload()
        raw = blob.download_as_text()
    except NotFound:
        return None
    try:
        doc = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        return None  # corrupt object ≠ fatal; caller falls back to default
    if not isinstance(doc, dict) or "trades" not in doc:
        return None
    return doc, int(blob.generation or 0)


def load_catalog() -> dict:
    """
    The catalog to use, served from a short per-instance cache. Returns the
    stored object when present, else the shipped default. NEVER raises — if the
    bucket is unconfigured or the object is missing/corrupt, returns the
    default so estimates keep rendering.
    """
    now = time.time()
    if _CACHE["catalog"] is not None and (now - _CACHE["at"]) < _cache_ttl():
        return _CACHE["catalog"]
    catalog = default_catalog()
    source = "default"
    try:
        stored = _read_stored()
        if stored is not None:
            catalog = stored[0]
            catalog.setdefault("version", DOC_VERSION)
            catalog.setdefault("trades", [])
            source = "stored"
    except PortalStoreNotConfigured:
        pass
    except Exception:  # noqa: BLE001 — any read hiccup falls back to default
        pass
    _CACHE.update({"catalog": catalog, "source": source, "at": now})
    return catalog


def catalog_info() -> dict:
    """Lightweight metadata for the admin UI. Never raises."""
    catalog = load_catalog()
    trade_count, scope_count = catalog_counts(catalog)
    return {
        "source": _CACHE.get("source") or "default",
        "updated_by": catalog.get("updated_by"),
        "updated_at": catalog.get("updated_at"),
        "trade_count": trade_count,
        "scope_count": scope_count,
    }


def put_catalog(raw: Any, *, actor: str) -> dict:
    """
    Validate, stamp metadata, and store the catalog (full replace). Returns the
    stored catalog. Raises ScopeCatalogInvalid / PortalStoreNotConfigured.
    Last-writer-wins on a concurrent admin edit (a full replace makes merging
    meaningless), guarded so we notice the race and simply re-apply.
    """
    catalog = validate_catalog(raw)
    catalog["updated_by"] = (actor or "").strip().lower() or "unknown"
    catalog["updated_at"] = datetime.now(timezone.utc).isoformat()

    payload = json.dumps(catalog, indent=2).encode("utf-8")
    if len(payload) > MAX_CATALOG_BYTES:
        raise ScopeCatalogInvalid(
            f"Catalog is {len(payload) // 1024}KB — larger than the "
            f"{MAX_CATALOG_BYTES // 1024}KB cap. Trim some scope text.")

    from google.api_core.exceptions import NotFound, PreconditionFailed

    blob = portal_store._blob(_catalog_object())
    try:
        blob.reload()
        gen = int(blob.generation or 0)
    except NotFound:
        gen = 0
    try:
        blob.upload_from_string(payload, content_type="application/json",
                                if_generation_match=gen)
    except PreconditionFailed:
        blob.reload()
        blob.upload_from_string(payload, content_type="application/json",
                                if_generation_match=int(blob.generation or 0))
    invalidate_cache()
    return catalog
