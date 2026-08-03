"""
Job Start flow — the Sales → Operations handoff.
=========================================================================
Jake's ask; Jordan's calls (2026-07-29; docs/portal-job-start-design.md):
portal-hosted, pre-filled from the data we already hold, no handwriting
anywhere, output as a PDF and a Drive link.

TWO PARTIES, ONE GATE. Sales fills a packet that is prefilled from the Bid
Board and sends it; Operations accepts it or sends it back naming what's
missing. Monday items are created ONLY in accept() — that is what makes "a job
belongs to Sales until Operations accepts it" true in the system rather than
only on a wall card. Field completeness gates the SEND; acceptance gates the JOB.

This is the ONLY sanctioned path a won bid becomes an operations job. The packet
defined by shared/boards.JOBSTART_FIELDS is the handoff contract, and it is
config so it can be tuned without a deploy.

(The legacy Bid Board automation still creates a Projects item on Accepted —
every write is adopt-or-create so that races into an update, never a duplicate.
See the design doc's ⚠ follow-up.)

Guardrails (enforced HERE, not trusted to the UI):
- Completeness is re-checked server-side on send AND again on accept — a packet
  can't be emptied out between the two.
- The sender cannot accept their own packet (admins excepted, and logged).
- Only fields in JOBSTART_FIELDS are ever written, and only to the (board,
  column) targets that field declares. Hard-excluded ids/types are re-checked
  on every submit, so a config edit can't open a path to contract/money columns.
- Status labels are validated against the board's live label set.
- People columns are never written (they need Monday user ids, and ops picks
  its own owner).
- Never creates a Customers record — an unlinked bid is a warning, not an
  invented customer.
"""
from __future__ import annotations

import re
import sys
from datetime import date as _date, datetime, timezone
from typing import Any, Optional

from shared import activity
from shared import boards
from subsystems.jobstart import ingest

# Longest value the packet may write to a text/long_text column.
MAX_TEXT_LEN = 4000


# ---------------------------------------------------------------------------
# Pure config + validation (unit-tested without Monday)
# ---------------------------------------------------------------------------

def packet_fields() -> list[dict]:
    """
    The EFFECTIVE packet spec: JOBSTART_FIELDS minus anything whose render type
    the form can't draw or whose target is hard-excluded. This is the gate every
    config edit passes through — a contract-value column added to the config
    never reaches the form, the validator, or Monday.

    Read via the module attribute (not a from-import) so the gate always sees
    the CURRENT config, including edits made after import.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for entry in boards.JOBSTART_FIELDS:
        key = str(entry.get("key") or "").strip()
        rtype = str(entry.get("type") or "").strip()
        if not key or key in seen:
            continue
        if rtype not in boards.JOBSTART_RENDER_TYPES:
            continue
        # An empty `targets` is legal and means PACKET-ONLY: the field appears
        # on the form and the PDF but has no Monday column (e.g. the date the
        # scope went to the GC — real information, no board column worth
        # inventing to hold it).
        targets = tuple(entry.get("targets") or ())
        if any(col in boards.JOBSTART_HARD_EXCLUDED_IDS for _, col in targets):
            continue
        seen.add(key)
        out.append({
            "key": key,
            "label": entry.get("label") or key,
            "type": rtype,
            "targets": targets,
            "required": bool(entry.get("required")),
            "help": entry.get("help") or "",
            "prefill": entry.get("prefill"),
        })
    return out


def required_keys() -> list[str]:
    """The gate's field keys, in display order."""
    return [f["key"] for f in packet_fields() if f["required"]]


def missing_required(values: dict) -> list[dict]:
    """
    PURE. THE GATE. Returns the required fields that are still empty, as
    [{key, label}, ...] in display order. Empty list ⇒ the packet may hand off.
    A value of whitespace only counts as missing — a space bar is not an answer.
    """
    out = []
    for field in packet_fields():
        if not field["required"]:
            continue
        raw = (values or {}).get(field["key"])
        if raw is None or not str(raw).strip():
            out.append({"key": field["key"], "label": field["label"]})
    return out


def shape_value(render_type: str, raw: Any) -> Any:
    """
    PURE. One UI value → the Monday API column value. Empty means "leave the
    column alone" and is signalled by returning None (the caller drops it) —
    a handoff never clears a column someone already filled.
    Raises ValueError with a human-readable message when the value can't shape.
    """
    is_empty = raw is None or (isinstance(raw, str) and not raw.strip())
    if is_empty:
        return None

    if render_type == "status":
        if not isinstance(raw, str):
            raise ValueError("Status value must be a label string.")
        return {"label": raw.strip()}

    if render_type == "date":
        if not isinstance(raw, str):
            raise ValueError("Date must be a YYYY-MM-DD string.")
        text = raw.strip()
        try:
            return {"date": _date.fromisoformat(text).isoformat()}
        except ValueError:
            pass
        # Dates reach the packet from Monday board updates, where a human typed
        # them — "8/15/2026", not an ISO string. Failing those meant a value we
        # could plainly read blocked the whole handoff. US month-first order is
        # assumed deliberately: GVC works Ohio/Indiana/Kentucky.
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m.%d.%Y"):
            try:
                return {"date": datetime.strptime(text, fmt).date().isoformat()}
            except ValueError:
                continue
        raise ValueError(f"Not a valid date (use YYYY-MM-DD): {text!r}")

    if render_type == "number":
        try:
            num = float(str(raw).strip().replace(",", "").replace("$", ""))
        except ValueError:
            raise ValueError(f"Not a number: {raw!r}")
        return str(int(num)) if num == int(num) else str(num)

    if render_type == "link":
        text = str(raw).strip()
        # People label their links — Jordan's real packet carried
        # "Stocking - https://docs.google.com/…". Requiring the value to START
        # with http rejected that outright and 422'd the whole accept, losing a
        # perfectly good URL over a prefix. Find the URL inside the text and
        # keep the label as the link's display text.
        m = re.search(r"https?://\S+", text)
        if not m:
            raise ValueError("No web link found — paste a URL starting with "
                             "http:// or https://")
        url = m.group(0).rstrip(").,;")
        if len(url) > MAX_TEXT_LEN:
            raise ValueError("Link is too long.")
        label = text[:m.start()].strip(" -–—:|") or "Take-off"
        return {"url": url, "text": label[:80]}

    if render_type in ("text", "long_text"):
        text = str(raw).strip()
        if len(text) > MAX_TEXT_LEN:
            raise ValueError(f"Text too long ({len(text)} chars; "
                             f"max {MAX_TEXT_LEN}).")
        return {"text": text} if render_type == "long_text" else text

    raise ValueError(f"Unsupported field type: {render_type!r}")


