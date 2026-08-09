"""Inventory flows — one function per operation; ALL store I/O lives here.

Layering: app/service.py routes call these; these call subsystems/inventory
(pure) + subsystems/inventory/store (GCS). Every mutation logs a structured
activity event (Cloud Logging is the portal's audit spine) and returns the
{ok, ...} shapes the UI renders. InventoryError bubbles to the route layer,
which maps it onto the portal's {code, detail, advice} envelope.
"""
from __future__ import annotations

import csv
import io
import sys
from decimal import Decimal

from shared import activity
from subsystems.inventory import attention as att
from subsystems.inventory import catalog as cat
from subsystems.inventory import counts as cnt
from subsystems.inventory import ledger as led
from subsystems.inventory import locations as locs
from subsystems.inventory import search as srch
from subsystems.inventory import store
from subsystems.inventory.model import (
    FLAG_CONDITIONS, InventoryError, new_uuid, now_iso, require,
)

MAX_IMPORT_ROWS = 2000


def _docs() -> tuple[dict, dict, dict]:
    """Read-only snapshot of catalog + locations + ledger."""
    c, _ = store.read_doc(store.CATALOG)
    l, _ = store.read_doc(store.LOCATIONS)
    g, _ = store.read_doc(store.LEDGER)
    return cat.ensure_shape(c), locs.ensure_shape(l), led.ensure_shape(g)


# ------------------------------------------------------------------ reading

def overview(email: str) -> dict:
    c, l, g = _docs()
    a, _ = store.read_doc(store.ATTENTION)
    n_items = sum(1 for i in c["items"].values()
                  if not i.get("archived") and not i.get("merged_into"))
    n_assets = sum(1 for x in g["assets"].values()
                   if x.get("condition") not in ("retired", "lost"))
    n_kits = sum(1 for k in g["kits"].values() if not k.get("dissolved"))
    low = cat.low_stock_hits(c, g["balances"])
    open_att = att.open_items(att.ensure_shape(a))
    recent = [_event_view(e, c, l) for e in reversed(g["events"][-8:])]
    counts_doc, _ = store.read_doc(store.COUNTS)
    open_counts = [s for s in (counts_doc.get("sessions") or {}).values()
                   if s.get("status") in ("open", "submitted")]
    my_counts = [s for s in open_counts
                 if s.get("assignee") == email.lower()
                 and s.get("status") == "open"]
    return {
        "ok": True, "generated_at": now_iso(),
        "totals": {"items": n_items, "assets": n_assets, "kits": n_kits,
                   "locations": sum(1 for x in l["locations"].values()
                                    if x.get("active", True))},
        "low_stock": low[:20],
        "attention_open": len(open_att),
        "attention": open_att[:10],
        "open_counts": [{"id": s["id"], "kind": s["kind"],
                         "location": locs.path_name(l, s["location_id"]),
                         "status": s["status"],
                         "assignee": s.get("assignee", "")}
                        for s in open_counts][:10],
        "my_counts": [{"id": s["id"], "kind": s["kind"],
                       "location": locs.path_name(l, s["location_id"])}
                      for s in my_counts],
        "recent": recent,
    }


def _event_view(e: dict, c: dict, l: dict) -> dict:
    return {
        "txn_no": e["txn_no"], "type": e["type"], "status": e["status"],
        "actor": e["actor"], "ts": e["ts"],
        "src": locs.path_name(l, e["src"]) if e.get("src") else "",
        "dst": locs.path_name(l, e["dst"]) if e.get("dst") else "",
        "note": e.get("note", ""), "reason": e.get("reason", ""),
        "job_ref": e.get("job_ref", ""),
        "reverses": e.get("reverses", ""),
        "reversed_by": e.get("reversed_by", ""),
        "negative_override": bool(e.get("negative_override")),
        "lines": [{
            "kind": ln.get("kind"), "item_name": ln.get("item_name"),
            "item_id": ln.get("item_id"),
            "qty": ln.get("entered_qty"), "unit": ln.get("entered_unit"),
            "sign": ln.get("sign", 1),
            "asset_id": ln.get("asset_id", ""),
            "kit_id": ln.get("kit_id", ""),
            "condition_to": ln.get("condition_to", ""),
            "src": locs.path_name(l, ln["src"])
                   if ln.get("src") and not str(ln["src"]).startswith("kit:")
                   else ln.get("src", ""),
            "dst": locs.path_name(l, ln["dst"])
                   if ln.get("dst") and not str(ln["dst"]).startswith("kit:")
                   else ln.get("dst", ""),
        } for ln in e.get("lines", [])],
    }


