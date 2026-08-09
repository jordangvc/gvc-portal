"""Inventory search — alias-aware, typo-tolerant, availability-boosted.

Modeled on subsystems/fieldguide/search.py: exact code > exact alias >
name > token overlap, with a boost for items available at the transaction's
current location. In-process over a few hundred items — sub-millisecond.
"""
from __future__ import annotations

from decimal import Decimal

from subsystems.inventory.model import dec_str


def _tokens(q: str) -> list[str]:
    return [t for t in (q or "").lower().replace(",", " ").replace("-", " ")
            .split() if t]


def _fuzzy_token_hit(token: str, hay: str) -> bool:
    """Cheap typo tolerance: prefix hit, or single edit for tokens ≥ 4
    chars (covers 'scews'→'screws', 'nals'→'nails')."""
    if token in hay:
        return True
    if len(token) < 4:
        return False
    words = hay.split()
    for w in words:
        if abs(len(w) - len(token)) > 1:
            continue
        # one-substitution / one-gap check without pulling in a dep
        if len(w) == len(token):
            if sum(1 for a, b in zip(w, token) if a != b) <= 1:
                return True
        else:
            longer, shorter = (w, token) if len(w) > len(token) else (token, w)
            for i in range(len(longer)):
                if longer[:i] + longer[i + 1:] == shorter:
                    return True
    return False


def score_item(item: dict, query: str, *, at_location_qty=None) -> int:
    q = (query or "").strip().lower()
    if not q:
        return 0
    score = 0
    name = (item.get("name") or "").lower()
    aliases = [a.lower() for a in (item.get("aliases") or [])]
    barcodes = [b.lower() for b in (item.get("barcodes") or [])]
    token = (item.get("scan_token") or "").lower()
    iid = (item.get("id") or "").lower()

    if q in (iid, token) or q in barcodes:
        score += 100
    if q == name:
        score += 90
    if q in aliases:
        score += 85
    if name.startswith(q):
        score += 45
    elif q in name:
        score += 35
    if any(a.startswith(q) or q in a for a in aliases):
        score += 30

    hay = " ".join([name, *aliases, (item.get("category") or "").lower()])
    toks = _tokens(q)
    if toks:
        hits = sum(1 for t in toks if _fuzzy_token_hit(t, hay))
        if hits == len(toks):
            score += 25
        else:
            score += 6 * hits

    if score and at_location_qty is not None:
        try:
            if Decimal(str(at_location_qty)) > 0:
                score += 20  # available where the user is working
        except Exception:
            pass
    return score


def search_items(catalog_doc: dict, ledger_doc: dict, query: str, *,
                 location_id: str = "", limit: int = 20,
                 include_archived: bool = False) -> list[dict]:
    balances = ledger_doc.get("balances") or {}
    out = []
    for item in (catalog_doc.get("items") or {}).values():
        if item.get("merged_into"):
            continue
        if item.get("archived") and not include_archived:
            continue
        at_loc = None
        if location_id:
            at_loc = (balances.get(item["id"]) or {}).get(location_id, "0")
        s = score_item(item, query, at_location_qty=at_loc)
        if s > 0:
            total = sum((Decimal(v) for v in
                         (balances.get(item["id"]) or {}).values()),
                        Decimal(0))
            out.append({"item": item, "score": s,
                        "on_hand_total": dec_str(total),
                        "on_hand_here": str(at_loc) if at_loc is not None
                        else None})
    out.sort(key=lambda r: (-r["score"], r["item"]["name"].lower()))
    return out[:max(1, min(limit, 100))]