def build_writes(values: dict, *,
                 status_labels: Optional[dict[str, list[str]]] = None,
                 ) -> tuple[dict[str, dict], dict[str, str], dict[str, dict]]:
    """
    PURE. Packet {key: raw} → ({board: {col_id: api_value}}, errors, accepted).

    One field may target BOTH boards (Scaffold genuinely lives on each); the
    same shaped value is written to every target it declares. Unknown keys are
    rejected rather than ignored, so a stale client can't quietly drop data it
    thinks it saved.
    """
    spec = {f["key"]: f for f in packet_fields()}
    writes: dict[str, dict] = {"projects": {}, "operations": {}}
    errors: dict[str, str] = {}
    accepted: dict[str, dict] = {}

    for key, raw in (values or {}).items():
        key = str(key).strip()
        field = spec.get(key)
        if field is None:
            errors[key] = "Not a Job Start packet field."
            continue
        try:
            api_value = shape_value(field["type"], raw)
        except ValueError as e:
            errors[key] = str(e)
            continue
        if api_value is None:
            continue                       # empty ⇒ leave the column alone
        if (field["type"] == "status" and isinstance(api_value, dict)
                and status_labels is not None):
            known = status_labels.get(key) or []
            if known and api_value["label"] not in known:
                errors[key] = (f"'{api_value['label']}' is not a label on this "
                               f"board column.")
                continue
        for board_name, col_id in field["targets"]:
            if col_id in boards.JOBSTART_HARD_EXCLUDED_IDS:
                continue                   # belt-and-braces; packet_fields filtered
            writes.setdefault(board_name, {})[col_id] = api_value
        accepted[key] = field

    return writes, errors, accepted


def writability_errors(values: dict, *,
                       status_labels: Optional[dict[str, list[str]]] = None,
                       ) -> dict[str, str]:
    """
    PURE. Which saved packet values CANNOT be written to Monday, and why.
    {key: human-readable reason}.

    Called at two different moments with two different strictnesses, on purpose:

      • autosave / fill time — `status_labels=None`, so no Monday round-trip.
        Catches the shape faults (a board count of "340 sheets", a malformed
        date, a take-off "link" that isn't a URL).
      • send time — with the LIVE label sets, which also catches a status value
        that no longer exists on the target board.

    Why it matters: before this existed the only check ran inside accept(), so
    an unwritable value was discovered by OPERATIONS, on a packet that is
    read-only by design, with an error telling them to fix fields they cannot
    edit. Validation has to happen while the person who typed the value still
    has the form open.
    """
    _writes, errors, _accepted = build_writes(values,
                                              status_labels=status_labels)
    return errors


def label_for(key: str) -> str:
    """PURE. A field's human label, falling back to the raw key."""
    for field in packet_fields():
        if field["key"] == key:
            return field.get("label") or key
    return key


def _unwritable_detail(errors: dict[str, str]) -> str:
    """PURE. One sentence naming the offending fields by their form labels."""
    rows = field_error_list(errors)
    named = "; ".join(f"{r['label']} — {r['reason']}" for r in rows)
    noun = "field" if len(rows) == 1 else "fields"
    return (f"{len(rows)} {noun} can't be saved to Monday yet: {named} "
            f"Fix them and send again.")


def field_error_list(errors: dict[str, str]) -> list[dict]:
    """PURE. {key: reason} → [{key, label, reason}] for the UI and the log."""
    return [{"key": k, "label": label_for(k), "reason": v}
            for k, v in sorted((errors or {}).items())]


def describe_packet(values: dict, accepted: dict[str, dict]) -> str:
    """PURE. Flat 'Label: value; …' for the activity log (scalars only)."""
    parts = []
    for key, field in accepted.items():
        raw = str((values or {}).get(key) or "").strip()
        if len(raw) > 60:
            raw = raw[:57] + "…"
        parts.append(f"{field.get('label', key)}: {raw or '(empty)'}")
    return "; ".join(parts)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# Automatic ingest sources. BOTH are best-effort by contract: the handoff page
# must open even when Drive or Monday is unreachable — just with less prefilled.
# A failure here is a smaller problem than a page that won't load.
# ---------------------------------------------------------------------------