def search(email: str, q: str, *, location_id: str = "",
           limit: int = 20, include_archived: bool = False) -> dict:
    c, l, g = _docs()
    hits = srch.search_items(c, g, q, location_id=location_id, limit=limit,
                             include_archived=include_archived)
    return {"ok": True, "results": [{
        "item": _item_view(h["item"], g),
        "score": h["score"],
        "on_hand_total": h["on_hand_total"],
        "on_hand_here": h["on_hand_here"],
    } for h in hits]}


def _item_view(item: dict, g: dict) -> dict:
    per_loc = dict((g.get("balances") or {}).get(item["id"]) or {})
    return {k: item.get(k) for k in
            ("id", "name", "tracking", "base_unit", "conversions",
             "category", "aliases", "barcodes", "scan_token", "image_url",
             "notes", "min_qty", "archived", "kit_components")} | {
        "balances": per_loc}


def item_detail(item_id: str) -> dict:
    c, l, g = _docs()
    item = cat.get_item(c, item_id, allow_archived=True)
    view = _item_view(item, g)
    view["locations"] = [
        {"location_id": lid, "location": locs.path_name(l, lid),
         "qty": qty}
        for lid, qty in sorted(view["balances"].items())]
    if item["tracking"] == "asset":
        view["assets"] = [
            {**{k: a.get(k) for k in ("id", "serial", "make", "model",
                                      "condition", "scan_token", "notes")},
             "location_id": a.get("location"),
             "location": locs.path_name(l, a.get("location", ""))}
            for a in g["assets"].values() if a.get("item_id") == item["id"]]
    history = [_event_view(e, c, l) for e in reversed(g["events"])
               if any(ln.get("item_id") == item["id"]
                      for ln in e.get("lines", []))][:50]
    view["history"] = history
    return {"ok": True, "item": view}


def location_contents(loc_id: str) -> dict:
    c, l, g = _docs()
    loc = locs.get_location(l, loc_id, allow_inactive=True)
    h = led.location_holdings(g, loc_id)
    items = []
    for item_id, qty in sorted(h["items"].items()):
        try:
            item = cat.get_item(c, item_id, allow_archived=True)
        except InventoryError:
            item = {"id": item_id, "name": item_id, "base_unit": ""}
        items.append({"item_id": item_id, "name": item["name"],
                      "qty": qty, "unit": item.get("base_unit", "")})
    return {"ok": True,
            "location": {**loc, "path": locs.path_name(l, loc_id)},
            "items": items,
            "assets": [{**{k: a.get(k) for k in
                           ("id", "item_id", "serial", "condition")},
                        "name": _safe_name(c, a.get("item_id"))}
                       for a in h["assets"]],
            "kits": [{**{k: k2.get(k) for k in
                         ("id", "name", "condition")},
                      "components": k2.get("components", [])}
                     for k2 in h["kits"]]}


def _safe_name(c: dict, item_id: str) -> str:
    try:
        return cat.get_item(c, item_id, allow_archived=True)["name"]
    except InventoryError:
        return item_id or "?"


def locations_list(*, include_inactive: bool = False) -> dict:
    l, _ = store.read_doc(store.LOCATIONS)
    l = locs.ensure_shape(l)
    out = []
    for loc in sorted(l["locations"].values(), key=lambda x: x["name"]):
        if not include_inactive and not loc.get("active", True):
            continue
        out.append({**loc, "path": locs.path_name(l, loc["id"])})
    return {"ok": True, "locations": out}


def history(*, limit: int = 50, item_id: str = "", location_id: str = "",
            actor: str = "", txn_type: str = "") -> dict:
    c, l, g = _docs()
    out = []
    for e in reversed(g["events"]):
        if txn_type and e["type"] != txn_type:
            continue
        if actor and e["actor"] != actor.lower():
            continue
        if item_id and not any(ln.get("item_id") == item_id
                               for ln in e["lines"]):
            continue
        if location_id and location_id not in (
                e.get("src"), e.get("dst"),
                *[ln.get("src") for ln in e["lines"]],
                *[ln.get("dst") for ln in e["lines"]]):
            continue
        out.append(_event_view(e, c, l))
        if len(out) >= max(1, min(limit, 200)):
            break
    return {"ok": True, "events": out}


def resolve_scan(email: str, code: str) -> dict:
    """A scanned/typed code → what it is + what you can do with it."""
    code = (code or "").strip()
    require(bool(code), "INVALID_INPUT", "Nothing scanned.", field="code")
    c, l, g = _docs()
    loc = locs.resolve_token(l, code)
    if loc is not None:
        return {"ok": True, "kind": "location",
                "location": {**loc, "path": locs.path_name(l, loc["id"])}}
    for a in g["assets"].values():
        if a.get("scan_token") == code or a.get("id") == code:
            return {"ok": True, "kind": "asset",
                    "asset": {**a, "name": _safe_name(c, a["item_id"]),
                              "location_path":
                                  locs.path_name(l, a.get("location", ""))}}
    for k in g["kits"].values():
        if (k.get("scan_token") == code or k.get("id") == code) \
                and not k.get("dissolved"):
            return {"ok": True, "kind": "kit", "kit": k}
    item = cat.resolve_barcode(c, code)
    if item is not None:
        return {"ok": True, "kind": "item", "item": _item_view(item, g)}
    return {"ok": True, "kind": "unknown", "code": code}


