"""
Takeoff outbox poller — Phase 2 consumer of the takeoff app's Firebase outbox.

The takeoff PWA (separate repo, Firebase RTDB + Netlify) queues estimate
payloads at ``gvc_portal_outbox/{draftId}`` with ``status: "queued"``.  This
flow polls that outbox and stages each queued payload as a SHARED estimate
draft for office review — the exact same draft-only staging the
``from-takeoff`` routes perform.  Staged means staged: this flow never
renders, finalizes, writes Monday, creates Gmail drafts, or sends anything to
a client (locked architecture — same boundary as takeoff_import_flow).

Ack protocol (the portal owns every status after "queued"; takeoff writes
ONLY "queued"):
    queued  — written by takeoff; the ONLY status this sweep consumes
    staged  — portal staged a draft; stagedAt + portalDraftId recorded
    error   — payload failed validation; error + processedAt recorded

Idempotency: the RTDB query filters on status == "queued", and the portal
draft id is DETERMINISTIC per outbox entry (``takeoff-{draftId}``, sanitized),
so Cloud Scheduler retries re-stage the same draft instead of duplicating it,
and a finalized/deleted draft never resurrects because its outbox entry is no
longer "queued".  Known trade-off: if staging succeeds but the ack PATCH
fails, the next sweep re-upserts that one draft with a fresh updated_at.

Triggered by Cloud Scheduler → POST /v1/tasks/poll-takeoff-outbox (X-API-Key)
every 10 minutes.  Per-item graceful: one bad payload never kills the sweep.
RTDB access is an OAuth bearer token from google-auth — the Cloud Run service
account (or the SA file at GVC_TAKEOFF_RTDB_CREDENTIALS) must be granted RTDB
access in the gvc-takeoff Firebase project, and the RTDB rules need
``".indexOn": "status"`` under /gvc_portal_outbox for the server-side filter.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime, timezone

DEFAULT_RTDB_URL = "https://gvc-takeoff-default-rtdb.firebaseio.com"
RTDB_SCOPES = (
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/firebase.database",
)

# Firebase push keys are [A-Za-z0-9_-]; anything outside that can't be safely
# placed in an RTDB path, so such entries are reported but never written back.
SAFE_OUTBOX_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Complement of the draft store's id charset (drafts.DRAFT_ID_RE) — used to
# sanitize, while valid_draft_id stays the single source of truth for checks.
_ID_BAD_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")


def _rtdb_url() -> str:
    return (os.environ.get("GVC_TAKEOFF_RTDB_URL") or DEFAULT_RTDB_URL).rstrip("/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def portal_draft_id(outbox_id: str) -> str:
    """Deterministic, draft-store-safe portal id for one outbox entry.

    ``takeoff-{draftId}`` when that already satisfies the draft store's
    ``^[A-Za-z0-9._-]{8,64}$``; otherwise bad chars become ``-`` and a short
    hash of the ORIGINAL id is appended, so two distinct outbox ids can never
    collapse into the same portal draft.  Same input → same output, always —
    that determinism is what makes re-runs idempotent.
    """
    candidate = f"takeoff-{str(outbox_id or '').strip()}"
    if len(candidate) <= 64 and not _ID_BAD_CHARS_RE.search(candidate):
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8]
    cleaned = _ID_BAD_CHARS_RE.sub("-", candidate)[:55].rstrip("-.")
    return f"{cleaned}-{digest}"


def _credentials():
    """SA file from GVC_TAKEOFF_RTDB_CREDENTIALS when set, else default creds.

    On Cloud Run the service account IS the default credential — grant it
    access in the gvc-takeoff Firebase project and no secret file is needed.
    """
    import google.auth

    sa_path = (os.environ.get("GVC_TAKEOFF_RTDB_CREDENTIALS") or "").strip()
    if sa_path:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(
            sa_path, scopes=list(RTDB_SCOPES)
        )
    credentials, _project = google.auth.default(scopes=list(RTDB_SCOPES))
    return credentials


def _bearer_token() -> str:
    from google.auth.transport.requests import Request

    credentials = _credentials()
    credentials.refresh(Request())
    return credentials.token


def _fetch_queued(token: str, limit: int) -> dict:
    """GET queued outbox entries.

    Prefer server-side ``orderBy=status`` (needs ``".indexOn": "status"``).
    If Firebase returns 400 (index missing), fall back to a shallow read and
    filter ``status == "queued"`` client-side so activation is not blocked.
    """
    import requests

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{_rtdb_url()}/gvc_portal_outbox.json"
    response = requests.get(
        url,
        params={
            "orderBy": '"status"',
            "equalTo": '"queued"',
            "limitToFirst": str(int(limit)),
        },
        headers=headers,
        timeout=30,
    )
    if response.status_code == 400:
        # Index not deployed yet — pull what we can and filter locally.
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict):
            return {}
        queued = {
            k: v for k, v in raw.items()
            if isinstance(v, dict) and v.get("status") == "queued"
        }
        # Deterministic cap
        keys = sorted(queued)[: max(0, int(limit))]
        return {k: queued[k] for k in keys}
    response.raise_for_status()
    entries = response.json()
    return entries if isinstance(entries, dict) else {}


def _ack(token: str, outbox_id: str, fields: dict) -> None:
    """PATCH one outbox entry (merge, not replace — takeoff's fields survive)."""
    import requests

    response = requests.patch(
        f"{_rtdb_url()}/gvc_portal_outbox/{outbox_id}.json",
        json=fields,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()


def _notify_staged(entry: dict, stored: dict) -> None:
    """Optional Slack notice — gated on GVC_TAKEOFF_OUTBOX_SLACK (default off)."""
    from adapters import slack_notify

    parts = [
        f"📥 *Takeoff estimate staged for review* — {stored.get('label') or stored.get('id')}",
        f"• Draft: {stored.get('id')} (open /ui/estimate to review)",
        f"• Queued by: {entry.get('queuedBy') or '—'}",
    ]
    bid_total = entry.get("bidTotal")
    if isinstance(bid_total, (int, float)) and not isinstance(bid_total, bool):
        parts.append(f"• Takeoff bid total: ${bid_total:,.2f}")
    parts.append("Draft only — nothing was sent; a person finalizes after review.")
    slack_notify.post_message("\n".join(parts))


def poll_outbox(*, dry_run: bool = False, limit: int = 20) -> dict:
    """
    One idempotent sweep.  Returns {ok, dry_run, checked, staged, skipped,
    errors: [{draftId, error}]}.  Never raises on per-item problems; only an
    unreachable/unauthorized RTDB or a missing draft store aborts (ok=False +
    code) because every subsequent item would fail identically.
    """
    from subsystems.estimate import drafts as estimate_drafts
    from subsystems.estimate import takeoff_import

    out: dict = {"ok": True, "dry_run": dry_run, "checked": 0, "staged": 0,
                 "skipped": 0, "errors": []}
    try:
        token = _bearer_token()
        entries = _fetch_queued(token, limit)
    except Exception as e:  # noqa: BLE001 — sweep-level: nothing to iterate
        out["ok"] = False
        out["code"] = type(e).__name__
        out["errors"].append(
            {"draftId": None, "error": f"outbox read: {type(e).__name__}: {e}"}
        )
        return out

    slack_enabled = (os.environ.get("GVC_TAKEOFF_OUTBOX_SLACK") or "").strip() == "1"

    for outbox_id in sorted(entries):
        out["checked"] += 1
        entry = entries[outbox_id]
        try:
            if not SAFE_OUTBOX_KEY_RE.match(outbox_id):
                # Can't even write an error status back — the key itself is
                # unsafe to place in an RTDB path.  Report and move on.
                out["errors"].append(
                    {"draftId": outbox_id, "error": "unsafe outbox key; skipped"}
                )
                continue
            if not isinstance(entry, dict):
                message = "outbox entry is not an object"
                out["errors"].append({"draftId": outbox_id, "error": message})
                if not dry_run:
                    _ack(token, outbox_id,
                         {"status": "error", "error": message,
                          "processedAt": _now_iso()})
                continue
            # Belt and braces — the query already filters on "queued", but a
            # stale index must never let a staged/error entry re-stage.
            if (entry.get("status") or "") != "queued":
                out["skipped"] += 1
                continue

            raw = takeoff_import.extract_takeoff_payload(entry.get("estimate"))
            data = takeoff_import.normalize_takeoff_payload(raw)
            errors = takeoff_import.validate_takeoff_payload(data)
            if errors:
                joined = "; ".join(errors)
                out["errors"].append({"draftId": outbox_id, "error": joined})
                if not dry_run:
                    _ack(token, outbox_id,
                         {"status": "error", "error": joined,
                          "processedAt": _now_iso()})
                continue

            if dry_run:
                out["staged"] += 1
                continue

            # Same staging call pattern as takeoff_import_flow, with the
            # DETERMINISTIC id swapped in — build_draft_record's fresh uuid
            # would break idempotent re-runs.
            draft_id = portal_draft_id(outbox_id)
            pending = takeoff_import.build_draft_record(
                data, f"outbox:{entry.get('queuedBy') or 'takeoff'}"
            )
            try:
                stored, _stale = estimate_drafts.upsert_draft(
                    draft_id,
                    label=pending["label"],
                    payload=pending["payload"],
                    updated_at=pending["updated_at"],
                    actor=pending["actor"],
                )
            except estimate_drafts.PortalStoreNotConfigured as e:
                # Sweep-level: every remaining upsert fails identically.  The
                # entries stay "queued" and the next sweep retries after an
                # admin fixes the store.
                out["ok"] = False
                out["code"] = "STORE_NOT_CONFIGURED"
                out["errors"].append({"draftId": outbox_id, "error": str(e)})
                break

            _ack(token, outbox_id,
                 {"status": "staged", "stagedAt": _now_iso(),
                  "portalDraftId": draft_id})
            out["staged"] += 1

            if slack_enabled:
                try:
                    _notify_staged(entry, stored)
                except Exception as e:  # noqa: BLE001 — notice is best-effort
                    out["errors"].append(
                        {"draftId": outbox_id,
                         "error": f"slack: {type(e).__name__}: {e}"}
                    )
        except Exception as e:  # noqa: BLE001 — one bad payload ≠ dead sweep
            out["errors"].append(
                {"draftId": outbox_id, "error": f"{type(e).__name__}: {e}"}
            )
            continue

    print(f"[takeoff-outbox] checked: {out['checked']} · staged: {out['staged']}"
          f" · skipped: {out['skipped']} · errors: {len(out['errors'])}"
          + (" · DRY-RUN" if dry_run else ""),
          file=sys.stderr)
    return out
