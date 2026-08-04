"""
Minimal, graceful Slack notifier for the GVC invoice/estimate flows.
=========================================================================
Posts a message to a channel via Slack's chat.postMessage Web API using a
bot token. Designed to be *non-fatal*: if SLACK_BOT_TOKEN isn't set it
raises SlackNotConfigured (callers treat that as "skip"), and any other
failure is the caller's to swallow — a Slack outage must never break an
estimate or invoice run.

Uses stdlib urllib only (no extra dependency). Reuses the same bot token
pattern as the GVC report system; point SLACK_BOT_TOKEN at a bot that is a
member of the target channel.

Env:
  SLACK_BOT_TOKEN                 xoxb-... bot token (required to post)
  GVC_ESTIMATES_SLACK_CHANNEL     channel for estimate notices (default "#estimates")
  GVC_SLACK_AUTH_PROBE_TTL        /health auth.test cache seconds (default 300)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"

# Transient Slack failures worth retrying. Everything else (channel_not_found,
# invalid_auth, not_in_channel, msg_too_long, …) is a config/data problem —
# retrying won't help, so we fail fast on those.
RETRYABLE_SLACK_ERRORS = frozenset({
    "ratelimited", "internal_error", "fatal_error",
    "service_unavailable", "request_timeout",
})
MAX_RETRIES = 2              # total attempts = MAX_RETRIES + 1
BASE_BACKOFF_SECONDS = 0.5   # exponential: 0.5s, 1s, …
MAX_BACKOFF_SECONDS = 4.0    # cap any single wait (incl. Retry-After) so a
                             # rate-limit never hangs an interactive finalize.


class SlackNotConfigured(Exception):
    """Raised when SLACK_BOT_TOKEN is unset — callers should skip, not fail."""


def _sleep(seconds: float) -> None:
    """Backoff sleep, isolated so tests can monkeypatch it (no real waiting)."""
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# Live token probe (2026-07-02 incident: the portal's slack-bot-token secret
# held a redacted placeholder for 9 days — /health said slack_configured=true
# the whole time because it only checked env PRESENCE). auth.test proves the
# token WORKS. Cached per instance so /health stays fast and never hammers
# Slack; a bad token is a config problem, so a 5-minute-stale answer is fine.
# ---------------------------------------------------------------------------

def auth_test(token: Optional[str] = None, *, timeout: int = 5) -> dict:
    """
    One live Slack auth.test call. Raises SlackNotConfigured when no token is
    set (mirrors post_message). Returns the Slack response dict on any HTTP
    outcome; network/transport failures (including a corrupt token that breaks
    the auth header) come back as {"ok": False, "error": "network: ..."} so
    the caller never has to catch.
    """
    token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise SlackNotConfigured("SLACK_BOT_TOKEN not set.")
    req = urllib.request.Request(
        SLACK_AUTH_TEST_URL,
        data=b"",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — a probe must never raise transport errors
        return {"ok": False, "error": f"network: {type(e).__name__}: {e}"}


_AUTH_PROBE_CACHE: dict = {"at": 0.0, "result": None}


def _auth_probe_ttl() -> float:
    try:
        return float(os.environ.get("GVC_SLACK_AUTH_PROBE_TTL") or "300")
    except ValueError:
        return 300.0


def invalidate_auth_probe() -> None:
    _AUTH_PROBE_CACHE.update({"at": 0.0, "result": None})


def probe_token(force: bool = False) -> dict:
    """
    Cached auth.test for /health. Returns a flat dict:
      configured  bool       — SLACK_BOT_TOKEN is set
      ok          bool|None  — token actually authenticates (None = no token)
      error       str|None   — Slack/transport error when not ok
      bot_user    str|None   — authenticated bot identity (e.g. "gvc_reporting")
    """
    now = time.time()
    if (not force and _AUTH_PROBE_CACHE["result"] is not None
            and (now - _AUTH_PROBE_CACHE["at"]) < _auth_probe_ttl()):
        return _AUTH_PROBE_CACHE["result"]
    try:
        body = auth_test()
        result = {
            "configured": True,
            "ok": bool(body.get("ok")),
            "error": None if body.get("ok") else str(body.get("error", "unknown")),
            "bot_user": body.get("user") if body.get("ok") else None,
        }
    except SlackNotConfigured:
        result = {"configured": False, "ok": None,
                  "error": "SLACK_BOT_TOKEN not set", "bot_user": None}
    _AUTH_PROBE_CACHE.update({"at": now, "result": result})
    return result


def post_message(
    text: str,
    *,
    channel: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = 10,
    max_retries: int = MAX_RETRIES,
) -> dict:
    """
    Post `text` to `channel`, with bounded retry + exponential backoff on
    *transient* failures: HTTP 429 (honoring the Retry-After header, capped),
    HTTP 5xx, network/timeout errors, and ok=false errors in
    RETRYABLE_SLACK_ERRORS. A single transient blip used to permanently drop
    the notice — this makes it self-heal within a few seconds.

    Raises SlackNotConfigured if no token (callers skip, not fail). Raises
    RuntimeError on a non-retryable ok=false / 4xx, or once retries are
    exhausted. Returns the Slack response dict on success.
    """
    token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise SlackNotConfigured("SLACK_BOT_TOKEN not set; Slack notice skipped.")
    channel = channel or os.environ.get("GVC_ESTIMATES_SLACK_CHANNEL") or "#estimates"

    data = json.dumps({"channel": channel, "text": text}).encode("utf-8")

    last_error = "Slack post failed after retries"
    for attempt in range(max_retries + 1):
        retry_after: Optional[float] = None
        try:
            req = urllib.request.Request(
                SLACK_POST_URL,
                data=data,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                return body
            err = body.get("error", "unknown")
            last_error = f"Slack API error: {err}"
            if err not in RETRYABLE_SLACK_ERRORS:
                raise RuntimeError(last_error)  # config/data problem — fail fast
        except urllib.error.HTTPError as e:
            if e.code == 429:
                last_error = "Slack API error: ratelimited (HTTP 429)"
                hdr = e.headers.get("Retry-After") if e.headers else None
                try:
                    retry_after = float(hdr) if hdr else None
                except (TypeError, ValueError):
                    retry_after = None
            elif e.code >= 500:
                last_error = f"Slack HTTP {e.code}"
            else:
                raise RuntimeError(f"Slack HTTP {e.code}") from e  # 4xx — fail fast
        except urllib.error.URLError as e:
            last_error = f"Slack network error: {e.reason}"

        if attempt >= max_retries:
            break
        backoff = BASE_BACKOFF_SECONDS * (2 ** attempt)
        _sleep(min(max(backoff, retry_after or 0.0), MAX_BACKOFF_SECONDS))

    raise RuntimeError(last_error)


def _estimate_message(enriched: dict, *, revised: bool = False,
                      version: Optional[int] = None) -> str:
    """
    PURE: build the estimate-drafted notice text. Split out of
    notify_estimate_drafted so the wording (incl. the revision variant) is
    unit-testable without a Slack stub.

    Revision wording keeps the OUTBOUND number unchanged (locked design,
    2026-07-02) — the version marker is internal context for the channel.
    """
    est = enriched["estimate"]
    client = enriched.get("client") or {}
    job = enriched.get("job") or {}
    prepared_by = enriched.get("prepared_by") or {}

    if revised:
        v = f" (v{version})" if version else ""
        headline = (f"📄 *Estimate {est['identifier']}* REVISED{v} — updated draft "
                    f"in hello@ — ready to review & send. Same estimate number; "
                    f"the prior version is archived in Drive.")
    else:
        headline = f"📄 *Estimate {est['identifier']}* drafted in hello@ — ready to review & send."

    parts = [
        headline,
        f"• Client: {client.get('name', '—')}",
        f"• Project: {job.get('name', '—')}",
    ]
    total = est.get("main_total_pretty", "")
    if est.get("optional_items"):
        parts.append(f"• Total: {total}  (with options {est.get('total_with_options_pretty')})")
    else:
        parts.append(f"• Total: {total}")
    if prepared_by.get("name"):
        parts.append(f"• Prepared by: {prepared_by['name']}")
    return "\n".join(parts)


def notify_estimate_drafted(enriched: dict, *, channel: Optional[str] = None,
                            revised: bool = False,
                            version: Optional[int] = None) -> dict:
    """
    Post a '#estimates' notice that an estimate was drafted in hello@ and is
    ready to review/send. Fires on the Accept/draft-created step (our code
    drafts; a human sends) — so the wording says "drafted, ready to send",
    not "sent". `revised=True` switches to the revision wording (same
    outbound number, prior version archived); `version` is the new version
    ordinal when known (v2 = first revision).
    """
    return post_message(
        _estimate_message(enriched, revised=revised, version=version),
        channel=channel,
    )


def _co_message(info: dict, *, revised: bool = False,
                version: Optional[int] = None) -> str:
    """
    PURE: build the CO-drafted notice text. Split out of
    notify_change_order_drafted so the wording (incl. the revision variant)
    is unit-testable without a Slack stub. Mirrors _estimate_message.

    Revision wording keeps the CO NUMBER unchanged (locked design,
    2026-07-16) — the version marker is internal context for the channel.
    """
    co_number = info.get("co_number", "—")
    if revised:
        v = f" (v{version})" if version else ""
        headline = (f"🧾 *Change Order {co_number}* REVISED{v} — updated draft "
                    f"in hello@ — ready to review & send. Same change order "
                    f"number; the prior version is archived in Drive.")
    else:
        headline = f"🧾 *Change Order {co_number}* drafted in hello@ — ready to review & send."

    parts = [
        headline,
        f"• Client: {info.get('client_name', '—')}",
        f"• Project: {info.get('job_name', '—')}",
    ]
    if info.get("base_number"):
        parts.append(f"• Project / Estimate #: {info['base_number']}")
    if info.get("total_pretty"):
        parts.append(f"• CO total: {info['total_pretty']}")
    if info.get("prepared_by_name"):
        parts.append(f"• Prepared by: {info['prepared_by_name']}")
    return "\n".join(parts)


def notify_change_order_drafted(info: dict, *, channel: Optional[str] = None,
                                revised: bool = False,
                                version: Optional[int] = None) -> dict:
    """
    Post a notice that a Change Order was drafted in hello@ and is ready to
    review/send. Defaults to the #change-orders channel
    (GVC_CHANGE_ORDERS_SLACK_CHANNEL); falls back to #estimates if that env
    var is unset so a misconfiguration still lands somewhere visible.

    `info` keys: co_number, base_number, client_name, job_name, total_pretty,
    prepared_by_name (all optional except co_number). `revised=True` switches
    to the revision wording (same CO number, prior version archived);
    `version` is the new version ordinal when known (v2 = first revision).
    """
    # Resolve to the CO channel; if its env var is unset, default to the
    # #change-orders channel by NAME rather than falling back to the estimates
    # channel — a CO notice landing in #bids is worse than posting by name
    # (the bot is a member of #change-orders).
    channel = (
        channel
        or os.environ.get("GVC_CHANGE_ORDERS_SLACK_CHANNEL")
        or "#change-orders"
    )
    return post_message(_co_message(info, revised=revised, version=version), channel=channel)


def _coi_message(info: dict) -> str:
    """
    PURE: the outbound-COI notice text. `info` keys: holder_name (required),
    contact_name, contact_email, expiry_pretty (all optional). Wording says
    "drafted, ready to send" — same posture as every other notice (our code
    drafts; a human sends).
    """
    parts = [
        f"📜 *COI drafted* for {info.get('holder_name', '—')} — "
        f"hello@ draft ready to review & send.",
    ]
    contact = info.get("contact_name") or ""
    email = info.get("contact_email") or ""
    if contact or email:
        who = f"{contact} <{email}>".strip() if email else contact
        parts.append(f"• Send to: {who}")
    if info.get("expiry_pretty"):
        parts.append(f"• Certificate expires: {info['expiry_pretty']}")
    return "\n".join(parts)


def notify_coi_drafted(info: dict, *, channel: Optional[str] = None) -> dict:
    """
    Post a notice that a COI was drafted in hello@. Channel comes ONLY from
    GVC_COI_SLACK_CHANNEL (set it to the channel ID once someone creates the
    annual-maintenance channel; the @gvc_reporting bot must be a MEMBER).
    Deliberately NO named-channel fallback: until the env var is set this
    raises SlackNotConfigured, which callers treat as a clean skip — a COI
    notice guessing its way into #bids or #billing would be noise.
    """
    channel = channel or os.environ.get("GVC_COI_SLACK_CHANNEL")
    if not channel:
        raise SlackNotConfigured(
            "GVC_COI_SLACK_CHANNEL not set; COI Slack notice skipped."
        )
    return post_message(_coi_message(info), channel=channel)


# ---------------------------------------------------------------------------
# Billing-channel notices (invoice sent / payment recorded)
# ---------------------------------------------------------------------------
# Real-time money-milestone pings. They complement (don't replace) the report
# system's daily AR digest: the digest is the roll-up, these fire the moment it
# happens. Channel: GVC_BILLING_SLACK_CHANNEL (default "#billing"); the
# SLACK_BOT_TOKEN bot must be a member of that channel.

def _billing_channel(channel: Optional[str]) -> str:
    return channel or os.environ.get("GVC_BILLING_SLACK_CHANNEL") or "#billing"


def notify_invoice_sent(enriched: dict, writeback: dict, *, channel: Optional[str] = None) -> dict:
    """Post a billing-channel notice that an invoice was created and the Gmail
    draft is waiting in billing@ for review/send (our flow drafts; a human
    sends — same posture as the estimate notice)."""
    inv = enriched.get("invoice") or {}
    client = enriched.get("client") or {}
    job = enriched.get("job") or {}
    identifier = writeback.get("identifier") or inv.get("identifier") or "—"
    parts = [
        f"💵 *Invoice {identifier}* created — draft ready to review & send in billing@.",
        f"• Client: {client.get('name', '—')}",
        f"• Project: {job.get('name', '—')}",
        f"• Amount: {inv.get('total_pretty', '—')}",
    ]
    if inv.get("due_date_pretty"):
        parts.append(f"• Due: {inv['due_date_pretty']}")
    return post_message("\n".join(parts), channel=_billing_channel(channel))


def notify_payment_recorded(info: dict, *, channel: Optional[str] = None) -> dict:
    """Post a billing-channel notice that a payment was recorded against an
    invoice (paid-by-check flow commit). `info` keys: identifier (required),
    amount, check_no (all optional except identifier)."""
    parts = [f"🟢 *Payment recorded* — Invoice {info.get('identifier', '—')} marked paid."]
    if info.get("amount"):
        parts.append(f"• Amount: {info['amount']}")
    if info.get("check_no"):
        parts.append(f"• Check #: {info['check_no']}")
    return post_message("\n".join(parts), channel=_billing_channel(channel))


def notify_invoice_emailed(info: dict, *, channel: Optional[str] = None) -> dict:
    """Post a billing-channel notice that the invoice email was ACTUALLY sent —
    the sent-watcher saw the hello@ draft turn into a Sent message. Complements
    notify_invoice_sent (which fires at draft creation). `info` keys:
    identifier (required), customer, job, amount_pretty, sent_at_pretty."""
    parts = [f"📤 *Invoice {info.get('identifier', '—')} emailed to client*."]
    if info.get("customer"):
        parts.append(f"• Client: {info['customer']}")
    if info.get("job"):
        parts.append(f"• Project: {info['job']}")
    if info.get("amount_pretty"):
        parts.append(f"• Amount: {info['amount_pretty']}")
    if info.get("sent_at_pretty"):
        parts.append(f"• Sent: {info['sent_at_pretty']}")
    return post_message("\n".join(parts), channel=_billing_channel(channel))


def notify_estimate_emailed(info: dict, *, channel: Optional[str] = None) -> dict:
    """Estimate counterpart of notify_invoice_emailed — posts to the estimates
    channel (#bids) when the estimate draft is detected as actually sent.
    Truthful successor to the retired Monday 'Bid Sent Notice' wording."""
    parts = [f"📤 *Estimate {info.get('identifier', '—')} emailed to client*."]
    if info.get("customer"):
        parts.append(f"• Client: {info['customer']}")
    if info.get("job"):
        parts.append(f"• Project: {info['job']}")
    if info.get("sent_at_pretty"):
        parts.append(f"• Sent: {info['sent_at_pretty']}")
    return post_message("\n".join(parts), channel=channel)


# ---------------------------------------------------------------------------
# Job Check → #operations
# ---------------------------------------------------------------------------
# A field save is the crew telling the office where a job stands; this puts it
# in front of ops in seconds instead of waiting for someone to open Monday.
# ONE message per save (a crew member sets several fields and taps once), never
# one per column. Channel: GVC_JOBCHECK_SLACK_CHANNEL, ID-based with NO named
# fallback — unset means a clean skip rather than a message in the wrong room.

# Changes that mean "someone needs to do something", not just "work progressed".
# A blocker leads the message and flips the header, because that's the line
# worth seeing from a truck.
JOBCHECK_ALERT_LABELS = ("Blocked", "Needs from Jordan")
JOBCHECK_CLEAR_VALUES = {"clear", "nothing", "(empty)", ""}


def _jobcheck_channel(channel: Optional[str]) -> Optional[str]:
    return channel or os.environ.get("GVC_JOBCHECK_SLACK_CHANNEL") or None


def _is_alerting(label: str, new_value: str) -> bool:
    return (label in JOBCHECK_ALERT_LABELS
            and (new_value or "").strip().lower() not in JOBCHECK_CLEAR_VALUES)


def _jobcheck_message(info: dict) -> str:
    """
    PURE. info: {job, actor, url, changes: [{label, old, new}, ...]}.
    Blocker lines float to the top and set the header, so the reason a job is
    stuck is never buried under three routine progress lines.
    """
    changes = list(info.get("changes") or [])
    alerts = [c for c in changes if _is_alerting(c.get("label", ""), c.get("new", ""))]
    routine = [c for c in changes if c not in alerts]

    header = "🚧 *Job update — needs attention*" if alerts else "🔧 *Job update*"
    parts = [f"{header} — {info.get('job', 'Unknown job')}"]

    actor = (info.get("actor") or "").split("@")[0] or "someone"
    parts.append(f"_updated by {actor}_")
    for c in alerts + routine:
        old = c.get("old") or "(empty)"
        new = c.get("new") or "(empty)"
        parts.append(f"• *{c.get('label', '?')}*: {old} → {new}")
    if not changes:
        parts.append("• (no field changes recorded)")
    if info.get("url"):
        parts.append(f"<{info['url']}|Open in Monday>")
    return "\n".join(parts)


def notify_jobcheck_saved(info: dict, *, channel: Optional[str] = None) -> Optional[dict]:
    """Post a Job Check save to #operations. Returns None when no channel is
    configured (clean skip). Raises like any other notice — the caller treats a
    Slack problem as non-fatal, because the Monday write already succeeded."""
    target = _jobcheck_channel(channel)
    if not target:
        raise SlackNotConfigured("GVC_JOBCHECK_SLACK_CHANNEL not set.")
    return post_message(_jobcheck_message(info), channel=target)


# ---------------------------------------------------------------------------
# Job Start → #operations (Sales → Operations handoff)
# ---------------------------------------------------------------------------
# This notice REPLACES the legacy Bid Board automation's message, which claimed
# "a new item was created in the Projects Dashboard and Operations Dashboard"
# on every won job while never creating the Operations one (design doc §Why
# this exists). The rule here: only name what was actually created, and link it.
# Falls back to the Job Check channel so a handoff and a field update land in
# the same room — ops watches one place.

def _jobstart_channel(channel: Optional[str]) -> Optional[str]:
    return (channel or os.environ.get("GVC_JOBSTART_SLACK_CHANNEL")
            or os.environ.get("GVC_JOBCHECK_SLACK_CHANNEL") or None)


def _jobstart_message(info: dict) -> str:
    """
    PURE. info: {job, actor, bid_url, project_url, ops_url, estimate_number,
    estimate_total, start_date, supervisor, warnings}.
    Every claim is conditional on the URL actually existing — the whole point
    of this tool is that the handoff notice stops lying.
    """
    parts = [f"🤝 *Job accepted by Operations* — {info.get('job', 'Unknown job')}"]

    actor = (info.get("actor") or "").split("@")[0] or "someone"
    sender = (info.get("sent_by") or "").split("@")[0]
    parts.append(f"_accepted by {actor}_" if not sender
                 else f"_{sender} handed off · {actor} accepted_")

    if info.get("estimate_number"):
        line = f"• Estimate: {info['estimate_number']}"
        if info.get("estimate_total"):
            line += f" · {info['estimate_total']}"
        parts.append(line)
    if info.get("start_date"):
        parts.append(f"• Start date: {info['start_date']}")
    if info.get("supervisor"):
        parts.append(f"• Site contact: {info['supervisor']}")

    links = []
    if info.get("packet_url"):
        links.append(f"<{info['packet_url']}|Handoff packet PDF>")
    if info.get("project_url"):
        links.append(f"<{info['project_url']}|Project>")
    if info.get("ops_url"):
        links.append(f"<{info['ops_url']}|Operations task>")
    if info.get("bid_url"):
        links.append(f"<{info['bid_url']}|Bid>")
    if links:
        parts.append("• " + " · ".join(links))

    for warning in info.get("warnings") or []:
        parts.append(f"⚠️ {warning}")
    return "\n".join(parts)


def notify_job_start_handoff(info: dict, *,
                             channel: Optional[str] = None) -> Optional[dict]:
    """Post a completed Sales → Operations handoff. Returns None when no channel
    is configured (clean skip). Raises like any other notice — the caller treats
    a Slack problem as non-fatal, because Monday already holds the truth."""
    target = _jobstart_channel(channel)
    if not target:
        raise SlackNotConfigured(
            "Neither GVC_JOBSTART_SLACK_CHANNEL nor GVC_JOBCHECK_SLACK_CHANNEL is set.")
    return post_message(_jobstart_message(info), channel=target)


def _job_start_sent_message(info: dict) -> str:
    """PURE. Sales has sent a packet; ops needs to act. This is the message that
    makes the acceptance step real — without it, a packet sits unseen."""
    parts = [f"📋 *Job packet ready for Operations* — {info.get('job', 'Unknown job')}"]
    actor = (info.get("actor") or "").split("@")[0] or "someone"
    parts.append(f"_from {actor} — needs an accept or a send-back_")
    if info.get("start_date"):
        parts.append(f"• Start date: {info['start_date']}")
    if info.get("supervisor"):
        parts.append(f"• Site contact: {info['supervisor']}")
    # Claimed ONLY when the write actually happened — the whole reason these
    # notices exist is that the legacy automation announced work it never did.
    if info.get("bid_marked_accepted"):
        parts.append("• Bid marked Accepted and moved to Won Deals")
    links = []
    if info.get("preview_url"):
        links.append(f"<{info['preview_url']}|Read the packet>")
    if info.get("bid_url"):
        links.append(f"<{info['bid_url']}|Bid>")
    if links:
        parts.append("• " + " · ".join(links))
    return "\n".join(parts)


def notify_job_start_sent(info: dict, *,
                          channel: Optional[str] = None) -> Optional[dict]:
    """Tell ops a packet is waiting on them."""
    target = _jobstart_channel(channel)
    if not target:
        raise SlackNotConfigured(
            "Neither GVC_JOBSTART_SLACK_CHANNEL nor GVC_JOBCHECK_SLACK_CHANNEL is set.")
    return post_message(_job_start_sent_message(info), channel=target)


def _gc_scope_emailed_message(info: dict) -> str:
    """PURE. The GC scope confirmation actually LEFT hello@ — posted by the
    sent-watcher, never at draft time. The 3-business-day reply window starts
    now, so the room should know the clock is running."""
    parts = [f"📤 *GC scope confirmation emailed* — {info.get('job', 'Unknown job')}"]
    if info.get("sent_at_pretty"):
        parts.append(f"• Sent {info['sent_at_pretty']} — their 3-business-day "
                     f"reply window is running")
    return "\n".join(parts)


def notify_gc_scope_emailed(info: dict, *,
                            channel: Optional[str] = None) -> Optional[dict]:
    """Tell the room the GC scope email was genuinely sent."""
    target = _jobstart_channel(channel)
    if not target:
        raise SlackNotConfigured(
            "Neither GVC_JOBSTART_SLACK_CHANNEL nor GVC_JOBCHECK_SLACK_CHANNEL is set.")
    return post_message(_gc_scope_emailed_message(info), channel=target)


def _job_start_sent_back_message(info: dict) -> str:
    """PURE. Ops returned a packet. Names what's missing so the fix is obvious —
    this list, over time, IS the defect list for the sales process."""
    parts = [f"↩️ *Job packet sent back* — {info.get('job', 'Unknown job')}"]
    actor = (info.get("actor") or "").split("@")[0] or "someone"
    back_to = (info.get("sent_by") or "").split("@")[0]
    parts.append(f"_{actor} needs more from {back_to or 'Sales'}_")
    if info.get("note"):
        parts.append(f"• Missing: {info['note']}")
    return "\n".join(parts)


def notify_job_start_sent_back(info: dict, *,
                               channel: Optional[str] = None) -> Optional[dict]:
    """Tell the room a packet went back to Sales, and why."""
    target = _jobstart_channel(channel)
    if not target:
        raise SlackNotConfigured(
            "Neither GVC_JOBSTART_SLACK_CHANNEL nor GVC_JOBCHECK_SLACK_CHANNEL is set.")
    return post_message(_job_start_sent_back_message(info), channel=target)


# ---------------------------------------------------------------------------
# Ops failure alerts (Portal → #gvc-ops-alerts)
# ---------------------------------------------------------------------------
# Fire-and-forget so a Slack-first ops model SEES failures instead of burying
# them in Cloud Logging. NEVER raises — a broken alert must not mask or replace
# the underlying error (same contract as the report system's
# slack_notifier.post_failure). Posts via the same SLACK_BOT_TOKEN bot
# (@gvc_reporting) to GVC_OPS_ALERTS_CHANNEL (default "#gvc-ops-alerts"); the
# bot must be a member of that channel.

def post_failure(reason: str, *, context: Optional[dict] = None,
                 channel: Optional[str] = None) -> bool:
    """Post an ops failure alert. Returns True if delivered, False otherwise.
    Swallows ALL errors (including SlackNotConfigured) by design — alerting
    must never raise into the failing request path."""
    channel = channel or os.environ.get("GVC_OPS_ALERTS_CHANNEL") or "#gvc-ops-alerts"
    lines = [f"🔴 *Portal error* — {reason}"]
    for k, v in (context or {}).items():
        if v:
            lines.append(f"• {k}: {v}")
    try:
        post_message("\n".join(lines), channel=channel)
        return True
    except Exception as e:  # noqa: BLE001 — fire-and-forget by contract
        print(f"[slack_notify] failure alert not delivered ({type(e).__name__}: {e})",
              file=sys.stderr)
        return False


def notify_finalize_degraded(kind: str, identifier: str, failed_steps: dict,
                             *, channel: Optional[str] = None) -> bool:
    """Alert that a finalize SUCCEEDED (HTTP 200) but a downstream step silently
    failed — the invisible class that once dropped the #bids (then "#leads") estimate notice.
    `failed_steps` maps a step label -> short reason. No-op when empty."""
    if not failed_steps:
        return False
    steps = ", ".join(failed_steps.keys())
    return post_failure(
        f"{kind} {identifier} finalized, but these steps failed: {steps}",
        context={f"{kind} #": identifier, **failed_steps},
        channel=channel,
    )


# ---------------------------------------------------------------------------
# Morning Brief — Slack DMs (never public performance warnings)
# ---------------------------------------------------------------------------

SLACK_LOOKUP_URL = "https://slack.com/api/users.lookupByEmail"
SLACK_OPEN_DM_URL = "https://slack.com/api/conversations.open"


def _slack_api(url: str, payload: dict, *, token: Optional[str] = None) -> dict:
    token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise SlackNotConfigured("SLACK_BOT_TOKEN not set.")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def lookup_user_id_by_email(email: str) -> Optional[str]:
    email = (email or "").strip().lower()
    if not email:
        return None
    try:
        # lookupByEmail wants form-encoded GET traditionally; Web API also
        # accepts POST with application/x-www-form-urlencoded.
        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            raise SlackNotConfigured("SLACK_BOT_TOKEN not set.")
        qs = urllib.parse.urlencode({"email": email})
        req = urllib.request.Request(
            f"{SLACK_LOOKUP_URL}?{qs}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return ((data.get("user") or {}).get("id"))
        return None
    except SlackNotConfigured:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[slack_notify] lookupByEmail failed: {e}", file=sys.stderr)
        return None


def open_dm(user_id: str) -> Optional[str]:
    if not user_id:
        return None
    try:
        data = _slack_api(SLACK_OPEN_DM_URL, {"users": user_id})
        if data.get("ok"):
            return ((data.get("channel") or {}).get("id"))
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[slack_notify] conversations.open failed: {e}", file=sys.stderr)
        return None


def post_dm(email: str, text: str) -> dict:
    """
    DM an employee by Google/Workspace email. Never raises — returns
    {ok, skipped?, error?}. Performance warnings MUST use this, not channels.
    """
    try:
        uid = lookup_user_id_by_email(email)
        if not uid:
            return {"ok": False, "skipped": True, "error": "user_not_found"}
        channel = open_dm(uid)
        if not channel:
            return {"ok": False, "skipped": True, "error": "dm_open_failed"}
        post_message(text, channel=channel)
        return {"ok": True}
    except SlackNotConfigured as e:
        return {"ok": False, "skipped": True, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        print(f"[slack_notify] post_dm failed: {e}", file=sys.stderr)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def notify_action_request_assigned(req: dict) -> dict:
    to = req.get("needed_from_email") or ""
    need = (req.get("need") or "")[:280]
    who = (req.get("requester_email") or "").split("@")[0]
    return post_dm(
        to,
        f"📋 *Action Request* from {who}\n"
        f"• {need}\n"
        f"Acknowledge in the portal Morning Brief within 30 minutes.",
    )


def notify_action_request_escalation(req: dict) -> dict:
    to = req.get("needed_from_email") or ""
    kind = req.get("escalation") or "reminder"
    need = (req.get("need") or "")[:200]
    return post_dm(
        to,
        f"⏰ Action Request *{kind.replace('_', ' ')}*\n• {need}\n"
        f"Open Morning Brief → Action Requests.",
    )


def notify_action_request_ack_due(req: dict) -> dict:
    """
    Spec: "Missing acknowledgment creates a Slack DM reminder" — the specific
    30-minute-ack-window reminder (distinct from notify_action_request_
    escalation's general overdue/ack_reminder wording, kept for the existing
    orchestrator wiring). DM to the recipient only.
    """
    to = req.get("needed_from_email") or ""
    need = (req.get("need") or "")[:200]
    return post_dm(
        to,
        f"⏰ *Acknowledgment due* — you haven't acknowledged this Action Request yet\n"
        f"• {need}\n"
        f"Acknowledge it in the portal Morning Brief or reply here.",
    )


def notify_prep_miss_private(email: str, *, streak: int) -> dict:
    return post_dm(
        email,
        f"Private prep notice: missed Morning Brief preparation "
        f"(streak {streak} scheduled workday(s)). "
        f"Open your brief before 6:45 AM next scheduled day.",
    )


def notify_owner_prep_alert(text: str) -> dict:
    """
    Owner/GM visibility for prep-miss escalations (spec: streak reaches the
    3+ tier gets "employee notice + Jordan/GM high-level visibility"). Tries,
    in order: a DM to GVC_OWNER_SLACK_USER (a Slack user id, if set) → a DM to
    GVC_OWNER_SLACK_EMAIL (falls back to jordan@greenvalleycontractors.com) →
    a post to GVC_OWNER_SLACK_CHANNEL if that's explicitly configured. Never
    raises hard; a fully unconfigured owner is a clean skip, not a failure —
    this must never become a public-channel performance callout by accident.
    """
    owner_user_id = (os.environ.get("GVC_OWNER_SLACK_USER") or "").strip()
    if owner_user_id:
        try:
            channel = open_dm(owner_user_id)
            if channel:
                post_message(text, channel=channel)
                return {"ok": True, "via": "dm_user_id"}
        except Exception as e:  # noqa: BLE001
            print(f"[slack_notify] owner DM by user id failed: {e}", file=sys.stderr)

    owner_email = (
        os.environ.get("GVC_OWNER_SLACK_EMAIL")
        or "jordan@greenvalleycontractors.com"
    )
    result = post_dm(owner_email, text)
    if result.get("ok"):
        result["via"] = "dm_email"
        return result

    owner_channel = (os.environ.get("GVC_OWNER_SLACK_CHANNEL") or "").strip()
    if owner_channel:
        try:
            post_message(text, channel=owner_channel)
            return {"ok": True, "via": "channel"}
        except Exception as e:  # noqa: BLE001
            print(f"[slack_notify] owner channel post failed: {e}", file=sys.stderr)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {"ok": False, "skipped": True, "error": "owner_unreachable"}