# ----------------------------------------------------------------- posting

def post_transaction(payload: dict, *, actor: str,
                     can_manage: bool) -> dict:
    c, _ = store.read_doc(store.CATALOG)
    c = cat.ensure_shape(c)
    l, _ = store.read_doc(store.LOCATIONS)
    l = locs.ensure_shape(l)
    txn = dict(payload)
    txn["actor"] = actor
    if txn.get("allow_negative") and not can_manage:
        raise InventoryError(
            "FORBIDDEN", "Only inventory managers can override stock.",
            advice="Ask a manager, or run a count if the shelf disagrees "
                   "with the app.")
    # Retiring an asset removes it from circulation - manager territory
    # even though field users may flag damage/lost (review finding 15).
    if not can_manage and any(
            str(ln.get("condition_to") or "") == "retired"
            for ln in (txn.get("lines") or []) if isinstance(ln, dict)):
        raise InventoryError(
            "FORBIDDEN", "Only inventory managers can retire an asset.",
            advice="Report it as damaged or needs repair; a manager "
                   "retires it.")
    # Photos never ride ledger lines (they'd balloon the single-doc
    # ledger); lift them into the attention side-channel instead.
    photos: list[dict] = []
    txn["lines"] = [dict(ln) if isinstance(ln, dict) else ln
                    for ln in (txn.get("lines") or [])]
    for ln in txn["lines"]:
        if isinstance(ln, dict) \
                and len(str(ln.get("photo_url") or "")) > 2000:
            photos.append({"asset_id": ln.get("asset_id", ""),
                           "photo_data": str(ln.pop("photo_url"))[:400000]})

    def fn(doc: dict):
        return led.post(doc, c, l, txn, can_override_negative=can_manage)

    result = store.mutate(store.LEDGER, fn)
    event = result.get("txn") or {}
    activity.log_event(
        "inventory.post", actor=actor,
        target=f"{event.get('type')}:{event.get('txn_no')}",
        result="duplicate" if result.get("already") else "ok",
        lines=len(event.get("lines") or []))
    if not result.get("already"):
        _after_post_hooks(event, c, actor, photos=photos)
    return result


def _after_post_hooks(event: dict, c: dict, actor: str, *,
                      photos: list[dict] | None = None) -> None:
    """Attention side-effects. Best-effort — a hook failure never breaks a
    posted transaction (the post already committed)."""
    try:
        if event.get("negative_override"):
            store.mutate(store.ATTENTION, lambda d: att.raise_item(
                d, kind="negative_override",
                title=f"Stock override on {event['txn_no']}",
                detail=event.get("reason", ""), actor=actor,
                txn_no=event["txn_no"]))
        g, _ = store.read_doc(store.LEDGER)
        hits = cat.low_stock_hits(c, led.ensure_shape(g)["balances"])
        touched = {ln.get("item_id") for ln in event.get("lines", [])}
        for h in hits:
            if h["item_id"] in touched:
                store.mutate(store.ATTENTION, lambda d, h=h: att.raise_item(
                    d, kind="low_stock",
                    title=f"Low stock: {h['name']}",
                    detail=f"{h['on_hand']} on hand vs min {h['min_qty']}",
                    payload=h, actor="system",
                    dedupe_key=f"low:{h['item_id']}:{h['location_id']}"))
        photo_by_asset = {p["asset_id"]: p["photo_data"]
                          for p in (photos or [])}
        for ln in event.get("lines", []):
            if ln.get("condition_to") in FLAG_CONDITIONS:
                store.mutate(store.ATTENTION, lambda d, ln=ln: att.raise_item(
                    d, kind="damage",
                    title=f"{ln.get('item_name')} ({ln.get('asset_id')}) "
                          f"→ {ln['condition_to']}",
                    detail=ln.get("note", ""),
                    payload={"asset_id": ln.get("asset_id"),
                             "photo_data": photo_by_asset.get(
                                 ln.get("asset_id", ""), ""),
                             "photo_url": ln.get("photo_url", "")},
                    actor=actor, txn_no=event["txn_no"]))
            if ln.get("condition_to") == "lost":
                store.mutate(store.ATTENTION, lambda d, ln=ln: att.raise_item(
                    d, kind="asset_lost",
                    title=f"Asset lost: {ln.get('asset_id')}",
                    detail=ln.get("note", ""), actor=actor,
                    txn_no=event["txn_no"]))
    except Exception as exc:  # noqa: BLE001
        print(f"[inventory] attention hook failed: {exc}", file=sys.stderr)