def _scope_review_values(bid: dict) -> tuple[dict, dict]:
    """
    Find and parse this job's scope review. Returns (values, info) where `info`
    tells the UI what was found — Jake needs to see WHICH document a prefilled
    value came from, or he'll retype it anyway.
    """
    from subsystems.jobstart import scope_review

    hint = bid.get("name") or ""
    extra = [(bid.get("context") or {}).get("customer"),
             (bid.get("context") or {}).get("location")]
    try:
        from adapters.drive import DriveUploader

        uploader = DriveUploader()
        docs = uploader.find_job_documents(job_hint=hint, extra_hints=extra)
        found = docs.get("scope_review")
        if not found:
            return {}, {"found": False,
                        "folder": (docs.get("folder") or {}).get("name"),
                        "detail": docs.get("detail")
                                  or "No scope review matched this job in Drive."}
        text = uploader.read_document_text(found["id"], found.get("mimeType") or "")
        parsed = scope_review.parse(text)
        if not parsed.get("found"):
            return {}, {"found": False, "name": found.get("name"),
                        "url": found.get("webViewLink"),
                        "detail": "Found the document but couldn't read its "
                                  "sections — prefilling from Monday instead."}
        values = ingest.from_scope_review(parsed)
        # The takeoff sits in the same job folder — link it rather than making
        # Jake paste a URL he already has on disk.
        takeoff = docs.get("takeoff") or {}
        if takeoff.get("webViewLink") and not values.get("takeoff_link"):
            values["takeoff_link"] = takeoff["webViewLink"]
        return values, {
            "found": True,
            "name": found.get("name"),
            "url": found.get("webViewLink"),
            "folder": (docs.get("folder") or {}).get("name"),
            "takeoff": takeoff.get("name"),
            "trades": parsed.get("trades") or [],
            "questions": len(parsed.get("clarifications") or []),
            "exclusions": len(parsed.get("exclusion_lines") or []),
        }
    except Exception as e:  # noqa: BLE001 — never block the page on Drive
        print(f"[jobstart] scope review ingest failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return {}, {"found": False,
                    "detail": f"Couldn't reach Drive ({type(e).__name__})."}


