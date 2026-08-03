"""
Sent-watcher — detects when portal-created Gmail drafts were ACTUALLY sent.

The portal drafts invoice/estimate emails into hello@ and a HUMAN clicks Send
(locked architecture — the portal never sends). Gmail doesn't call us back, so
this flow polls: for recent Monday rows with no "Emailed on" date yet, it
searches hello@'s Sent mail for the draft's subject; a hit means the draft left
the building →
  1. stamp the Monday row's "Emailed on" date (invoices also flip Status
     "Draft Ready" → "Invoice Sent"; Paid/Void/office-set states untouched)
  2. post the 📤 emailed notice to Slack (#billing for invoices, #bids for
     estimates) — the truthful successor to notices that fired at draft time.

Triggered by Cloud Scheduler → POST /v1/tasks/check-sent (X-API-Key) every
10 minutes. Read-only against Gmail (gmail.readonly), additive against Monday,
and per-item graceful: one bad row never blocks the sweep.

State/dedup is the "Emailed on" column itself — a stamped row drops out of the
work list, so re-runs and Scheduler retries are idempotent. Two bounds keep the
sweep small and the channels quiet:
  • only rows whose issue/estimate date is within `limit_days` are checked
    (dateless history counts as old — the work list can't grow unbounded);
  • sends older than `notify_backfill_hours` are stamped QUIETLY (no Slack) —
    the first sweep after deploy records history instead of spamming it.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# GVC is Ohio/Indiana — Slack times should read local. Falls back to UTC when
# the tz database is unavailable (requirements pins `tzdata` so the container
# always has it; the fallback keeps a missing db from crashing the module).
try:
    _EASTERN = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    _EASTERN = timezone.utc


def _within_days(iso_text: Optional[str], days: int) -> bool:
    """True when iso_text (YYYY-MM-DD…) parses and falls within `days` back.
    Blank/garbage dates → False: rows without a date are treated as old, which
    keeps every sweep bounded."""
    if not iso_text:
        return False
    try:
        d = date.fromisoformat(iso_text.strip()[:10])
    except ValueError:
        return False
    return d >= (date.today() - timedelta(days=days))


def _pretty_sent(sent_dt: datetime) -> str:
    return sent_dt.astimezone(_EASTERN).strftime("%b %d, %I:%M %p")


def check_sent(*, limit_days: int = 45, notify_backfill_hours: int = 48,
               dry_run: bool = False) -> dict:
    """
    One sweep. Returns {ok, invoices: [...], estimates: [...], skipped, errors}.
    Never raises on per-item problems; only a missing Gmail scope/config aborts
    (ok=False + code) because every subsequent search would fail identically.
    """
    from adapters import slack_notify
    from adapters.gmail import GmailNotConfigured, GmailScopeMissing, find_sent_message
    from adapters.monday import estimate as bid
    from adapters.monday.client import MondayClient

    out: dict = {"ok": True, "dry_run": dry_run, "invoices": [], "estimates": [],
                 "skipped": {"invoices": 0, "estimates": 0}, "errors": []}
    now = datetime.now(timezone.utc)
    mc = MondayClient()

    def _sweep(kind: str, items: list, subject_for, stamp, notify) -> bool:
        """Shared per-item loop. Returns False when the sweep must abort
        (Gmail scope/config problem — surfaced on `out` already)."""
        for it in items:
            ident = it["identifier"]
            entry: dict = {"identifier": ident, "monday_item_id": it["monday_item_id"]}
            try:
                hit = find_sent_message(subject_for(ident), newer_than_days=limit_days + 15)
            except (GmailScopeMissing, GmailNotConfigured) as e:
                out["ok"] = False
                out["code"] = type(e).__name__
                out["errors"].append(str(e))
                return False
            except Exception as e:  # noqa: BLE001 — one flaky search ≠ dead sweep
                out["errors"].append(f"gmail {ident}: {type(e).__name__}: {e}")
                continue
            if not hit:
                out["skipped"][kind] += 1
                continue
            sent_dt = datetime.fromtimestamp((hit.get("sent_epoch_ms") or 0) / 1000,
                                             tz=timezone.utc)
            entry["sent_at"] = sent_dt.isoformat()
            fresh = (now - sent_dt) <= timedelta(hours=notify_backfill_hours)
            if dry_run:
                entry["would_notify"] = fresh
                out[kind].append(entry)
                continue
            try:
                stamp(it, sent_dt.date().isoformat())
                entry["stamped"] = True
            except Exception as e:  # noqa: BLE001
                # No stamp = row stays pending and retries next sweep; skip the
                # Slack ping now so a stamp-retry can't double-post later.
                entry["stamped"] = False
                out["errors"].append(f"monday {ident}: {type(e).__name__}: {e}")
                out[kind].append(entry)
                continue
            if fresh:
                try:
                    notify(it, sent_dt)
                    entry["notified"] = True
                except Exception as e:  # noqa: BLE001 — notice is best-effort
                    entry["notified"] = False
                    out["errors"].append(f"slack {ident}: {type(e).__name__}: {e}")
            else:
                entry["notified"] = False
                entry["backfill"] = True
            out[kind].append(entry)
        return True

    # ---------------- invoices (ledger board 1931784889) ----------------
    inv_rows: list = []
    try:
        # open_only=False: a row Paid before the watcher saw the send still
        # deserves its "Emailed on" date. Void rows are dropped below — a
        # voided invoice's draft may legitimately never be sent.
        inv_rows = mc.fetch_invoice_rows(open_only=False)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"invoice rows: {type(e).__name__}: {e}")

    inv_pending = [r for r in inv_rows
                   if r.get("identifier") and not r.get("emailed_on")
                   and (r.get("status") or "").strip().lower() != "void"
                   and _within_days(r.get("issue_date"), limit_days)]

    def _stamp_invoice(r: dict, date_str: str) -> None:
        mc.stamp_invoice_emailed(r["monday_item_id"], date_str,
                                 current_status=r.get("status"))

    def _notify_invoice(r: dict, sent_dt: datetime) -> None:
        amount = r.get("amount")
        slack_notify.notify_invoice_emailed({
            "identifier": r["identifier"],
            "customer": r.get("customer"),
            "job": r.get("job"),
            "amount_pretty": f"${amount:,.2f}" if isinstance(amount, (int, float)) else None,
            "sent_at_pretty": _pretty_sent(sent_dt),
        })

    aborted = not _sweep("invoices", inv_pending,
                         lambda ident: f"Invoice {ident}",
                         _stamp_invoice, _notify_invoice)

    # ---------------- estimates (Bid Board 1918846027) ----------------
    if not aborted:
        est_pending: list = []
        try:
            est_pending = [
                {"identifier": e["estimate_number"],
                 "monday_item_id": e["item_id"],
                 "item_name": e["item_name"]}
                for e in bid.fetch_pending_estimates(mc)
                if _within_days(e.get("estimate_date"), limit_days)
            ]
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"estimate rows: {type(e).__name__}: {e}")

        def _stamp_estimate(r: dict, date_str: str) -> None:
            bid.stamp_estimate_emailed(mc, r["monday_item_id"], date_str)

        def _notify_estimate(r: dict, sent_dt: datetime) -> None:
            slack_notify.notify_estimate_emailed({
                "identifier": r["identifier"],
                "job": r.get("item_name"),
                "sent_at_pretty": _pretty_sent(sent_dt),
            })

        _sweep("estimates", est_pending,
               lambda ident: f"Estimate {ident}",
               _stamp_estimate, _notify_estimate)

    # -------- GC scope confirmations (Job Start packets, not Monday) --------
    # Same truthful-signal problem as invoices/estimates: email_scope_to_gc
    # DRAFTS into hello@, a human clicks Send, and gc_confirmed_on used to be
    # stamped at draft time — so the packet claimed "scope emailed to the GC"
    # while the draft sat unsent. Every GC correction is a change order not
    # eaten, which only works if the reply-window clock starts when the email
    # actually LEFT. State lives on the packet record (GCS), not a Monday row.
    out["gc_confirmations"] = []
    out["skipped"]["gc_confirmations"] = 0
    if not aborted:
        from orchestrators import jobstart_flow

        gc_pending: list = []
        try:
            gc_pending = [
                {"identifier": g.get("gc_subject")
                              or f"Scope confirmation — {g['job_name']}",
                 "monday_item_id": g["bid_id"],   # _sweep's row id slot
                 "bid_id": g["bid_id"], "job_name": g.get("job_name")}
                for g in jobstart_flow.gc_pending_confirmations()
                if _within_days(g.get("gc_drafted_at"), limit_days)
            ]
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"gc rows: {type(e).__name__}: {e}")

        def _stamp_gc(r: dict, date_str: str) -> None:
            jobstart_flow.stamp_gc_confirmed(r["bid_id"], date_str)

        def _notify_gc(r: dict, sent_dt: datetime) -> None:
            slack_notify.notify_gc_scope_emailed({
                "job": r.get("job_name"),
                "sent_at_pretty": _pretty_sent(sent_dt),
            })

        # The identifier IS the subject here (subjects carry the job name, and
        # the exact drafted subject is persisted on the packet), so subject_for
        # is the identity function.
        _sweep("gc_confirmations", gc_pending,
               lambda ident: ident, _stamp_gc, _notify_gc)

    print(f"[sent-watch] invoices: {len(out['invoices'])} detected / "
          f"{out['skipped']['invoices']} still waiting · estimates: "
          f"{len(out['estimates'])} detected / {out['skipped']['estimates']} "
          f"still waiting · gc confirmations: {len(out['gc_confirmations'])} "
          f"detected / {out['skipped']['gc_confirmations']} still waiting · "
          f"errors: {len(out['errors'])}"
          + (" · DRY-RUN" if dry_run else ""),
          file=sys.stderr)
    return out