def reverse_transaction(txn_no: str, *, actor: str, reason: str,
                        client_uuid: str,
                        allow_negative: bool = False) -> dict:
    c, l, _ = _docs()

    def fn(doc: dict):
        return led.reverse(doc, c, l, txn_no, actor=actor, reason=reason,
                           client_uuid=client_uuid,
                           allow_negative=allow_negative)

    result = store.mutate(store.LEDGER, fn)
    activity.log_event("inventory.reverse", actor=actor, target=txn_no,
                       result="duplicate" if result.get("already") else "ok")
    if not result.get("already"):
        _after_post_hooks(result.get("txn") or {}, c, actor)
    return result


# ---------------------------------------------------------------- catalog

def save_item(payload: dict, *, actor: str, item_id: str = "") -> dict:
    item_box: dict = {}

    def fn(doc: dict):
        new, item = cat.upsert_item(doc, payload, actor=actor,
                                    item_id=item_id)
        item_box.update(item)
        return new, item

    item = store.mutate(store.CATALOG, fn)
    activity.log_event("inventory.item", actor=actor,
                       target=item_box.get("id", item_id), result="ok")
    return {"ok": True, "item": item}


def archive_item(item_id: str, archived: bool, *, actor: str) -> dict:
    item = store.mutate(store.CATALOG, lambda d: cat.set_archived(
        d, item_id, archived, actor=actor))
    activity.log_event("inventory.item.archive", actor=actor,
                       target=f"{item_id}:{archived}", result="ok")
    return {"ok": True, "item": item}


def merge_item(source_id: str, target_id: str, *, actor: str) -> dict:
    """Move the source's balances to the target FIRST (while both items
    still resolve as themselves), then commit the catalog merge. Order
    matters: after the merge, source ids resolve to the target, so a
    post-merge transfer would net to zero on the target and strand the
    source balance (adversarial review finding 1). Both steps are
    idempotent (deterministic uuids; merge_items re-run is a no-op merge),
    so a retry after a partial failure completes cleanly."""
    c, l, g = _docs()
    cat.merge_items(c, source_id, target_id, actor=actor)  # validate only
    src_balances = dict((g["balances"].get(source_id) or {}))
    for loc_id, qty in src_balances.items():
        txn = {"client_uuid": f"merge-{source_id}-{target_id}-{loc_id}",
               "type": "MANUAL_ADJUSTMENT", "actor": actor,
               "reason": f"merge {source_id} → {target_id}",
               "dst": loc_id,
               "lines": [
                   {"item_id": source_id, "qty": qty, "unit": "",
                    "sign": -1},
                   {"item_id": target_id, "qty": qty, "unit": "",
                    "sign": 1}]}
        _fill_base_units(c, txn)
        store.mutate(store.LEDGER, lambda d, t=txn: led.post(
            d, c, l, t, can_override_negative=True))
    target = store.mutate(store.CATALOG, lambda d: cat.merge_items(
        d, source_id, target_id, actor=actor))
    activity.log_event("inventory.item.merge", actor=actor,
                       target=f"{source_id}->{target_id}", result="ok")
    return {"ok": True, "item": target,
            "moved_locations": len(src_balances)}


def _fill_base_units(c: dict, txn: dict) -> None:
    for ln in txn.get("lines", []):
        if not ln.get("unit"):
            item = cat.get_item(c, ln["item_id"], allow_archived=True)
            ln["unit"] = item["base_unit"]


# --------------------------------------------------------------- locations

def save_location(payload: dict, *, actor: str, loc_id: str = "") -> dict:
    loc = store.mutate(store.LOCATIONS, lambda d: locs.upsert_location(
        d, payload, actor=actor, loc_id=loc_id))
    activity.log_event("inventory.location", actor=actor,
                       target=loc.get("id", ""), result="ok")
    return {"ok": True, "location": loc}


def set_location_active(loc_id: str, active: bool, *, actor: str) -> dict:
    g, _ = store.read_doc(store.LEDGER)
    empty = led.holdings_empty(led.ensure_shape(g), loc_id)
    loc = store.mutate(store.LOCATIONS, lambda d: locs.set_active(
        d, loc_id, active, actor=actor, holdings_empty=empty))
    activity.log_event("inventory.location.active", actor=actor,
                       target=f"{loc_id}:{active}", result="ok")
    return {"ok": True, "location": loc}