def _update_values(bid: dict) -> dict:
    """
    Pull Project- and Ops-board item updates so the packet reflects what's been
    posted since it was created (Jordan: "it will check the updates there and
    then keep the handoff up to date essentially").
    """
    item_ids = [int(i) for i in
                (bid.get("existing_project_ids") or []) + (bid.get("existing_ops_ids") or [])
                if i]
    if not item_ids:
        return {}
    try:
        from adapters.monday.client import MondayClient
        from adapters.monday import jobstart as mj

        texts = mj.fetch_item_updates(MondayClient(), item_ids)
        return ingest.from_updates(texts)
    except Exception as e:  # noqa: BLE001 — never block the page on Monday
        print(f"[jobstart] board-update ingest failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Flows (Monday I/O via adapters/monday/jobstart.py)
# ---------------------------------------------------------------------------

def picker_rank(bid: dict, *, statuses) -> int:
    """
    PURE. Where a bid sits in the work list. Lower sorts first.

      0  with ops      — somebody is waiting on an acceptance; this blocks jobs
      1  sent back     — Sales has to fix something
      2  in progress   — a half-filled packet, nobody blocked
      3  won, no packet— accepted bid whose handoff hasn't started
      4  open bid      — not accepted yet; Sales can accept it in here
      5  handed off    — done; sinks to the bottom

    Open bids sort BELOW work in flight but are never hidden behind a filter
    toggle — a filter Jake has to find is a filter Jake won't find.
    """
    status = bid.get("packet_status")
    if status == statuses.STATUS_ACCEPTED:
        return 5
    if status == statuses.STATUS_WITH_OPS:
        return 0
    if status == statuses.STATUS_SENT_BACK:
        return 1
    if status == statuses.STATUS_DRAFT or bid.get("draft_filled"):
        return 2
    return 3 if bid.get("stage_state") == "accepted" else 4


def list_open_handoffs() -> dict:
    """
    Picker payload: every live bid — open AND accepted — each flagged with its
    stage, whether a Projects/Operations item already exists, and whether a
    packet draft is in progress. Read-only.
    """
    from adapters.monday.client import MondayClient
    from adapters.monday import jobstart as mj
    from subsystems.jobstart import drafts

    bids = mj.fetch_bids(MondayClient())

    drafted: dict[int, dict] = {}
    try:
        for row in drafts.list_drafts():
            if row.get("bid_id"):
                drafted[int(row["bid_id"])] = row
    except Exception as e:  # noqa: BLE001 — a draft-store problem must not
        # hide the work list; the picker degrades to "no drafts known".
        print(f"[jobstart] draft list unavailable: {type(e).__name__}: {e}",
              file=sys.stderr)

    for bid in bids:
        row = drafted.get(bid["item_id"])
        bid["draft_filled"] = row.get("filled") if row else None
        bid["draft_updated_at"] = row.get("updated_at") if row else None
        bid["packet_status"] = row.get("status") if row else None
        bid["handed_off"] = (bid["packet_status"] == drafts.STATUS_ACCEPTED)

    # Packets waiting on Operations float to the top — that's the queue that
    # actually blocks jobs. Open bids sit below the work in flight; handed-off
    # ones sink.
    for bid in bids:
        bid["rank"] = picker_rank(bid, statuses=drafts)
    bids.sort(key=lambda b: (b["rank"], b["name"].lower()))

    counts = {
        "with_ops": sum(1 for b in bids if b["rank"] == 0),
        "sent_back": sum(1 for b in bids if b["rank"] == 1),
        "in_progress": sum(1 for b in bids if b["rank"] == 2),
        "accepted_no_packet": sum(1 for b in bids if b["rank"] == 3),
        "open": sum(1 for b in bids if b["rank"] == 4),
        "handed_off": sum(1 for b in bids if b["rank"] == 5),
    }
    return {"ok": True, "count": len(bids), "bids": bids, "counts": counts,
            "accepted_stage": boards.JOBSTART_ACCEPTED_STAGE,
            "required_keys": required_keys()}


def get_handoff_detail(bid_id: int, actor: str = "") -> Optional[dict]:
    """
    Everything the page needs for one bid: read-only context, the packet spec
    with live status labels, prefilled values (saved packet wins over bid
    prefill), and the handoff state — including whether THIS person may accept
    it. None when the bid doesn't exist.
    """
    from adapters.monday.client import MondayClient
    from adapters.monday import jobstart as mj
    from subsystems.jobstart import drafts

    mc = MondayClient()
    bid = mj.get_bid_detail(mc, int(bid_id))
    if bid is None:
        return None

    try:
        labels = mj.get_field_labels(mc)
    except Exception as e:  # noqa: BLE001 — chips degrade to a text input
        print(f"[jobstart] label fetch failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        labels = {}

    saved = None
    try:
        saved = drafts.get_draft(bid_id)
    except Exception as e:  # noqa: BLE001 — a missing draft store must not
        # block the handoff; the form just starts from the bid prefill.
        print(f"[jobstart] draft read failed: {type(e).__name__}: {e}",
              file=sys.stderr)

    # ---- Ingest, in Jordan's stated precedence (subsystems/jobstart/ingest) --
    # packet (typed) > scope review (Drive) > board updates > Bid Board columns.
    # Every automatic source is best-effort: the page must still open when Drive
    # or Monday is unreachable, just with less prefilled.
    scope_values, scope_info = _scope_review_values(bid)
    update_values = _update_values(bid)

    values, sources = ingest.merge(
        packet={k: v for k, v in (saved or {}).get("values", {}).items()
                if str(v or "").strip()},
        scope_review=scope_values,
        updates=update_values,
        bid=bid.get("prefill") or {},
    )

    fields = []
    for field in packet_fields():
        fields.append({
            "key": field["key"], "label": field["label"],
            "type": field["type"], "required": field["required"],
            "help": field["help"], "labels": labels.get(field["key"]) or [],
        })

    # ---- Job name in Jake's pipe standard (Jordan, 2026-07-29: "We prefer the
    # -Pipe- | it looks so much better"). A name already saved on the packet wins
    # — it's what a human decided. Otherwise suggest the standard form, and if a
    # piece is missing say so rather than guessing (Jake's own rule).
    from subsystems.jobstart import naming as _naming

    saved_name = (saved or {}).get("job_name")
    std = _naming.to_standard(
        bid["name"],
        customer_hint=(bid.get("context") or {}).get("customer"))
    if saved_name:
        suggested_name = saved_name
        naming_info = {"standard": _naming.is_standard(saved_name),
                       "suggestion": std["name"], "note": std["note"]}
    else:
        suggested_name = std["name"] or bid["name"]
        naming_info = {"standard": std["ok"], "suggestion": std["name"],
                       "note": std["note"], "original": bid["name"]}

    missing = missing_required(values)
    status = (saved or {}).get("status") or drafts.STATUS_DRAFT
    sent_by = (saved or {}).get("sent_by")

    # Two-party rule: the person who sent a packet can't also accept it.
    # Admins are exempt so a one-person day (Jordan doing both roles) is never
    # hard-blocked — the activity log still records who did what.
    from shared import access
    is_admin = access.has_feature(actor, "admin") if actor else False
    can_accept = (status == drafts.STATUS_WITH_OPS
                  and (is_admin or not actor or actor != sent_by))

    return {
        "ok": True,
        "bid": {
            "item_id": bid["item_id"], "name": bid["name"], "url": bid["url"],
            "group": bid.get("group_title"),
            "stage": bid.get("stage"),
            "stage_state": bid.get("stage_state"),
            **(bid.get("context") or {}),
        },
        # The form says so on the button: sending an OPEN bid also marks it
        # Accepted. Stated up front rather than discovered afterwards on a board
        # other people watch.
        "will_mark_accepted": bid.get("stage_state") != "accepted",
        "accepted_stage": boards.JOBSTART_ACCEPTED_STAGE,
        "job_name": suggested_name,
        "naming": naming_info,
        "fields": fields,
        "values": values,
        "missing": missing,
        "sources": sources,
        "scope_review": scope_info,
        "status": status,
        "editable": status in drafts.EDITABLE_STATUSES,
        "can_send": not missing and status in drafts.EDITABLE_STATUSES,
        "can_accept": can_accept,
        "self_sent": bool(actor and actor == sent_by),
        "sent_by": sent_by,
        "sent_at": (saved or {}).get("sent_at"),
        "accepted_by": (saved or {}).get("accepted_by"),
        "accepted_at": (saved or {}).get("accepted_at"),
        "sent_back_note": (saved or {}).get("sent_back_note"),
        "packet_url": (saved or {}).get("packet_url"),
        "preview_url": (saved or {}).get("preview_url"),
        "already": {
            "project": bool(bid.get("existing_project_ids")),
            "ops": bool(bid.get("existing_ops_ids")),
        },
        "customer_linked": bool((bid.get("copy") or {}).get("customer_ids")),
        "draft_updated_at": (saved or {}).get("updated_at"),
        "draft_updated_by": (saved or {}).get("updated_by"),
    }


def save_packet_draft(bid_id: int, values: dict, actor: str, *,
                      job_name: Optional[str] = None,
                      label: Optional[str] = None,
                      updated_at: Optional[str] = None) -> dict:
    """
    Autosave. Never gated — a partial packet is exactly what this is for.
    Returns the gate state alongside, so the form can update its counter from
    the SERVER's opinion rather than its own.
    """
    from subsystems.jobstart import drafts

    record, stale = drafts.save_draft(
        bid_id, values=values, label=label, job_name=job_name,
        updated_at=updated_at, actor=actor)
    missing = missing_required(values)
    # Shape-only (no Monday call — autosave must stay cheap and must NEVER
    # refuse to save). Returned as warnings so the form can flag the field
    # while it's still on screen; the send gate is where they become blocking.
    warnings = field_error_list(writability_errors(values))
    return {"ok": True, "stale": stale, "saved_at": record.get("updated_at"),
            "missing": missing, "can_hand_off": not missing,
            "field_warnings": warnings}


def require_complete(values: dict) -> list[dict]:
    """
    THE ENFORCEMENT. Raises nothing — returns the missing list so the route can
    map it to a 422. Separated from `missing_required` to make the call site
    read as the gate it is, and so a future soft-gate mode has one place to
    change.
    """
    return missing_required(values)


# ---------------------------------------------------------------------------
# The two-party handoff: Sales sends → Operations accepts.
#
# The REAL gate is acceptance, not field completeness. Field completeness only
# gates the SEND — it stops Sales handing over a half-packet. Monday items are
# created exclusively in accept(), which is what makes "a job belongs to Sales
# until Operations accepts it" true in the system rather than only on a wall.
# ---------------------------------------------------------------------------

def _render_packet(bid: dict, record: dict, values: dict, job_name: str,
                   *, accepted: bool):
    """Render the packet PDF to a temp path. Returns (path, context)."""
    import tempfile
    from pathlib import Path
    from subsystems.jobstart import packet

    context = packet.build_context(
        job_name=job_name, values=values,
        bid_context=bid.get("context") or {}, record=record,
        bid_url=bid.get("url"),
        drive_folder_path=record.get("drive_folder_path"),
    )
    out = (Path(tempfile.gettempdir())
           / packet.packet_filename(job_name, accepted=accepted))
    packet.render_packet_pdf(context, out)
    return out, context


def send_to_ops(bid_id: int, values: dict, actor: str, *,
                job_name: Optional[str] = None) -> dict:
    """
    Sales hands the packet to Operations.

    Gate: every required field must be present — an incomplete packet is
    refused and nothing changes state. On success the packet PDF is rendered
    and (best-effort) uploaded for preview, the record moves to `with_ops`,
    and ops is pinged in Slack. NO Monday items are created here.
    """
    from adapters.monday.client import MondayClient
    from adapters.monday import jobstart as mj
    from subsystems.jobstart import drafts

    bid_id = int(bid_id)
    missing = require_complete(values)
    if missing:
        activity.log_event("jobstart.send_blocked", actor=actor,
                           target=str(bid_id), result="blocked", severity="INFO",
                           missing=",".join(m["key"] for m in missing))
        return {"ok": False, "blocked": True, "missing": missing,
                "detail": "Packet is incomplete — not sent."}

    mc = MondayClient()
    bid = mj.get_bid_detail(mc, bid_id)
    if bid is None:
        return {"ok": False, "missing": [],
                "detail": f"Bid {bid_id} not found on the Bid Board."}

    # SECOND GATE — writability. Completeness only proves a field isn't empty;
    # this proves the value can actually land in its Monday column. Checked HERE
    # rather than in accept() because this is the last moment the packet is
    # editable and the person who typed the value is the one being told.
    try:
        live_labels = {k: [l["label"] for l in v]
                       for k, v in mj.get_field_labels(mc).items()}
    except Exception as e:  # noqa: BLE001 — degrade to shape-only rather than
        # blocking the send on a Monday hiccup; accept() re-checks anyway.
        print(f"[jobstart] label fetch failed on send: {type(e).__name__}: {e}",
              file=sys.stderr)
        live_labels = None

    bad = writability_errors(values, status_labels=live_labels)
    if bad:
        activity.log_event("jobstart.send_blocked", actor=actor,
                           target=str(bid_id), result="blocked",
                           severity="WARNING",
                           unwritable=",".join(sorted(bad)))
        # No "blocked" key on purpose: the route maps `blocked` to
        # PACKET_INCOMPLETE (a missing-fields count) and `field_errors` to
        # PACKET_INVALID, which is the accurate code here.
        return {"ok": False, "missing": [], "field_errors": bad,
                "detail": _unwritable_detail(bad)}

    name = (job_name or bid["name"] or "").strip() or bid["name"]
    # Persist the packet first so the render and the record can't disagree.
    drafts.save_draft(bid_id, values=values, label=bid["name"], job_name=name,
                      actor=actor)
    record = drafts.set_status(bid_id, status=drafts.STATUS_WITH_OPS,
                               actor=actor)

    preview_url = None
    try:
        pdf_path, _ = _render_packet(bid, record, values, name, accepted=False)
        from adapters.gcs import upload_preview_pdf, utc_timestamp
        preview_url = upload_preview_pdf(
            pdf_path, identifier=f"handoff-{bid_id}",
            run_timestamp=utc_timestamp())
        record = drafts.set_status(bid_id, status=drafts.STATUS_WITH_OPS,
                                   actor=actor,
                                   extra={"preview_url": preview_url})
    except Exception as e:  # noqa: BLE001 — ops can still review in the portal
        print(f"[jobstart] packet preview unavailable: {type(e).__name__}: {e}",
              file=sys.stderr)

    # ---- Mark the bid won ---------------------------------------------------
    # Sending a job-start packet IS the statement that the bid is won, so the
    # Bid Board stage is written here rather than left as a separate errand in
    # Monday — one less tap, one less thing to forget (Jordan's ask, 2026-07-29).
    # It is NOT silent: it's reported in the result, named on the button, and in
    # the Slack notice. Verified 2026-07-30 that no ACTIVE automation triggers on
    # Stage → Accepted, which is what made an implicit write safe — the estimate
    # flow deliberately does NOT auto-advance stage because things DO key off
    # "Sent to Client".
    stage_report = mj.mark_bid_accepted(
        mc, bid_id, current_stage=bid.get("stage"),
        current_group=bid.get("group_id"))
    for err in stage_report.get("errors") or []:
        print(f"[jobstart] bid stage/group write failed: {err}", file=sys.stderr)

    activity.log_event("jobstart.sent_to_ops", actor=actor, target=str(bid_id),
                       result="ok", severity="INFO", job=name,
                       bid_stage_written=stage_report.get("stage_written"),
                       bid_group_moved=stage_report.get("group_moved"),
                       bid_stage_error=("; ".join(stage_report.get("errors") or [])
                                        or None))

    slack_status = _notify(lambda sn: sn.notify_job_start_sent({
        "job": name, "actor": actor, "bid_url": bid.get("url"),
        "preview_url": preview_url,
        "start_date": values.get("start_date"),
        "supervisor": values.get("supervisor"),
        "bid_marked_accepted": bool(stage_report.get("stage_written")),
    }))

    return {"ok": True, "blocked": False, "missing": [], "status": record["status"],
            "job_name": name, "preview_url": preview_url, "slack": slack_status,
            "bid_stage": stage_report}


def send_back(bid_id: int, note: str, actor: str) -> dict:
    """
    Operations returns a packet, naming what's missing. Sales can edit again.
    Deliberately lightweight — a note, not a formal rejection document.
    """
    from subsystems.jobstart import drafts

    bid_id = int(bid_id)
    note = (note or "").strip()
    if not note:
        return {"ok": False, "detail": "Say what's missing so Sales can fix it."}

    record = drafts.set_status(bid_id, status=drafts.STATUS_SENT_BACK,
                               actor=actor, note=note)
    activity.log_event("jobstart.sent_back", actor=actor, target=str(bid_id),
                       result="ok", severity="INFO",
                       job=record.get("job_name"), note=note[:200])

    slack_status = _notify(lambda sn: sn.notify_job_start_sent_back({
        "job": record.get("job_name"), "actor": actor, "note": note,
        "sent_by": record.get("sent_by"),
    }))
    return {"ok": True, "status": record["status"], "slack": slack_status}


def accept(bid_id: int, actor: str) -> dict:
    """
    THE handoff. Operations accepts, and only now does the job become real:
    Monday Projects + Operations items are created (adopt-or-create), the bid is
    stamped, the accepted packet PDF is filed into the job's Drive folder, and
    the link goes to Slack.

    Refuses if the packet isn't currently with ops, or if the accepter is the
    same person who sent it (unless they're an admin) — a handoff with one
    signature isn't a handoff.
    """
    from adapters.monday.client import MondayClient
    from adapters.monday import jobstart as mj
    from subsystems.jobstart import drafts, packet as packet_mod
    from shared import access

    bid_id = int(bid_id)
    record = drafts.get_draft(bid_id)
    if record is None:
        return {"ok": False, "detail": "No handoff packet exists for this bid."}
    if record.get("status") != drafts.STATUS_WITH_OPS:
        return {"ok": False,
                "detail": f"This packet is '{record.get('status')}', not waiting "
                          f"on Operations."}
    if (actor and actor == record.get("sent_by")
            and not access.has_feature(actor, "admin")):
        return {"ok": False, "self_accept": True,
                "detail": "You sent this packet — someone in Operations needs "
                          "to accept it."}

    values = record.get("values") or {}
    missing = require_complete(values)
    if missing:
        return {"ok": False, "blocked": True, "missing": missing,
                "detail": "Packet is no longer complete — send it back to Sales."}

    mc = MondayClient()
    bid = mj.get_bid_detail(mc, bid_id)
    if bid is None:
        return {"ok": False, "detail": f"Bid {bid_id} not found on the Bid Board."}

    try:
        status_labels = {k: [l["label"] for l in v]
                         for k, v in mj.get_field_labels(mc).items()}
    except Exception as e:  # noqa: BLE001
        print(f"[jobstart] label fetch failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        status_labels = None

    # A value that can't be written NO LONGER BLOCKS THE HANDOFF. It is dropped,
    # the job is still created, and the field is named so somebody can set it by
    # hand — the same contract as `manual_columns` below. The old behaviour
    # returned field_errors and refused: a dead end, because an in-review packet
    # is read-only and Operations cannot edit Sales' fields. send_to_ops() now
    # catches these while the form is still open, so reaching here means the
    # value was saved before that gate existed, or a board label changed since.
    writes, unwritable, accepted_fields = build_writes(
        values, status_labels=status_labels)

    projects_values = dict(writes.get("projects") or {})
    projects_values[boards.JOBSTART_P_COL_PROJECT_STATUS] = {
        "label": boards.JOBSTART_P_NOT_STARTED_LABEL}
    projects_values[boards.JOBSTART_P_COL_INVOICE_STATUS] = {
        "label": boards.JOBSTART_P_NOT_STARTED_LABEL}

    ops_values = dict(writes.get("operations") or {})
    ops_values[boards.JOBSTART_OPS_COL_STAGE] = {
        "label": boards.JOBSTART_OPS_STAGE_LABEL}
    ops_values[boards.JOBSTART_OPS_COL_BILLABLE] = {
        "label": boards.JOBSTART_OPS_BILLABLE_LABEL}

    name = record.get("job_name") or bid["name"]
    report = mj.hand_off(mc, bid=bid, job_name=name,
                         projects_values=projects_values,
                         ops_values=ops_values, accepted_date=_today())

    # Mark accepted BEFORE filing, so the PDF renders with the acceptance on it
    # and a Drive failure can never leave the job un-accepted in the record.
    record = drafts.set_status(bid_id, status=drafts.STATUS_ACCEPTED,
                               actor=actor)

    warnings: list[str] = []
    if unwritable:
        for row in field_error_list(unwritable):
            warnings.append(
                f"'{row['label']}' was left blank on the job — {row['reason']} "
                f"Set it by hand, or fix the packet and accept again.")
    if not (bid.get("copy") or {}).get("customer_ids"):
        warnings.append("This bid has no Customer linked, so the project was "
                        "created without one.")
    if report.get("ops_error"):
        warnings.append(f"The Operations task did NOT get created "
                        f"({report['ops_error']}). Accept again to retry it.")
    if report.get("bid_stamp_error"):
        warnings.append(f"The Bid Board stamp failed "
                        f"({report['bid_stamp_error']}).")
    if report.get("manual_columns"):
        pretty = {"location5": "Job Location", "connect_boards9": "Customer",
                  "connect_boards5": "Customer"}
        names = ", ".join(sorted({pretty.get(c, c) for c in report["manual_columns"]}))
        warnings.append(
            f"Monday rejected these columns, so the job was created without "
            f"them: {names}. Set them by hand on the project — Monday's API "
            f"blocks writes to them (known limitation, not a portal bug).")

    # ---- File the accepted packet into the job's Drive folder --------------
    packet_url = None
    try:
        pdf_path, _ = _render_packet(bid, record, values, name, accepted=True)
        from adapters.drive import DriveUploader

        uploader = DriveUploader()
        folder = uploader.ensure_handoff_folder(
            customer=(bid.get("context") or {}).get("customer") or "Unknown",
            project_label=name,
            project_type=values.get("project_type") or "residential",
            year=datetime.now(timezone.utc).year,
        )
        uploaded = uploader.upload_or_replace_file(
            folder["folder_id"], pdf_path,
            packet_mod.packet_filename(name, accepted=True))
        packet_url = uploaded.get("web_view_link")
        record = drafts.set_status(
            bid_id, status=drafts.STATUS_ACCEPTED, actor=actor,
            extra={"packet_url": packet_url,
                   "drive_folder_path": folder.get("folder_path")})
    except Exception as e:  # noqa: BLE001 — Drive is graceful by contract
        warnings.append(f"The packet PDF wasn't filed to Drive "
                        f"({type(e).__name__}). The job is still accepted.")
        print(f"[jobstart] Drive filing failed: {type(e).__name__}: {e}",
              file=sys.stderr)

    ok = bool(report.get("project_id")) and not report.get("ops_error")
    activity.log_event(
        "jobstart.accepted", actor=actor, target=str(bid_id),
        result="ok" if ok else "partial",
        severity="INFO" if ok else "WARNING", job=name,
        sent_by=record.get("sent_by"),
        project_id=str(report.get("project_id") or ""),
        ops_id=str(report.get("ops_id") or ""),
        packet=describe_packet(values, accepted_fields),
        failed=report.get("ops_error") or None,
        unwritable=",".join(sorted(unwritable)) or None,
    )

    slack_status = _notify(lambda sn: sn.notify_job_start_handoff({
        "job": name, "actor": actor, "bid_url": bid.get("url"),
        "project_url": report.get("project_url"),
        "ops_url": report.get("ops_url"),
        "packet_url": packet_url,
        "sent_by": record.get("sent_by"),
        "estimate_number": (bid.get("context") or {}).get("estimate_number"),
        "estimate_total": (bid.get("context") or {}).get("estimate_total"),
        "start_date": values.get("start_date"),
        "supervisor": values.get("supervisor"),
        "warnings": warnings,
    }))

    # NOTE: deliberately NOT keyed "field_errors" — that key makes the route
    # raise 422 PACKET_INVALID, which is exactly the dead end this replaces.
    return {"ok": ok, "status": record["status"], "job_name": name,
            "packet_url": packet_url, "warnings": warnings,
            "unwritable_fields": field_error_list(unwritable),
            "slack": slack_status, **report}


def email_scope_to_gc(bid_id: int, actor: str) -> dict:
    """
    Draft the GC scope-confirmation email — the outbound step that reconciles our
    scope against the GC's in writing BEFORE we mobilize.

    DRAFT ONLY. Per the locked architecture (AGENTS.md #2) nothing is auto-sent
    to a customer: this lands in hello@ Drafts and a human clicks send. Re-running
    updates the same draft in place rather than stacking duplicates.

    Does NOT stamp `gc_confirmed_on` — that used to happen at draft time and
    lied on the PDF while the draft sat unsent. The sent-watcher stamps the
    real send date when the message appears in Gmail `in:sent`. Returns the
    Gmail draft URL so the sender can go straight to it.
    """
    from adapters.monday.client import MondayClient
    from adapters.monday import jobstart as mj
    from subsystems.jobstart import drafts, gc_confirm

    bid_id = int(bid_id)
    record = drafts.get_draft(bid_id)
    if record is None:
        return {"ok": False, "detail": "Fill in the packet first."}

    values = record.get("values") or {}
    to, cc = gc_confirm.recipients(values)
    if not to:
        return {"ok": False, "code": "NO_GC_EMAIL",
                "detail": "No GC PM email on the packet.",
                "advice": "Add the GC PM email, then draft the confirmation."}
    if not (values.get("scope") or "").strip():
        return {"ok": False, "detail": "The packet has no scope to confirm yet."}

    bid = mj.get_bid_detail(MondayClient(), bid_id)
    ctx = (bid or {}).get("context") or {}
    name = record.get("job_name") or (bid or {}).get("name") or "this job"

    from adapters import gmail
    result = gmail.create_draft(
        to=to, cc=cc,
        subject=gc_confirm.subject(name, estimate_number=ctx.get("estimate_number")),
        body=gc_confirm.body(values, job_name=name,
                             company_contact=_person_name(actor),
                             estimate_number=ctx.get("estimate_number")),
        invoice_identifier=gc_confirm.draft_identifier(bid_id),
    )

    # gc_confirmed_on is DELIBERATELY NOT stamped here. It used to be — which
    # made the packet claim the scope was emailed to the GC when in fact a
    # draft was sitting unsent in hello@. The sent-watcher (check_sent's GC
    # sweep) stamps it when the message actually appears in in:sent — the same
    # truthful-signal pattern as invoice/estimate "Emailed on". Only
    # gc_drafted_at is recorded, which is the fact that is actually true now.
    # A human typing the date by hand still wins: the watcher fills only an
    # empty value (ingest precedence — typed beats automatic).
    record = drafts.set_status(
        bid_id, status=record.get("status") or drafts.STATUS_DRAFT, actor=actor,
        extra={"gc_draft_url": result.get("gmail_url"),
               "gc_drafted_at": _today(),
               # Persisted so the watcher can search Gmail for the exact
               # subject it drafted, even if the job is renamed afterwards.
               "gc_subject": gc_confirm.subject(
                   name, estimate_number=ctx.get("estimate_number"))})

    activity.log_event("jobstart.gc_confirmation_drafted", actor=actor,
                       target=str(bid_id), result="ok", severity="INFO",
                       job=name, to=to, cc=cc or None,
                       replaced=str(result.get("replaced_existing")))

    return {"ok": True, "to": to, "cc": cc,
            "gmail_url": result.get("gmail_url"),
            "replaced_existing": bool(result.get("replaced_existing")),
            "drafted_on": _today()}


def gc_pending_confirmations() -> list[dict]:
    """
    Work list for the sent-watcher: packets whose GC scope confirmation was
    DRAFTED but not yet confirmed sent. Each row carries the exact Gmail
    subject the draft was composed with, so the watcher searches for what was
    actually written, not a reconstruction.
    """
    from subsystems.jobstart import drafts

    out = []
    for row in drafts.list_drafts():
        if row.get("gc_drafted_at") and not row.get("gc_confirmed_on"):
            out.append({
                "bid_id": row["bid_id"],
                "job_name": row.get("job_name") or row.get("label"),
                "gc_subject": row.get("gc_subject"),
                "gc_drafted_at": row.get("gc_drafted_at"),
            })
    return out


def stamp_gc_confirmed(bid_id: int, date_str: str) -> bool:
    """
    The sent-watcher found the GC email in in:sent — record the REAL send date.

    Fill-if-empty, always (ingest precedence: a date a human typed is never
    overwritten by an automatic source). Returns False when there was nothing
    to do. Written into the packet values because gc_confirmed_on is a packet
    field — it's what renders on the PDF — plus a `gc_sent_at` record extra so
    the state survives even if a stale autosave clobbers the value; the watcher
    re-stamps on its next sweep in that case (membership in the work list is
    recomputed from the value each time).
    """
    from subsystems.jobstart import drafts

    record = drafts.get_draft(int(bid_id))
    if record is None:
        return False
    values = dict(record.get("values") or {})
    if str(values.get("gc_confirmed_on") or "").strip():
        return False
    values["gc_confirmed_on"] = date_str
    drafts.save_draft(int(bid_id), values=values,
                      job_name=record.get("job_name"), actor="sent-watcher")
    drafts.set_status(int(bid_id),
                      status=record.get("status") or drafts.STATUS_DRAFT,
                      actor="sent-watcher", extra={"gc_sent_at": date_str})
    return True


def _person_name(email: Optional[str]) -> str:
    """jake@greenvalleycontractors.com → 'Jake'. Signs the GC email."""
    if not email:
        return "The Green Valley Team"
    name = str(email).split("@")[0].replace(".", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in name.split()) or "The Green Valley Team"


def _notify(fn) -> Optional[str]:
    """Post a Slack notice, best-effort. Monday/Drive already hold the truth by
    the time these fire, so a Slack problem must never fail the operation."""
    try:
        from adapters import slack_notify
        return "posted" if fn(slack_notify) else "skipped"
    except Exception as e:  # noqa: BLE001 — a notice never breaks a handoff
        print(f"[jobstart] Slack notice failed (non-fatal): {e}", file=sys.stderr)
        return f"skipped — {type(e).__name__}"