def rotate_location_token(loc_id: str, *, actor: str) -> dict:
    loc = store.mutate(store.LOCATIONS, lambda d: locs.rotate_token(
        d, loc_id, actor=actor))
    activity.log_event("inventory.location.token", actor=actor,
                       target=loc_id, result="ok")
    return {"ok": True, "location": loc}


def my_custody_location(email: str, display_name: str = "") -> dict:
    before, _ = store.read_doc(store.LOCATIONS)
    known = {x.get("employee_email") for x in
             (before.get("locations") or {}).values()
             if x.get("kind") == "employee"}
    loc = store.mutate(store.LOCATIONS, lambda d:
                       locs.ensure_employee_location(d, email, display_name))
    if email.lower() not in known:
        activity.log_event("inventory.location", actor=email,
                           target=loc.get("id", ""), result="ok",
                           kind="employee-custody-auto")
    return {"ok": True, "location": loc}


# ------------------------------------------------------------------ assets

def create_asset(payload: dict, *, actor: str) -> dict:
    c, _ = store.read_doc(store.CATALOG)
    c = cat.ensure_shape(c)
    asset = store.mutate(store.LEDGER, lambda d: led.create_asset(
        d, c, payload, actor=actor))
    activity.log_event("inventory.asset.create", actor=actor,
                       target=asset.get("id", ""), result="ok")
    return {"ok": True, "asset": asset}


# ------------------------------------------------------------------ counts

def start_count(*, kind: str, location_id: str, assignee: str,
                actor: str, extra_item_ids: list[str] | None = None) -> dict:
    c, l, g = _docs()
    locs.get_location(l, location_id)
    balances_at = {}
    for item_id, row in g["balances"].items():
        if location_id in row:
            balances_at[item_id] = row[location_id]
    names = {i["id"]: i["name"] for i in c["items"].values()}
    session = store.mutate(store.COUNTS, lambda d: cnt.create_session(
        d, kind=kind, location_id=location_id,
        ledger_balances_at_loc=balances_at, item_names=names,
        assignee=assignee, actor=actor, extra_item_ids=extra_item_ids))
    activity.log_event("inventory.count.start", actor=actor,
                       target=session["id"], result="ok")
    return {"ok": True, "session": session}


def _count_session(sid: str) -> dict:
    doc, _ = store.read_doc(store.COUNTS)
    s = (cnt.ensure_shape(doc).get("sessions") or {}).get(sid)
    require(s is not None, "UNKNOWN_SESSION",
            f"Count '{sid}' does not exist.", field="session_id")
    return s


def count_view(sid: str, *, viewer: str, can_manage: bool = False) -> dict:
    s = _count_session(sid)
    if s["kind"] == "blind" and s["status"] == "open" and not can_manage:
        # Blind means blind: only the assignee may even open it, and the
        # assignee sees no expected values (review finding 7).
        require(viewer.lower() == s.get("assignee"), "FORBIDDEN",
                "This blind audit is assigned to someone else.",
                field="session_id")
        s = cnt.strip_expected(s)
    return {"ok": True, "session": s}


def _require_counter(sid: str, actor: str, can_manage: bool) -> None:
    s = _count_session(sid)
    require(can_manage or actor.lower() == s.get("assignee"), "FORBIDDEN",
            "This count is assigned to someone else.", field="session_id",
            advice="Ask a manager to reassign it.")


def count_record(sid: str, item_id: str, *, counted=None,
                 skipped: bool = False, skip_reason: str = "",
                 note: str = "", actor: str = "",
                 can_manage: bool = False) -> dict:
    _require_counter(sid, actor, can_manage)
    s = store.mutate(store.COUNTS, lambda d: cnt.record_line(
        d, sid, item_id, counted=counted, skipped=skipped,
        skip_reason=skip_reason, note=note, actor=actor))
    activity.log_event("inventory.count.record", actor=actor,
                       target=f"{sid}:{item_id}", result="ok")
    return {"ok": True, "session_status": s["status"]}


def count_submit(sid: str, *, actor: str, can_manage: bool = False) -> dict:
    _require_counter(sid, actor, can_manage)
    s = store.mutate(store.COUNTS, lambda d: cnt.submit(d, sid,
                                                        actor=actor))
    activity.log_event("inventory.count.submit", actor=actor, target=sid,
                       result="ok")
    return {"ok": True, "session": s, "variances": cnt.variances(s)}


def count_approve(sid: str, *, actor: str, reject: bool = False) -> dict:
    """Adjustments post FIRST with deterministic idempotent uuids; the
    session flips to approved (recording the txn ids) only afterwards, so
    a mid-flight failure is retryable instead of stranding an approved
    session with no adjustments (review findings 5/14)."""
    posted: list[str] = []
    doc, _ = store.read_doc(store.COUNTS)
    txns = cnt.pending_adjustments(doc, sid, actor=actor)  # validates state
    if txns and not reject:
        c, l, _ = _docs()
        for i, txn in enumerate(txns):
            txn["client_uuid"] = f"count-{sid}-{i}"  # deterministic
            txn["actor"] = actor
            _fill_base_units(c, txn)
            res = store.mutate(store.LEDGER, lambda d, t=txn: led.post(
                d, c, l, t, can_override_negative=True))
            posted.append(res["txn"]["txn_no"])
            if not res.get("already"):
                store.mutate(store.ATTENTION, lambda d, t=res["txn"]:
                             att.raise_item(
                                 d, kind="count_discrepancy",
                                 title=f"Count variance posted ({sid})",
                                 detail=f"{len(t['lines'])} item(s) "
                                        "adjusted",
                                 actor=actor, txn_no=t["txn_no"]))

    box: dict = {}

    def fn(doc2: dict):
        new2, s, _ = cnt.approve(doc2, sid, actor=actor, reject=reject,
                                 adjustment_txns=posted)
        box["session"] = s
        return new2, s

    store.mutate(store.COUNTS, fn)
    activity.log_event("inventory.count.close", actor=actor, target=sid,
                       result="rejected" if reject else "ok",
                       adjustments=len(posted))
    return {"ok": True, "session": box.get("session"),
            "adjustment_txns": posted}


# --------------------------------------------------- unknown item / damage

def submit_unknown_item(payload: dict, *, actor: str) -> dict:
    name = str(payload.get("name") or "").strip()
    require(bool(name), "INVALID_INPUT",
            "Give it the name your crew uses.", field="name")
    rec = store.mutate(store.ATTENTION, lambda d: att.raise_item(
        d, kind="unknown_item", title=f"Unknown item: {name}",
        detail=str(payload.get("note") or ""),
        payload={"name": name,
                 "qty": str(payload.get("qty") or ""),
                 "unit": str(payload.get("unit") or ""),
                 "location_id": str(payload.get("location_id") or ""),
                 "photo_data": str(payload.get("photo_data") or "")[:400000]},
        actor=actor))
    activity.log_event("inventory.unknown", actor=actor, target=name,
                       result="ok")
    return {"ok": True, "attention": rec}


def attention_list(*, kind: str = "") -> dict:
    doc, _ = store.read_doc(store.ATTENTION)
    return {"ok": True, "items": att.open_items(att.ensure_shape(doc),
                                                kind=kind)}


def attention_update(aid: str, *, status: str, actor: str, note: str = "",
                     assigned_to: str = "") -> dict:
    rec = store.mutate(store.ATTENTION, lambda d: att.update_status(
        d, aid, status=status, actor=actor, note=note,
        assigned_to=assigned_to))
    activity.log_event("inventory.attention", actor=actor,
                       target=f"{aid}:{status}", result="ok")
    return {"ok": True, "attention": rec}


# ------------------------------------------------------------------ import

IMPORT_COLUMNS = ("name", "tracking", "base_unit", "category", "aliases",
                  "qty", "location", "serial")


def import_preview(csv_text: str, *, actor: str) -> dict:
    """Dry run: parse, validate, and report row-by-row without writing."""
    c, l, g = _docs()
    rows, errors, plans = _parse_import(csv_text, c, l)
    return {"ok": True, "dry_run": True, "rows": len(rows),
            "valid": len(plans), "errors": errors,
            "plan": [p["describe"] for p in plans][:200]}


def import_commit(csv_text: str, *, actor: str) -> dict:
    """All items are created in ONE catalog write; balances land as
    chunked INITIAL_LOAD txns with DETERMINISTIC uuids derived from the
    CSV content, so a timed-out or retried commit resumes idempotently
    instead of duplicating (review finding 9)."""
    import hashlib
    c, l, g = _docs()
    rows, errors, plans = _parse_import(csv_text, c, l)
    require(not errors, "IMPORT_INVALID",
            f"{len(errors)} row(s) failed validation — nothing imported.",
            field="csv",
            advice="Fix the rows in the error report and re-upload; the "
                   "import commits only when every row is clean.")
    digest = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()[:12]

    new_plans = [p for p in plans if p["kind"] == "new_item"]
    created_items = 0
    if new_plans:
        def make_items(doc: dict):
            cur = doc
            made = []
            for p2 in new_plans:
                cur, item = cat.upsert_item(cur, p2["item_payload"],
                                            actor=actor)
                made.append(item)
            return cur, made
        made = store.mutate(store.CATALOG, make_items)
        for p2, item in zip(new_plans, made):
            p2["item_id"] = item["id"]
        created_items = len(made)
    c, _ = store.read_doc(store.CATALOG)
    c = cat.ensure_shape(c)

    created_assets = 0
    loaded_lines = []
    for p2 in plans:
        if p2.get("asset_payload") is not None:
            ap = dict(p2["asset_payload"])
            ap["item_id"] = p2.get("item_id") or ap["item_id"]
            try:
                create_asset(ap, actor=actor)
                created_assets += 1
            except InventoryError as e:
                if e.code != "DUPLICATE_ASSET":  # retry-safe
                    raise
        elif p2.get("load_line") is not None:
            line = dict(p2["load_line"])
            line["item_id"] = p2.get("item_id") or line["item_id"]
            loaded_lines.append((p2["location_id"], line))
    posted = []
    if loaded_lines:
        c, l, _ = _docs()
        by_loc: dict[str, list] = {}
        for loc_id, line in loaded_lines:
            by_loc.setdefault(loc_id, []).append(line)
        for loc_id, lines in by_loc.items():
            for ci in range(0, len(lines), 150):  # < ledger MAX_LINES
                chunk = lines[ci:ci + 150]
                txn = {"client_uuid":
                       f"import-{digest}-{loc_id}-{ci // 150}",
                       "type": "INITIAL_LOAD", "actor": actor,
                       "dst": loc_id, "lines": chunk,
                       "note": "CSV import"}
                res = store.mutate(store.LEDGER, lambda d, t=txn: led.post(
                    d, c, l, t))
                posted.append(res["txn"]["txn_no"])
    job = {"id": f"imp-{digest}", "actor": actor, "at": now_iso(),
           "rows": len(rows), "items_created": created_items,
           "assets_created": created_assets, "load_txns": posted}
    store.mutate(store.IMPORTS, lambda d: _append_import(d, job))
    activity.log_event("inventory.import", actor=actor,
                       target=job["id"], result="ok", rows=len(rows))
    return {"ok": True, "dry_run": False, **job}


def _append_import(doc: dict, job: dict):
    import copy
    new = copy.deepcopy(doc)
    new.setdefault("jobs", []).append(job)
    return new, job


def _parse_import(csv_text: str, c: dict, l: dict):
    require(bool((csv_text or "").strip()), "INVALID_INPUT",
            "The CSV is empty.", field="csv")
    reader = csv.DictReader(io.StringIO(csv_text))
    require(reader.fieldnames is not None, "INVALID_INPUT",
            "The CSV has no header row.", field="csv")
    headers = [h.strip().lower() for h in reader.fieldnames]
    require("name" in headers, "IMPORT_INVALID",
            "The CSV needs at least a 'name' column.", field="csv",
            advice=f"Recognized columns: {', '.join(IMPORT_COLUMNS)}.")
    by_name = {i["name"].lower(): i for i in c["items"].values()
               if not i.get("merged_into")}
    loc_by_name = {x["name"].lower(): x for x in l["locations"].values()
                   if x.get("active", True)}
    rows, errors, plans = [], [], []
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_IMPORT_ROWS:
            errors.append({"row": n, "error": "row limit exceeded"})
            break
        row = {(k or "").strip().lower(): (v or "").strip()
               for k, v in raw.items()}
        rows.append(row)
        name = row.get("name", "")
        if not name:
            errors.append({"row": n, "error": "name is empty"})
            continue
        tracking = (row.get("tracking") or "quantity").lower()
        existing = by_name.get(name.lower())
        plan = {"kind": "existing_item" if existing else "new_item",
                "item_id": existing["id"] if existing else None}
        if not existing:
            plan["item_payload"] = {
                "name": name, "tracking": tracking,
                "base_unit": (row.get("base_unit") or "each").lower(),
                "category": row.get("category", ""),
                "aliases": [a.strip() for a in
                            (row.get("aliases") or "").split("|")
                            if a.strip()]}
        loc_name = row.get("location", "")
        loc = loc_by_name.get(loc_name.lower()) if loc_name else None
        if loc_name and loc is None:
            errors.append({"row": n,
                           "error": f"location '{loc_name}' not found"})
            continue
        if tracking == "asset":
            if loc is None:
                errors.append({"row": n,
                               "error": "asset rows need a location"})
                continue
            plan["asset_payload"] = {"item_id": plan["item_id"] or "",
                                     "location": loc["id"],
                                     "serial": row.get("serial", "")}
            plan["describe"] = f"asset '{name}' at {loc['name']}"
        elif row.get("qty"):
            if loc is None:
                errors.append({"row": n,
                               "error": "balance rows need a location"})
                continue
            try:
                q = Decimal(row["qty"])
                require(q > 0, "x", "x")
            except Exception:
                errors.append({"row": n,
                               "error": f"qty '{row.get('qty')}' invalid"})
                continue
            unit = (row.get("base_unit")
                    or (existing or {}).get("base_unit") or "each").lower()
            plan["load_line"] = {"item_id": plan["item_id"] or "",
                                 "qty": row["qty"], "unit": unit}
            plan["location_id"] = loc["id"]
            plan["describe"] = (f"load {row['qty']} {unit} '{name}' into "
                                f"{loc['name']}")
        else:
            plan["describe"] = f"catalog only: '{name}'"
        plans.append(plan)
    return rows, errors, plans


# ------------------------------------------------------------------ export

def export_csv(kind: str) -> str:
    c, l, g = _docs()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    if kind == "balances":
        w.writerow(["item", "tracking", "location", "qty", "base_unit"])
        for item_id, row in sorted(g["balances"].items()):
            name = _safe_name(c, item_id)
            for loc_id, qty in sorted(row.items()):
                w.writerow([name, "quantity", locs.path_name(l, loc_id),
                            qty, cat.get_item(c, item_id,
                                              allow_archived=True)
                            .get("base_unit", "")])
        for a in g["assets"].values():
            w.writerow([_safe_name(c, a["item_id"]), "asset",
                        locs.path_name(l, a.get("location", "")),
                        a["id"], a.get("condition", "")])
        for k in g["kits"].values():
            if not k.get("dissolved"):
                w.writerow([k.get("name"), "kit",
                            locs.path_name(l, k.get("location", "")),
                            k["id"], k.get("condition", "")])
    elif kind == "history":
        w.writerow(["txn_no", "type", "status", "actor", "ts", "src",
                    "dst", "item", "qty", "unit", "asset/kit", "reason"])
        for e in g["events"]:
            for ln in e["lines"]:
                w.writerow([e["txn_no"], e["type"], e["status"],
                            e["actor"], e["ts"],
                            locs.path_name(l, e.get("src", "")),
                            locs.path_name(l, e.get("dst", "")),
                            ln.get("item_name", ""),
                            ln.get("entered_qty", ""),
                            ln.get("entered_unit", ""),
                            ln.get("asset_id") or ln.get("kit_id") or "",
                            e.get("reason", "")])
    elif kind == "low_stock":
        w.writerow(["item", "location", "min_qty", "on_hand"])
        for h in cat.low_stock_hits(c, g["balances"]):
            w.writerow([h["name"], locs.path_name(l, h["location_id"]),
                        h["min_qty"], h["on_hand"]])
    else:
        raise InventoryError("INVALID_INPUT",
                             f"Unknown export '{kind}'.", field="kind")
    return buf.getvalue()


# ------------------------------------------------------------------ labels

def labels_payload(*, location_ids: list[str] | None = None,
                   asset_ids: list[str] | None = None,
                   item_ids: list[str] | None = None) -> dict:
    """Rows for the QR label sheet (templates/inventory_labels.html.j2)."""
    c, l, g = _docs()
    rows = []
    for lid in (location_ids or []):
        loc = locs.get_location(l, lid, allow_inactive=True)
        rows.append({"code": loc["scan_token"], "title": loc["name"],
                     "sub": locs.path_name(l, lid), "kind": "LOCATION"})
    for aid in (asset_ids or []):
        a = led.get_asset(g, aid)
        rows.append({"code": a["scan_token"], "title": aid,
                     "sub": _safe_name(c, a["item_id"]), "kind": "ASSET"})
    for iid in (item_ids or []):
        item = cat.get_item(c, iid, allow_archived=True)
        rows.append({"code": item["scan_token"], "title": item["name"],
                     "sub": item.get("category", ""), "kind": "ITEM"})
    require(bool(rows), "INVALID_INPUT", "Nothing selected for labels.",
            field="location_ids")
    return {"ok": True, "labels": rows}


# ---------------------------------------------------------------- ops tool

def reconcile() -> dict:
    """Diff the stored balance projection against a pure event replay —
    the OPERATIONS.md consistency check. Read-only."""
    g, _ = store.read_doc(store.LEDGER)
    g = led.ensure_shape(g)
    rebuilt = led.rebuild_balances(g)
    diffs = []
    keys = set(rebuilt) | set(g["balances"])
    for item_id in sorted(keys):
        a = g["balances"].get(item_id) or {}
        b = rebuilt.get(item_id) or {}
        for loc in sorted(set(a) | set(b)):
            if a.get(loc) != b.get(loc):
                diffs.append({"item_id": item_id, "location_id": loc,
                              "stored": a.get(loc, "0"),
                              "rebuilt": b.get(loc, "0")})
    return {"ok": True, "consistent": not diffs, "diffs": diffs,
            "events": len(g["events"])}
