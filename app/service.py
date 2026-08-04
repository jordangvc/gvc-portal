"""
FastAPI HTTP wrapper around the GVC invoice flow.
=========================================================================
Designed to be deployed as a Cloud Run service. Same code paths as the
CLI — calls process_one() with the same modes (dry-run / preflight / live).

Endpoints:
  GET  /health                 — liveness probe (Cloud Run + monitoring)
  POST /v1/invoice/from-monday — build + create an invoice from a Monday item ID
  POST /v1/invoice/from-json   — build + create an invoice from inline JSON

Auth: All POST endpoints require an X-API-Key header that matches the
GVC_SERVICE_API_KEY env var. Cloud Run can optionally layer Google IAM
auth on top — see docs/cloud-run-deploy.md.

Local dev:
  uvicorn service:app --reload --port 8080
"""
from __future__ import annotations

from shared import paths
import json
import os
import re
import sys
import threading
from html import escape as html_escape
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

load_dotenv(paths.ENV_FILE)

# Import after .env loads so module-level constants pick up env values
from shared.paths import OUTPUT_DIR
from subsystems.invoice.model import validate_environment
from orchestrators.invoice_flow import process_one, _run, _run_correction
from shared.errors import _friendly_error, humanize_validation_message
from orchestrators.estimate_flow import process_estimate
from adapters.monday.client import (
    MondayClient,
    MondayInsufficientData,
    MondayNotConfigured,
)

API_KEY = (os.environ.get("GVC_SERVICE_API_KEY") or "").strip() or None

# --- Browser UI (portal) ------------------------------------------------------
# The /ui/* routes serve static HTML forms for office staff who can't use the
# Claude Desktop / MCP path (Windows). Reached at portal.greenvalleycontractors.com
# (Cloud Run domain mapping). Auth is IN-APP Google sign-in — the free path from
# docs/portal-deploy-plan.md, which supersedes the older IAP + LB design in
# docs/ui-iap-setup.md. Humans authenticate via /auth/login (Google OAuth,
# Workspace-internal + allowlist); the session rides a signed HttpOnly cookie.
# The /v1/* MCP path keeps its X-API-Key, untouched.
from shared import auth as portal_auth
from shared import access as access
from shared import activity as activity
from shared import activity_detail as activity_detail
from shared import activity_read as activity_read
from shared import portal_store as portal_store
from subsystems.estimate import drafts as estimate_drafts
from subsystems.estimate import scope_catalog as scope_catalog
from subsystems.invoice import drafts as invoice_drafts
from subsystems.change_order import drafts as co_drafts
from subsystems.fieldguide import runs as fieldguide_runs
from adapters import vision as vision
from adapters import slack_notify as slack_notify
from subsystems.checks import deposit as check_deposit
from orchestrators import change_order_flow as change_order_flow
from orchestrators import check_flow
from orchestrators import coi_flow
from orchestrators import lien_flow
from orchestrators import jobcheck_flow
from orchestrators import morning_flow
from orchestrators import jobstart_flow
from orchestrators import billing_flow
from subsystems.coi import template as coi_template
from adapters.monday import co as monday_co
from adapters.monday import estimate as monday_estimate
from adapters.monday import jobstart as monday_jobstart
from adapters.monday import morning as monday_morning
from adapters.monday import jobcheck as monday_jobcheck
from adapters.monday import search as monday_search
from subsystems.invoice import correct as invoice_correct
from adapters.stripe_invoice import preflight_stripe, void_stripe_invoice

WEB_DIR = paths.WEB_DIR
# Local dev escape hatch — set GVC_UI_DEV_BYPASS=1 to skip the sign-in check
# entirely (uvicorn on localhost). NEVER set this on the public service.
UI_DEV_BYPASS = os.environ.get("GVC_UI_DEV_BYPASS") == "1"

# Startup environment audit — surface missing optional integrations to the
# Cloud Run logs so an admin can spot misconfiguration without waiting for the
# first request to fail. Assume live mode for the audit (worst case).
_startup_warnings, _startup_errors = validate_environment(
    mode="live", needs_stripe=True, monday_source=True,
)
for _w in _startup_warnings:
    print(f"[service:startup] WARNING: {_w}", file=sys.stderr)
for _e in _startup_errors:
    # Non-fatal at boot — service can still serve --dry-run via /from-json.
    # The per-request guard in _run() handles hard requirements.
    print(f"[service:startup] missing config: {_e}", file=sys.stderr)
if not API_KEY:
    print(
        "[service:startup] WARNING: GVC_SERVICE_API_KEY env var not set. "
        "All POST endpoints will return 503 until it is.",
        file=sys.stderr,
    )

app = FastAPI(
    title="GVC Invoice Service",
    description="HTTP wrapper around the GVC invoice creation flow.",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Ops failure alerting — single chokepoint for server-side errors.
# Every route already raises HTTPException with a {code, detail, advice}
# envelope (via shared.errors._friendly_error). This handler fires a Slack
# ops alert on 5xx ONLY — 4xx are user-actionable (bad input, idempotency
# conflict, auth) and would be noise — then delegates to FastAPI's default
# handler so the HTTP response is byte-for-byte unchanged. Alerting can never
# alter or break the response (post_failure is fire-and-forget).
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def _alerting_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code >= 500:
        try:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            code = detail.get("code") or "ERROR"
            advice = detail.get("advice") or detail.get("detail") or str(exc.detail)
            slack_notify.post_failure(
                f"{code} ({exc.status_code}) on {request.method} {request.url.path}",
                context={"detail": advice},
            )
        except Exception:  # noqa: BLE001 — never let alerting change the response
            pass
    return await http_exception_handler(request, exc)


# ---------------------------------------------------------------------------
# Grants-store tripwire (2026-07-02 incident). A missing portal/grants.json in
# gcs mode silently locks out every non-superadmin (deny-by-default resolves an
# absent store to "no access"). portal_store fires this hook — throttled — on
# any fresh read that finds no object; we make it LOUD in #gvc-ops-alerts.
# Wired here because shared/ may not import adapters/ (layering).
# ---------------------------------------------------------------------------
def _grants_store_missing_alert(object_name: str) -> None:
    if access.backend() != "gcs":
        return  # env backend: the store is legitimately unused
    slack_notify.post_failure(
        f"grants store object '{object_name}' is MISSING — every non-superadmin "
        "is locked out of the portal (deny-by-default).",
        context={
            "advice": "Restore the object / check GVC_PORTAL_STATE_BUCKET. "
                      "Runbook: docs/incident-2026-07-02-grants-lifecycle-wipe.md",
        },
    )


portal_store.MISSING_STORE_HOOK = _grants_store_missing_alert


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _warm_monday_caches() -> dict:
    """
    Best-effort: force-refresh Job Start / Morning / Job Check list caches
    and persist them to GCS so cold Cloud Run instances stay fast.
    """
    from adapters.monday import cache as monday_cache

    warmed: list[str] = []
    errors: dict[str, str] = {}
    try:
        mc = MondayClient()
    except Exception as e:  # noqa: BLE001 — warm is best-effort
        msg = f"{type(e).__name__}: {e}"
        print(f"[monday:warm] client init failed: {msg}", file=sys.stderr)
        return {"ok": False, "warmed": [], "errors": {"client": msg},
                "cache": monday_cache.stats()}

    # Always hit Monday (refresh), not the SWR get path — otherwise a fresh L1
    # would skip the write to durable L2 snapshots.
    for key, factory in (
        ("list:jobstart:bids",
         lambda: monday_jobstart._fetch_bids_uncached(mc)),
        ("list:morning:ops_items",
         lambda: monday_morning._fetch_ops_items_uncached(mc)),
        ("list:jobcheck:active_jobs",
         lambda: monday_jobcheck._fetch_active_jobs_uncached(mc)),
    ):
        try:
            monday_cache.refresh(
                key,
                factory,
                ttl=monday_cache.list_ttl(),
                stale_ttl=monday_cache.stale_ttl(),
            )
            warmed.append(key)
        except Exception as e:  # noqa: BLE001 — keep warming the rest
            msg = f"{type(e).__name__}: {e}"
            errors[key] = msg
            print(f"[monday:warm] {key} failed: {msg}", file=sys.stderr)

    return {
        "ok": not errors,
        "warmed": warmed,
        "errors": errors,
        "cache": monday_cache.stats(),
    }


def require_api_key(x_api_key: Optional[str]) -> None:
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "code": "SERVICE_MISCONFIGURED",
                "detail": "GVC_SERVICE_API_KEY env var not set on the service.",
                "advice": "Ask an admin to set the GVC_SERVICE_API_KEY secret.",
            },
        )
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "code": "INVALID_API_KEY",
                "detail": "Invalid or missing X-API-Key header.",
                "advice": "Check the X-API-Key header value.",
            },
        )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

Mode = Literal["dry-run", "preflight", "live"]


class FromMondayRequest(BaseModel):
    monday_item_id: int = Field(..., description="Monday Projects-board item ID")
    mode: Mode = Field("preflight", description="dry-run | preflight | live")
    finalize: bool = Field(True, description="(live only) finalize the Stripe invoice")


class FromJSONRequest(BaseModel):
    data: dict = Field(..., description="Canonical invoice JSON (see example_input.json)")
    mode: Mode = Field("preflight")
    finalize: bool = Field(True)


EstimateMode = Literal["dry-run", "finalize"]

CorrectMode = Literal["dry-run", "live"]


CorrectIntent = Literal["auto", "recipient"]


class InvoiceCorrectRequest(BaseModel):
    original: Optional[dict] = Field(None, description="The original as-billed invoice JSON (auto intent). For the inline 'recipient' intent it's optional — the existing invoice is read from Stripe.")
    corrected: Optional[dict] = Field(None, description="The full edited invoice JSON (from the form). If omitted, `corrections` is merged onto `original`.")
    corrections: dict = Field(default_factory=dict,
                              description="Partial overrides (nested {client/job/invoice} or flat e.g. {email}) — used when `corrected` is not supplied.")
    intent: CorrectIntent = Field("auto", description="auto = diff original vs corrected and route automatically. recipient = forced in-place recipient fix (inline, from the invoice form).")
    mode: CorrectMode = Field("dry-run", description="dry-run (preview the diff + plan + corrected PDF) | live (apply it)")


class EstimateRunRequest(BaseModel):
    data: dict = Field(..., description="Canonical estimate JSON (see example_estimate.json)")
    mode: EstimateMode = Field("dry-run", description="dry-run (render only) | finalize (hello@ draft + Slack)")
    revise: bool = Field(
        False,
        description="Update an existing estimate under the SAME outbound number: "
                    "requires estimate.identifier; archives the prior PDF/sidecar "
                    "in Drive as e{n}-, overwrites the Monday columns, and uses "
                    "revision wording in the Gmail draft + Slack notice.")


class AdminUpsertRequest(BaseModel):
    email: str = Field(..., description="Employee work email (the grant key)")
    features: list[str] = Field(default_factory=list, description="Granted features, or ['*'] for all")
    person: dict = Field(default_factory=dict, description="Optional employee fields (name, position, …)")


class AdminRemoveRequest(BaseModel):
    email: str = Field(..., description="Employee work email to remove from the portal")


class EstimateDraftUpsertRequest(BaseModel):
    payload: dict = Field(..., description="The in-progress estimate form state (canonical estimate JSON)")
    label: Optional[str] = Field(None, description="Short human label; derived from client/job if omitted")
    updated_at: Optional[str] = Field(None, description="Client ISO-8601 timestamp; used for last-writer-wins")


class FieldGuideRunUpsertRequest(BaseModel):
    payload: dict = Field(..., description="The checklist run state: procedure, job, checked step keys, note, done")
    updated_at: Optional[str] = Field(None, description="Client ISO-8601 timestamp; used for last-writer-wins")


class EstimateScopeCatalogRequest(BaseModel):
    catalog: dict = Field(
        ...,
        description="Full standard-scope catalog to store (replace semantics): "
                    "{trades:[{id,name,scopes:[{id,title,default_scope,default_price}]}]}")


class ChangeOrderRunRequest(BaseModel):
    data: dict = Field(..., description="Canonical change-order JSON (see example_change_order.json)")
    mode: EstimateMode = Field("dry-run", description="dry-run (render only) | finalize (Drive + hello@ draft + Slack + Monday CO item + Ops task)")
    revise: bool = Field(
        False,
        description="Update an existing Change Order under the SAME CO number: "
                    "requires change_order.co_number; archives the prior PDF/sidecar "
                    "in Drive as e{n}-, updates the Monday CO item + Ops task in place "
                    "(CO Status resets to Drafted), and uses revision wording in the "
                    "Gmail draft + Slack notice.")


class ChangeOrderDraftUpsertRequest(BaseModel):
    payload: dict = Field(..., description="The in-progress change-order form state (canonical CO JSON)")
    label: Optional[str] = Field(None, description="Short human label; derived from client/job if omitted")
    updated_at: Optional[str] = Field(None, description="Client ISO-8601 timestamp; used for last-writer-wins")


class CoiRunRequest(BaseModel):
    data: dict = Field(..., description="COI payload: {holder:{name,address}, contact:{name,email}}")
    mode: EstimateMode = Field("dry-run", description="dry-run (stamp + preview) | finalize (Drive + hello@ draft + Slack + Monday placeholder)")


class CoiBulkRunRequest(BaseModel):
    sheet_url: str = Field(..., description="Google Sheets URL (or bare id) of the Annual COI List")
    mode: EstimateMode = Field("dry-run", description="dry-run (parse + review, no writes) | finalize (process one chunk of ready rows)")
    after_row: int = Field(0, description="(finalize) process only sheet rows AFTER this row number — the chunk cursor; pass the previous response's next_after_row")
    chunk: Optional[int] = Field(None, description="(finalize) max rows this call (default GVC_COI_BULK_CHUNK, 15)")


class ActivityExportRequest(BaseModel):
    month: Optional[str] = Field(
        None,
        description="Target month 'YYYY-MM'. Omit to export the previous calendar month (UTC) — the month that just closed. Used for the monthly Cloud Scheduler trigger; pass an explicit month for backfill.",
    )
    formats: list[str] = Field(
        default_factory=lambda: ["json", "csv"],
        description="File formats to write: any of 'json', 'csv'. Defaults to both.",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def healthz() -> dict:
    """
    Liveness probe. No auth required.

    `gmail_ready` actively probes the cached OAuth token by attempting a
    refresh. The Claude skill calls this before running live and refuses
    to proceed if False — that's the difference between Andrea seeing a
    Stripe invoice with no draft URL (confusing) and a clean refusal
    before any state changes (clear).
    """
    gmail_ready = False
    gmail_error = None
    try:
        from adapters.gmail import _load_credentials
        _load_credentials()
        gmail_ready = True
    except Exception as e:
        gmail_error = f"{type(e).__name__}: {e}"

    # Slack readiness for the portal notices. slack_configured used to mean
    # "token PRESENT", which hid a dead placeholder token for 9 days (2026-07-02
    # incident) — it now means "token WORKS": a cached live auth.test
    # (slack_notify.probe_token, TTL GVC_SLACK_AUTH_PROBE_TTL, default 5 min).
    # slack_auth_error carries the exact Slack error when it doesn't.
    slack_probe = slack_notify.probe_token()
    slack_configured = bool(slack_probe["configured"] and slack_probe["ok"])

    # Monday readiness — same "present vs works" correction (2026-07-27).
    # Cached `me { name }` probe; TTL GVC_MONDAY_AUTH_PROBE_TTL (default 5 min)
    # so Cloud Run's liveness checks don't burn Monday API quota.
    try:
        from adapters.monday.client import probe_token as _monday_probe_token
        monday_probe = _monday_probe_token()
    except Exception as e:  # noqa: BLE001 — health must not raise
        monday_probe = {"configured": bool(os.environ.get("MONDAY_API_TOKEN")),
                        "ok": False, "error": f"probe failed: {type(e).__name__}: {e}",
                        "account_user": None}
    slack_channel = os.environ.get("GVC_ESTIMATES_SLACK_CHANNEL") or "#estimates (default)"

    # Grants-store visibility (2026-07-02 incident): in gcs mode an empty or
    # missing store means org-wide lockout — surface it on the health check
    # instead of leaving it invisible until someone can't sign in.
    grants_backend = access.backend()
    grants_store_ok = None
    grants_users = None
    if grants_backend == "gcs":
        try:
            grants_users = len(portal_store.list_users())
            grants_store_ok = grants_users > 0
        except Exception:  # noqa: BLE001 — health must not raise
            grants_store_ok = False

    return {
        "ok": True,
        "service": "gvc-invoice",
        "stripe_configured": bool(os.environ.get("STRIPE_API_KEY")),
        "drive_configured": bool(os.environ.get("GVC_DRIVE_SHARED_DRIVE_ID")),
        # v r6: monday_configured now means "token WORKS", not "token PRESENT"
        # — same correction Slack got after 2026-07-02. monday_auth_error
        # carries the exact failure (e.g. a 401 from a revoked token).
        "monday_configured": bool(monday_probe["configured"] and monday_probe["ok"]),
        "monday_token_present": bool(monday_probe["configured"]),
        "monday_auth_error": monday_probe["error"],
        "monday_account_user": monday_probe["account_user"],
        "preview_bucket_configured": bool(os.environ.get("GVC_GCS_PREVIEW_BUCKET")),
        "gmail_ready": gmail_ready,
        "gmail_error": gmail_error,
        "slack_configured": slack_configured,
        "slack_token_ok": slack_probe["ok"],
        "slack_auth_error": slack_probe["error"],
        "slack_bot_user": slack_probe["bot_user"],
        "slack_estimate_channel": slack_channel,
        "slack_change_orders_channel": os.environ.get("GVC_CHANGE_ORDERS_SLACK_CHANNEL") or "(falls back to estimate channel)",
        "slack_ops_alerts_channel": os.environ.get("GVC_OPS_ALERTS_CHANNEL") or "#gvc-ops-alerts (default)",
        "slack_billing_channel": os.environ.get("GVC_BILLING_SLACK_CHANNEL") or "#billing (default)",
        "slack_coi_channel": os.environ.get("GVC_COI_SLACK_CHANNEL") or "(not set — COI notices skipped)",
        "grants_backend": grants_backend,
        "grants_store_ok": grants_store_ok,
        "grants_users": grants_users,
    }


@app.post("/v1/invoice/from-monday")
def from_monday(
    req: FromMondayRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Build an invoice from a Monday Projects-board item and run the flow."""
    require_api_key(x_api_key)
    try:
        mc = MondayClient()
        data = mc.build_invoice_dict(req.monday_item_id)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "code": "MONDAY_NOT_CONFIGURED",
                "detail": str(e),
                "advice": "Ask an admin to set the MONDAY_API_TOKEN secret.",
            },
        )
    except MondayInsufficientData as e:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "code": "MONDAY_INSUFFICIENT_DATA",
                "detail": str(e),
                "advice": "The Monday item is missing required fields. Fill them in and retry.",
            },
        )
    return _run(data, mode=req.mode, finalize=req.finalize,
                source_label=f"monday:{req.monday_item_id}")


@app.post("/v1/invoice/from-json")
def from_json(
    req: FromJSONRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Build an invoice from inline JSON. Useful for testing and ad-hoc runs."""
    require_api_key(x_api_key)
    return _run(req.data, mode=req.mode, finalize=req.finalize, source_label="api:from-json")


def _log_estimate_slack(wb: dict, *, actor: str) -> None:
    """
    Emit a structured `estimate.slack` activity event for the finalize Slack
    sub-step, so a dropped estimates-channel (#bids) notice is answerable straight from
    the activity log instead of only raw Cloud Logging stderr. Records:
      result="ok"      — posted
      result="skipped" — Slack not configured (no SLACK_BOT_TOKEN)
      result="error"   — post attempted and failed (after retries); error detail attached
    """
    identifier = wb.get("identifier")
    if wb.get("slack_notified"):
        activity.log_event("estimate.slack", actor=actor, target=identifier, result="ok")
        return
    if wb.get("slack_error"):
        activity.log_event("estimate.slack", actor=actor, target=identifier,
                           result="error", severity="WARNING",
                           error=str(wb.get("slack_error")))
    else:
        activity.log_event("estimate.slack", actor=actor, target=identifier,
                           result="skipped",
                           error=str(wb.get("slack_status") or "not configured"))


@app.post("/v1/estimate/from-json")
def estimate_from_json(
    req: EstimateRunRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """
    Build an estimate from inline JSON — the machine / Claude path, mirroring
    /v1/invoice/from-json. Wraps process_estimate with the SAME dry-run -> finalize
    gate as the browser form (no Stripe, ever; finalize leaves a hello@ Gmail draft
    for human review — never auto-sends).

    Auth today = the shared X-API-Key (same as the other /v1/* tools). The per-user
    token layer in docs/portal-claude-access-and-automation-design.md (phase 3) will
    layer on top of this same endpoint without changing its body.
    """
    require_api_key(x_api_key)
    try:
        wb = process_estimate(req.data, OUTPUT_DIR, mode=req.mode,
                              source_label="api:estimate-from-json",
                              revise=req.revise)
        if req.mode == "finalize":
            wb["mode_warning"] = (
                "FINALIZED — the estimate draft is waiting in hello@. Open it, "
                "review, then click Send."
            )
            if not wb.get("gmail_draft_url") and "gmail_status" not in wb:
                wb["gmail_status"] = "No draft URL returned — check hello@ configuration."
            _log_estimate_slack(wb, actor="api:estimate-from-json")
        return {"ok": True, "writeback": wb}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[api:estimate] error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


class CheckSentRequest(BaseModel):
    dry_run: bool = Field(False, description="Detect and report only — no Monday/Slack writes")
    limit_days: int = Field(45, ge=1, le=120, description="How far back to scan for unsent drafts")


@app.post("/v1/tasks/check-sent")
def tasks_check_sent(
    req: CheckSentRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """
    Sent-watcher sweep: for recent invoice/estimate rows with no 'Emailed on'
    yet, look for the portal-created draft's subject in hello@'s Sent mail;
    when found, stamp the Monday row (invoices also flip Draft Ready →
    Invoice Sent) and post the 📤 emailed notice to Slack. Idempotent — a
    stamped row drops out of the work list, so Cloud Scheduler retries are
    safe. Gmail access is READ-ONLY; this endpoint never sends mail.

    X-API-Key protected, same as the other /v1/* endpoints — Cloud Scheduler
    sends the key in the header (job: gvc-sent-watch, every 10 min).
    """
    require_api_key(x_api_key)
    from orchestrators.sent_watch_flow import check_sent

    # check_sent is graceful by contract (per-item try/except; returns ok=False
    # + code on a sweep-level problem like a missing Gmail scope) — no
    # _friendly_error translation needed here.
    return check_sent(limit_days=req.limit_days, dry_run=req.dry_run)



@app.post("/v1/tasks/warm-monday")
def tasks_warm_monday(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """
    Force-refresh Monday list caches into memory + GCS snapshots
    (Job Start / Morning / Job Check). X-API-Key protected like check-sent.
    Wire Cloud Scheduler every 2–5 minutes so cold Cloud Run instances still
    open instantly. Returns immediately; work runs in a background thread.
    """
    require_api_key(x_api_key)
    threading.Thread(
        target=_warm_monday_caches, name="warm-monday", daemon=True
    ).start()
    return {"ok": True, "started": True}


@app.post("/v1/activity/export-month")
def activity_export_month(
    req: ActivityExportRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """
    Archive one calendar month of portal activity to the backup Drive folder as
    "Portal Activity Log MMYYYY.{json,csv}". Idempotent per (month, format): a
    re-run overwrites that month's file in place (so Cloud Scheduler retries are
    safe). Triggered monthly on the 1st (defaults to the month that just
    closed); also callable ad-hoc for backfill by passing `month`.

    X-API-Key protected, same as the other /v1/* endpoints — Cloud Scheduler
    sends the key in the header.
    """
    require_api_key(x_api_key)

    month_key = (req.month or "").strip() or activity_read.previous_month_key()
    try:
        start, end = activity_read.month_bounds(month_key)
        stub = activity_read.month_file_stub(month_key)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_MONTH",
                    "detail": f"month must be 'YYYY-MM', got {req.month!r}",
                    "advice": "Pass month like '2026-06', or omit it to export last month."},
        )

    formats = [f.lower() for f in (req.formats or []) if f.lower() in ("json", "csv")] or ["json", "csv"]

    backup_folder_id = (os.environ.get("GVC_ACTIVITY_BACKUP_FOLDER_ID") or "").strip()
    if not backup_folder_id:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "BACKUP_FOLDER_NOT_CONFIGURED",
                    "detail": "GVC_ACTIVITY_BACKUP_FOLDER_ID env var is not set.",
                    "advice": "Set GVC_ACTIVITY_BACKUP_FOLDER_ID to the Drive folder ID, then redeploy."},
        )

    try:
        out = activity_read.fetch_all_in_range(start, end)
    except activity_read.ActivityReadNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "ACTIVITY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Grant the service account roles/logging.viewer, then retry."},
        )
    events = out["events"]

    base = f"Portal Activity Log {stub}"
    artifacts: list[tuple[str, bytes, str]] = []
    if "json" in formats:
        artifacts.append((
            f"{base}.json",
            activity_read.to_json(events, month_key=month_key).encode("utf-8"),
            "application/json",
        ))
    if "csv" in formats:
        artifacts.append((
            f"{base}.csv",
            activity_read.to_csv(events).encode("utf-8"),
            "text/csv",
        ))

    try:
        from adapters.drive import DriveUploader
        uploader = DriveUploader()
        written = []
        for filename, data, mimetype in artifacts:
            res = uploader.upload_or_replace_file(
                folder_id=backup_folder_id, filename=filename, data=data, mimetype=mimetype,
            )
            written.append({"filename": filename, "file_id": res["file_id"],
                            "action": res["action"], "web_view_link": res.get("web_view_link")})
    except Exception as e:  # noqa: BLE001 — surface a clean envelope, log the failure
        activity.log_event("activity.backup", actor="scheduler", target=month_key,
                           result="error", error=str(e)[:300])
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(status_code=status,
                            detail={"ok": False, "code": code, "detail": detail, "advice": advice})

    activity.log_event("activity.backup", actor="scheduler", target=month_key,
                       result="ok", events=len(events), truncated=out["truncated"],
                       files=",".join(w["filename"] for w in written))

    return {
        "ok": True,
        "month": month_key,
        "events": len(events),
        "truncated": out["truncated"],
        "folder_id": backup_folder_id,
        "written": written,
    }


# ---------------------------------------------------------------------------
# Browser UI (Phase 1) — additive; reuses _run(). Existing /v1/* + the MCP
# transport are unaffected. Protected by Cloud IAP at the LB, not X-API-Key.
# ---------------------------------------------------------------------------

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")


def _callback_uri(request: Request) -> str:
    """The OAuth redirect URI for this host (https except on localhost)."""
    host = request.headers.get("host") or request.url.netloc
    scheme = "http" if host.split(":")[0] in ("localhost", "127.0.0.1") else "https"
    return f"{scheme}://{host}/auth/callback"


def require_ui_access(request: Request) -> str:
    """
    Single deny-by-default chokepoint for all /ui/* routes (in-app Google
    sign-in; see docs/portal-deploy-plan.md Phase 2). Returns the signed-in
    email. Browser page loads get a redirect to /auth/login; the form's
    fetch() calls get a 401 the page JS turns into a reload.

    GVC_UI_DEV_BYPASS=1 skips the check for local development only.
    """
    if UI_DEV_BYPASS:
        return "dev-bypass@localhost"

    email = portal_auth.verify_session(request.cookies.get(portal_auth.SESSION_COOKIE))
    if email:
        return email

    is_page_load = request.method == "GET" and not request.url.path.startswith("/ui/api/")
    if is_page_load:
        raise HTTPException(
            status_code=303,
            headers={"Location": f"/auth/login?next={request.url.path}"},
            detail="Redirecting to sign-in.",
        )
    raise HTTPException(
        status_code=401,
        detail={
            "ok": False,
            "code": "SESSION_EXPIRED",
            "detail": "Not signed in, or the session expired.",
            "advice": "Reload the page to sign in again.",
        },
    )


def require_feature(request: Request, feature: str) -> str:
    """
    Signed-in AND granted `feature`. Returns the email. Page loads that lack the
    grant are bounced to the hub (where the tile isn't shown anyway); /ui/api/*
    calls get a 403. Every denial is logged.
    """
    email = require_ui_access(request)
    if access.has_feature(email, feature):
        return email
    activity.log_event("access.denied", actor=email, target=feature,
                       result="denied", severity="WARNING")
    is_page_load = request.method == "GET" and not request.url.path.startswith("/ui/api/")
    if is_page_load:
        raise HTTPException(
            status_code=303,
            headers={"Location": "/"},
            detail="Not authorized for this tool.",
        )
    raise HTTPException(
        status_code=403,
        detail={
            "ok": False,
            "code": "FEATURE_NOT_GRANTED",
            "detail": f"Your account isn't granted '{feature}'.",
            "advice": "Ask an admin to grant you access on the Admin page.",
        },
    )


def require_admin(request: Request) -> str:
    return require_feature(request, "admin")


# ---------------------------------------------------------------------------
# Auth routes (Google OAuth — portal sign-in)
# ---------------------------------------------------------------------------

@app.get("/auth/login")
def auth_login(request: Request, next: str = "/") -> RedirectResponse:
    try:
        url = portal_auth.login_redirect_url(
            redirect_uri=_callback_uri(request), next_path=next
        )
    except portal_auth.AuthNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "AUTH_NOT_CONFIGURED",
                    "detail": str(e),
                    "advice": "Ask an admin — the OAuth secrets aren't mounted yet "
                              "(portal-deploy-plan.md Phase 1)."},
        )
    return RedirectResponse(url, status_code=303)


@app.get("/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    try:
        next_path = portal_auth.parse_state(state)
        email = portal_auth.exchange_code(
            code=code, redirect_uri=_callback_uri(request)
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=403,
            detail={"ok": False, "code": "SIGN_IN_DENIED", "detail": str(e),
                    "advice": "Use your @greenvalleycontractors.com account. "
                              "If you should have access, ask an admin to add you "
                              "to the allowlist."},
        )
    except portal_auth.AuthNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "AUTH_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — OAuth secrets missing."},
        )
    resp = RedirectResponse(next_path, status_code=303)
    resp.set_cookie(
        portal_auth.SESSION_COOKIE,
        portal_auth.make_session_cookie(email),
        max_age=portal_auth.session_ttl(),
        secure=not UI_DEV_BYPASS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    print(f"[auth] {email} signed in")
    return resp


@app.get("/auth/logout")
def auth_logout() -> RedirectResponse:
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(portal_auth.SESSION_COOKIE, path="/")
    return resp


@app.get("/", response_class=HTMLResponse)
def portal_home(request: Request) -> HTMLResponse:
    """Portal hub: tiles for each tool the signed-in user can reach.
    Unauthenticated GETs are 303'd to /auth/login by require_ui_access."""
    return _portal_home_impl(request)


@app.get("/ui/gvc.css")
def portal_stylesheet() -> Response:
    """
    The shared GVC design system (web/gvc.css), served to every portal page.

    Deliberately NOT behind require_ui_access: a stylesheet carries no data, and
    gating it would mean the sign-in page itself renders unstyled. Cached for an
    hour — long enough to stop refetching on every page, short enough that a
    redeploy shows up without anyone clearing a cache.

    Before this existed each page carried its own private <style> block, so a
    restyle meant editing twelve files and they drifted apart (activity.html was
    still carrying rules for form cards it doesn't have).
    """
    path = WEB_DIR / "gvc.css"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return Response(content=path.read_text(encoding="utf-8"),
                    media_type="text/css",
                    headers={"Cache-Control": "public, max-age=3600"})


def _portal_home_impl(request: Request) -> HTMLResponse:
    email = require_ui_access(request)
    path = WEB_DIR / "hub.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    feats = sorted(access.effective_features(email))
    activity.log_event("hub.open", actor=email, target=",".join(feats) or "none")
    html = (
        path.read_text(encoding="utf-8")
        .replace("{{EMAIL}}", html_escape(email))
        .replace("{{FEATURES_JSON}}", json.dumps(feats))
    )
    return HTMLResponse(html)


@app.get("/ui/gvc.css")
def portal_stylesheet() -> Response:
    """
    The shared GVC design system (web/gvc.css), served to every portal page.

    Deliberately NOT behind require_ui_access: a stylesheet carries no data, and
    gating it would mean the sign-in page itself renders unstyled. Cached for an
    hour — long enough to stop refetching on every page, short enough that a
    redeploy shows up without anyone clearing a cache.
    """
    path = WEB_DIR / "gvc.css"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/ui/invoice", response_class=HTMLResponse)
def ui_invoice_form(request: Request) -> HTMLResponse:
    """Serve the office-staff invoice form."""
    email = require_feature(request, "invoice")
    activity.log_event("tool.open", actor=email, target="invoice")
    path = WEB_DIR / "invoice.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/ui/billing", response_class=HTMLResponse)
def ui_billing_hub(request: Request) -> HTMLResponse:
    """
    Invoicing hub — Ready-to-Invoice queue, accepted bids needing a next step,
    and multi-field search so the office does not need a Project # memorized.
    Gated by `invoice` (same grant as the invoice generator).
    """
    email = require_feature(request, "invoice")
    activity.log_event("billing.open", actor=email, target="billing", result="ok")
    path = WEB_DIR / "billing.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(
        path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email))
    )


@app.get("/ui/api/billing/hub")
def ui_billing_hub_data(request: Request) -> dict:
    """Queue payload for the Billing hub (Ready to Invoice + Accepted bids…)."""
    email = require_feature(request, "invoice")
    try:
        payload = billing_flow.billing_hub_payload()
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "BILLING_HUB_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Try again, or open a project from search below."},
        )
    counts = payload.get("counts") or {}
    activity.log_event(
        "billing.hub", actor=email, result="ok",
        ready=counts.get("ready_to_invoice"),
        accepted=counts.get("accepted_bids"),
        projects=counts.get("projects_billing"),
    )
    return payload


@app.get("/ui/api/billing/search")
def ui_billing_search(request: Request, q: str = "") -> dict:
    """Multi-field search across Projects + Bid Board for the Billing hub."""
    email = require_feature(request, "invoice")
    term = (q or "").strip()
    if len(term) < 2:
        return {"ok": True, "q": term, "projects": [], "bids": [], "notes": []}
    try:
        payload = billing_flow.search_billing(None, term)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "BILLING_SEARCH_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Try a shorter name, address fragment, or Project #."},
        )
    activity.log_event(
        "billing.search", actor=email, result="ok", target=term,
        **activity_detail.summarize(
            "billing",
            {"billing": {"q": term}, "name": term},
            {"project_count": len(payload.get("projects") or []),
             "bid_count": len(payload.get("bids") or [])},
        ),
    )
    return payload


@app.get("/ui/api/billing/activity")
def ui_billing_activity(request: Request, limit: int = 30) -> dict:
    """
    Recent estimate/invoice/billing/check activity for the Billing hub strip.
    Best-effort: if Cloud Logging is unavailable, return an empty list with a note
    (hub queues still work).
    """
    require_feature(request, "invoice")
    try:
        n = max(1, min(int(limit or 30), 50))
    except (TypeError, ValueError):
        n = 30
    wanted = (
        "invoice.", "estimate.", "billing.", "check.", "estimate.qa",
    )
    events_out: list[dict] = []
    note = None
    try:
        out = activity_read.fetch_events(
            range_key="30d", page_size=activity_read.MAX_PAGE_SIZE,
        )
        for ev in out.get("events") or []:
            action = str(ev.get("action") or "")
            if not any(action.startswith(p) or action == p.rstrip(".")
                       for p in wanted):
                continue
            events_out.append({
                "action": action,
                "actor": ev.get("actor"),
                "customer": ev.get("customer"),
                "job": ev.get("job"),
                "target": ev.get("target"),
                "amount": ev.get("amount"),
                "result": ev.get("result"),
                "time": ev.get("ts") or ev.get("time"),
                "qa": ev.get("qa") or ev.get("qa_summary"),
            })
            if len(events_out) >= n:
                break
    except activity_read.ActivityReadNotConfigured as e:
        note = str(e)
    except Exception as e:  # noqa: BLE001 — strip must not break the hub
        note = f"{type(e).__name__}: {e}"
    payload: dict = {"ok": True, "events": events_out}
    if note:
        payload["note"] = note
    return payload


@app.post("/ui/api/invoice/run")
def ui_invoice_run(req: FromJSONRequest, request: Request) -> dict:
    """Run the invoice flow for the browser form. Same core as /v1/from-json."""
    email = require_feature(request, "invoice")
    try:
        out = _run(req.data, mode=req.mode, finalize=req.finalize, source_label="ui:invoice")
    except Exception as e:
        # Log the ATTEMPT with the customer/document it was for — a bare
        # "invoice.run … error" can't be investigated a week later.
        activity.log_event(
            "invoice.run", actor=email, result="error", severity="ERROR",
            **activity_detail.summarize("invoice", req.data, None, mode=req.mode, error=e))
        raise
    wb = out.get("writeback") if isinstance(out, dict) else None
    activity.log_event(
        "invoice.run", actor=email, result=activity_detail.result_for(wb),
        **activity_detail.summarize("invoice", req.data, wb, mode=req.mode))
    return out


@app.post("/ui/api/invoice/correct")
def ui_invoice_correct(req: InvoiceCorrectRequest, request: Request) -> dict:
    """
    Correct an already-issued invoice as a diff. dry-run returns the field-by-field
    changes + the routed plan + a rebuilt preview; live applies it. The route
    (in_place vs Stripe revision) is decided automatically from what changed.
    """
    email = require_feature(request, "invoice")
    return _run_correction(
        req.original, corrected=req.corrected, corrections=req.corrections,
        intent=req.intent, mode=req.mode, actor=email,
    )


@app.get("/ui/api/invoice/original")
def ui_invoice_original(request: Request, identifier: str = "") -> dict:
    """
    Load the as-billed invoice JSON for `identifier` from its Drive sidecar
    (`<identifier>.gvc.json`, written next to the PDF at finalize). Lets the
    correction page pull the exact original straight from Drive instead of asking
    the office to paste it.
    """
    require_feature(request, "invoice")
    ident = (identifier or "").strip()
    if not ident:
        raise HTTPException(status_code=422, detail={
            "ok": False, "code": "BAD_IDENTIFIER",
            "detail": "Enter an invoice number to load.",
            "advice": "Type the invoice's number (e.g. GVC-2026-MV-007)."})
    try:
        from adapters.drive import DriveUploader, DriveNotConfigured
        du = DriveUploader()
        hit = du.find_file_anywhere(f"{ident}.gvc.json")
        if not hit:
            raise HTTPException(status_code=404, detail={
                "ok": False, "code": "ORIGINAL_NOT_FOUND",
                "detail": f"No saved JSON found for '{ident}'.",
                "advice": "Only invoices billed after this feature shipped have a saved "
                          "JSON. For older ones, paste the original invoice JSON instead."})
        original = du.download_json(hit["id"])
    except HTTPException:
        raise
    except DriveNotConfigured as e:
        raise HTTPException(status_code=503, detail={
            "ok": False, "code": "DRIVE_NOT_CONFIGURED", "detail": str(e),
            "advice": "Ask an admin to confirm the Drive service-account credentials."})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail={
            "ok": False, "code": "ORIGINAL_LOAD_FAILED",
            "detail": f"{type(e).__name__}: {e}",
            "advice": "Try again, or paste the original invoice JSON."})
    return {"ok": True, "original": original, "drive_file_id": hit["id"]}


@app.get("/ui/api/invoice/lookup")
def ui_invoice_lookup(
    request: Request,
    project_number: str = "",
    item_id: str = "",
    monday_url: str = "",
) -> dict:
    """
    Prefill the invoice form from a Projects-board item (SoT).

    Accepts Project # (preferred) OR a Monday item id / URL so the Billing hub
    and Find-the-Project search can deep-link without forcing staff to memorize
    numbers. Pulls client, job site, Res/Comm/AIA, Drive folder, suggested
    identifier. Sets job.monday_item_id. Line items stay manual.
    """
    email = require_feature(request, "invoice")
    pn = (project_number or "").strip()
    raw_id = (item_id or "").strip() or (monday_url or "").strip()
    parsed_id = _parse_monday_item_id(raw_id) if raw_id else None
    if not pn and not parsed_id:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_PROJECT_NUMBER",
                    "detail": "Enter a Project #, paste a Monday URL, or pick a search result.",
                    "advice": "Search by builder, address, city, or Project # (e.g. C-005)."},
        )
    try:
        mc = MondayClient()
        target_id: Optional[int] = None
        if pn:
            match = mc.find_project_by_number(pn)
            if not match:
                raise HTTPException(
                    status_code=404,
                    detail={"ok": False, "code": "PROJECT_NOT_FOUND",
                            "detail": f"No project found with Project # '{pn}'.",
                            "advice": "Try search by builder/address, or check the Projects board."},
                )
            target_id = int(match["item_id"])
        else:
            target_id = int(parsed_id)  # type: ignore[arg-type]
        prefill = mc.build_invoice_prefill(target_id)
    except HTTPException:
        raise
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001 — bad id / not found / API error → friendly 422
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "MONDAY_LOOKUP_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Confirm the project exists on the Projects board."},
        )
    activity.log_event(
        "invoice.lookup", actor=email,
        target=pn or str(parsed_id or ""),
        result="ok",
    )
    return {"ok": True, "prefill": prefill}


@app.get("/ui/api/invoice/search")
def ui_invoice_search(request: Request, q: str = "") -> dict:
    """
    Find-the-Project for the invoice form: builder, supervisor, address/city/
    state (inside location), project name, or Project #. Returns light rows the
    UI renders as tappable results.
    """
    email = require_feature(request, "invoice")
    term = (q or "").strip()
    if len(term) < 2:
        return {"ok": True, "results": []}
    try:
        rows = monday_search.search_projects_rich(MondayClient(), term, limit=20)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "MONDAY_SEARCH_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Try again, or look the job up from the Billing hub."},
        )
    results = [{
        "item_id": r.get("item_id"),
        "name": r.get("name") or "",
        "project_number": r.get("project_number") or "",
        "builder": r.get("builder") or "",
        "supervisor": r.get("supervisor") or "",
        "location": r.get("location") or "",
        "group": r.get("group") or "",
        "invoice_status": r.get("invoice_status") or "",
        "url": r.get("url") or "",
        "match_fields": r.get("match_fields") or [],
    } for r in (rows or [])]
    activity.log_event(
        "invoice.search", actor=email, target=term, result="ok",
        hits=len(results),
    )
    return {"ok": True, "results": results}


@app.get("/ui/api/invoice/customer-search")
def ui_invoice_customer_search(request: Request, q: str = "") -> dict:
    """
    Search the Customer & Vendor Directory by name to import billing identity
    (name, email, contact, phone, billing address) into the invoice form without
    hand-keying. Vendors are filtered out. Returns up to 10 matches.
    """
    require_feature(request, "invoice")
    term = (q or "").strip()
    if len(term) < 2:
        return {"ok": True, "customers": []}
    try:
        customers = MondayClient().search_customers(term, limit=10)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "CUSTOMER_SEARCH_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Try again, or check Monday connectivity."},
        )
    return {"ok": True, "customers": customers}


@app.get("/ui/api/invoice/drafts")
def ui_invoice_drafts_list(request: Request) -> dict:
    """List the shared, resumable invoice drafts (server copy of the browser
    autosave). Anyone with the `invoice` grant sees the team's drafts so a draft
    started on one machine can be finished or deleted from another."""
    require_feature(request, "invoice")
    try:
        drafts = invoice_drafts.list_drafts()
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    return {"ok": True, "drafts": drafts}


@app.put("/ui/api/invoice/drafts/{draft_id}")
def ui_invoice_draft_upsert(draft_id: str, req: EstimateDraftUpsertRequest, request: Request) -> dict:
    """Create or update one shared invoice draft (reuses the generic draft model)."""
    actor = require_feature(request, "invoice")
    try:
        record, stale = invoice_drafts.upsert_draft(
            draft_id, label=req.label, payload=req.payload,
            updated_at=req.updated_at, actor=actor,
        )
    except invoice_drafts.DraftValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_DRAFT", "detail": str(e),
                    "advice": "This is a client bug — the form sent an invalid draft."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    activity.log_event("invoice.draft.save", actor=actor, target=draft_id,
                       result="stale" if stale else "ok")
    return {"ok": True, "draft": record, "stale": stale}


@app.delete("/ui/api/invoice/drafts/{draft_id}")
def ui_invoice_draft_delete(draft_id: str, request: Request) -> dict:
    """Delete one shared invoice draft (explicit delete, or after a live create)."""
    actor = require_feature(request, "invoice")
    try:
        existed = invoice_drafts.remove_draft(draft_id)
    except invoice_drafts.DraftValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_DRAFT", "detail": str(e),
                    "advice": "Invalid draft id."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    activity.log_event("invoice.draft.delete", actor=actor, target=draft_id,
                       result="ok" if existed else "noop")
    return {"ok": True, "existed": existed}


@app.get("/ui/api/preview")
def ui_preview(identifier: str, request: Request) -> FileResponse:
    """
    Serve the rendered PDF straight from the container's output dir.

    Fallback for when GVC_GCS_PREVIEW_BUCKET isn't configured (e.g. local dev):
    process_one() always writes output/<identifier>.pdf, so the form can show
    it even without the GCS signed-URL path.
    """
    require_ui_access(request)
    if not _SAFE_IDENTIFIER.match(identifier):
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "code": "BAD_IDENTIFIER",
                    "detail": "Invalid identifier.", "advice": "Use the invoice number as entered."},
        )
    pdf = Path(OUTPUT_DIR) / f"{identifier}.pdf"
    if not pdf.exists():
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "code": "PREVIEW_NOT_FOUND",
                    "detail": f"No rendered PDF for {identifier}.",
                    "advice": "Click Generate Preview first."},
        )
    return FileResponse(str(pdf), media_type="application/pdf")


@app.get("/ui/estimate", response_class=HTMLResponse)
def ui_estimate_form(request: Request) -> HTMLResponse:
    """Serve the office-staff estimate form."""
    email = require_feature(request, "estimate")
    activity.log_event("tool.open", actor=email, target="estimate")
    path = WEB_DIR / "estimate.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.post("/ui/api/estimate/run")
def ui_estimate_run(req: EstimateRunRequest, request: Request) -> dict:
    """
    Run the estimate flow for the browser form.

    dry-run renders the PDF (preview); finalize also saves the PDF to the
    project's Drive folder, creates the hello@ Gmail draft, posts the
    #estimates Slack notice, and syncs the Monday Bid Board item.
    No Stripe, ever.
    """
    user_email = require_feature(request, "estimate")
    _est_mode = f"{req.mode}+revise" if req.revise else req.mode
    # The signed-in user is the default preparer (overridable in the form).
    pb = req.data.setdefault("prepared_by", {})
    if not pb.get("email") and "@" in user_email:
        pb["email"] = user_email
    try:
        wb = process_estimate(req.data, OUTPUT_DIR, mode=req.mode,
                              source_label="ui:estimate", revise=req.revise)
        if req.mode == "finalize":
            wb["mode_warning"] = (
                "FINALIZED — the estimate draft is waiting in hello@. Open it, "
                "review, then click Send. Office QA also notified Andrea automatically."
            )
            if not wb.get("gmail_draft_url") and "gmail_status" not in wb:
                wb["gmail_status"] = "No draft URL returned — check hello@ configuration."
            _log_estimate_slack(wb, actor=user_email)
            qa = wb.get("qa") if isinstance(wb.get("qa"), dict) else None
            if qa is not None:
                activity.log_event(
                    "estimate.qa",
                    actor=user_email,
                    result="ok" if qa.get("ok") else "error",
                    target=(wb.get("identifier")
                            or (req.data.get("estimate") or {}).get("identifier")
                            or ""),
                    **activity_detail.summarize("estimate", req.data, wb, mode=_est_mode),
                    qa_ok=bool(qa.get("ok")),
                    qa_summary=(qa.get("summary") or "")[:240],
                )
        activity.log_event(
            "estimate.run", actor=user_email, result=activity_detail.result_for(wb),
            **activity_detail.summarize("estimate", req.data, wb, mode=_est_mode))
        return {"ok": True, "writeback": wb}
    except HTTPException:
        raise
    except Exception as e:
        activity.log_event(
            "estimate.run", actor=user_email, result="error", severity="ERROR",
            **activity_detail.summarize("estimate", req.data, None, mode=_est_mode, error=e))
        print(f"[ui:estimate] error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


@app.get("/ui/api/estimate/lookup")
def ui_estimate_lookup(request: Request, monday_url: str = "") -> dict:
    """
    Prefill the estimate form from a Monday Bid Board item URL (Monday = SoT).
    Read-only: pulls client (linked customer), job/location/scope/project-type,
    and salesperson, and links the estimate to that exact item
    (job.monday_item_id) so finalize writes back to it instead of name-matching.
    """
    require_feature(request, "estimate")
    item_id = _parse_monday_item_id(monday_url)
    if not item_id:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_MONDAY_URL",
                    "detail": "Couldn't find a Monday item id in that value.",
                    "advice": "Paste the bid's Monday URL (it contains /pulses/<id>) "
                              "or just the numeric item id."},
        )
    try:
        prefill = monday_estimate.lookup_bid(MondayClient(), item_id)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001 — bad id / not found / API error → friendly 422
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "MONDAY_LOOKUP_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Check the URL points to a real item on the Bid Board."},
        )

    # ---- Revision path: the deal already has an Estimate #. Try to load the
    # as-sent JSON sidecar from Drive so EVERY field (incl. line items) prefills
    # and the form can offer "Update this Estimate" under the same number.
    # Best-effort: a Drive hiccup or a pre-sidecar estimate degrades to the
    # normal metadata prefill + a manual-line-items note. ----
    existing = prefill.pop("_existing_estimate", None)
    revision: Optional[dict] = None
    if existing and existing.get("number"):
        est_no = existing["number"]
        # Capture the customer the LIVE Monday bid is linked to BEFORE a sidecar
        # merge replaces `prefill` — this is the name the estimate would reuse, and
        # the one to surface in the duplicate-bid guard (a duplicated bid keeps the
        # original's Customer link + Estimate #, so both silently carry over).
        bid_customer = ((prefill.get("client") or {}).get("name") or "").strip()
        revision = {"estimate_number": est_no, "sidecar_found": False,
                    "customer_name": bid_customer}
        try:
            from subsystems.estimate.revision import merge_revision_prefill, sidecar_filename
            from adapters.drive import DriveUploader
            uploader = DriveUploader()
            sidecar = uploader.find_file_anywhere(sidecar_filename(est_no))
            if sidecar:
                original = uploader.download_json(sidecar["id"])
                full = merge_revision_prefill(original, monday_item_id=item_id)
                # Keep the lookup's match context; the sidecar wins on data.
                full["_matched"] = prefill.get("_matched") or {}
                full["_notes"] = []
                prefill = full
                revision["sidecar_found"] = True
                revision["prior_total"] = sum(
                    float(li.get("unit_price") or 0) * float(li.get("quantity") or 1)
                    for li in ((original.get("estimate") or {}).get("line_items") or [])
                    if not li.get("optional")
                )
        except Exception as e:  # noqa: BLE001 — degrade gracefully, never 500 a lookup
            revision["sidecar_error"] = f"{type(e).__name__}: {e}"
            print(f"[ui:estimate-lookup] sidecar load failed (non-fatal): {e}",
                  file=sys.stderr)
        if not revision["sidecar_found"]:
            prefill.setdefault("_notes", []).append(
                f"This deal already has Estimate {est_no}, but its as-sent data "
                "couldn't be loaded (sent before revision tracking) — re-enter "
                "line items to revise it under the same number."
            )

    out = {"ok": True, "prefill": prefill}
    if revision:
        out["revision"] = revision
    return out


@app.get("/ui/api/estimate/search")
def ui_estimate_search(request: Request, q: str = "") -> dict:
    """
    Search the Bid Board by bid name, Estimate #, location (address/city/state),
    or customer — so estimators don't need to remember a Monday id. Returns
    light rows: [{item_id, name, estimate_number, stage, url, …}]. Read-only.
    """
    require_feature(request, "estimate")
    if len((q or "").strip()) < 2:
        return {"ok": True, "results": []}
    try:
        rich = monday_search.search_bids_rich(MondayClient(), q, limit=20)
        results = [{
            "item_id": r.get("item_id"),
            "name": r.get("name") or "",
            "estimate_number": r.get("estimate_number") or "",
            "stage": r.get("stage") or "",
            "location": r.get("location") or "",
            "customer": r.get("customer") or "",
            "url": r.get("url") or "",
            "match_fields": r.get("match_fields") or [],
        } for r in (rich or [])]
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001
        # Fall back to the classic name/Estimate# search if rich search fails.
        try:
            results = monday_estimate.search_bids(MondayClient(), q)
        except Exception as e2:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail={"ok": False, "code": "MONDAY_SEARCH_FAILED",
                        "detail": f"{type(e).__name__}: {e}; fallback: "
                                  f"{type(e2).__name__}: {e2}",
                        "advice": "Try again, or paste the Monday item URL directly."},
            )
    return {"ok": True, "results": results}


@app.get("/ui/api/estimate/salespeople")
def ui_estimate_salespeople(request: Request) -> dict:
    """
    Source for the estimate form's "Bid contact" dropdown: provisioned users who
    can create estimates (anyone whose effective features include `estimate` —
    covers admins with `*`). Returns [{name, email, phone}] sorted by name.
    Manual entry stays available in the form for one-offs.
    """
    require_feature(request, "estimate")
    out: list[dict] = []
    try:
        users = portal_store.list_users()
    except portal_store.PortalStoreNotConfigured:
        users = {}
    except Exception:  # noqa: BLE001 — never break the form over a store hiccup
        users = {}
    for email, rec in users.items():
        if "estimate" not in access.effective_features(email):
            continue
        person = rec.get("person") or {}
        out.append({
            "name": person.get("name") or "",
            "email": email,
            "phone": person.get("phone") or "",
        })

    # Company account (team-bonus-pool salesperson). Selecting it puts the
    # company's bid-contact info on the estimate AND routes the commission to
    # "Green Valley Contractors" on the bid (Commission Recipient col).
    # Lets Jordan run a lead without taking personal commission. Env-overridable.
    company = {
        "name": os.environ.get("GVC_COMPANY_SALESPERSON_NAME", "Green Valley Contractors"),
        "email": os.environ.get("GVC_COMPANY_SALESPERSON_EMAIL", "hello@greenvalleycontractors.com"),
        "phone": os.environ.get("GVC_COMPANY_SALESPERSON_PHONE", "(513) 912-2235"),
        "company_account": True,
    }
    if company["name"] and not any((p.get("email") or "").lower() == company["email"].lower() for p in out):
        out.append(company)

    out.sort(key=lambda p: (p["name"] or p["email"]).lower())
    return {"ok": True, "salespeople": out}


def _store_unconfigured(e: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"ok": False, "code": "STORE_NOT_CONFIGURED", "detail": str(e),
                "advice": "Ask an admin to set GVC_PORTAL_STATE_BUCKET (or reuse "
                          "GVC_GCS_PREVIEW_BUCKET) + the service-account JSON."},
    )


@app.get("/ui/api/estimate/drafts")
def ui_estimate_drafts_list(request: Request) -> dict:
    """List the shared, resumable estimate drafts. Anyone with the `estimate`
    grant sees the whole team's drafts (so a draft started on one machine can be
    finished or deleted from another). The browser also keeps a localStorage
    copy so nothing is lost on a session timeout or dropped connection."""
    require_feature(request, "estimate")
    try:
        drafts = estimate_drafts.list_drafts()
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    return {"ok": True, "drafts": drafts}


@app.put("/ui/api/estimate/drafts/{draft_id}")
def ui_estimate_draft_upsert(draft_id: str, req: EstimateDraftUpsertRequest, request: Request) -> dict:
    """Create or update one shared draft (server copy of the browser autosave)."""
    actor = require_feature(request, "estimate")
    try:
        record, stale = estimate_drafts.upsert_draft(
            draft_id, label=req.label, payload=req.payload,
            updated_at=req.updated_at, actor=actor,
        )
    except estimate_drafts.DraftValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_DRAFT", "detail": str(e),
                    "advice": "This is a client bug — the form sent an invalid draft."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    activity.log_event("estimate.draft.save", actor=actor, target=draft_id,
                       result="stale" if stale else "ok")
    return {"ok": True, "draft": record, "stale": stale}


@app.delete("/ui/api/estimate/drafts/{draft_id}")
def ui_estimate_draft_delete(draft_id: str, request: Request) -> dict:
    """Delete one shared draft (on explicit delete, or after a successful finalize)."""
    actor = require_feature(request, "estimate")
    try:
        existed = estimate_drafts.remove_draft(draft_id)
    except estimate_drafts.DraftValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_DRAFT", "detail": str(e),
                    "advice": "Invalid draft id."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    activity.log_event("estimate.draft.delete", actor=actor, target=draft_id,
                       result="ok" if existed else "noop")
    return {"ok": True, "existed": existed}


@app.get("/ui/api/estimate/scopes")
def ui_estimate_scopes(request: Request) -> dict:
    """
    The standard-scope catalog that powers the estimate form's Scope Selection
    section (checkboxes + default scope text/price) and the PDF's Additional
    Services page. Any estimate user reads it; only admins may edit it.
    Never fails on a missing store — falls back to the shipped default catalog.
    """
    email = require_feature(request, "estimate")
    return {"ok": True,
            "catalog": scope_catalog.load_catalog(),
            "info": scope_catalog.catalog_info(),
            "can_manage": access.has_feature(email, "admin")}


@app.post("/ui/api/estimate/scopes")
def ui_estimate_scopes_save(req: EstimateScopeCatalogRequest, request: Request) -> dict:
    """
    Replace the standard-scope catalog (admin only). Validated + normalized,
    then stored in the portal state bucket; the bucket's object versioning
    keeps every prior catalog recoverable.
    """
    admin_email = require_admin(request)
    try:
        catalog = scope_catalog.put_catalog(req.catalog, actor=admin_email)
    except scope_catalog.ScopeCatalogInvalid as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "SCOPE_CATALOG_INVALID", "detail": str(e),
                    "advice": "Every trade needs a name and at least one scope; "
                              "every scope needs a title."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "SCOPE_STORE_NOT_CONFIGURED",
                    "detail": str(e),
                    "advice": "Ask an admin — the portal state bucket isn't configured."},
        )
    n_trades, n_scopes = scope_catalog.catalog_counts(catalog)
    activity.log_event("estimate.scopes.save", actor=admin_email,
                       target=f"{n_trades} trades / {n_scopes} scopes", result="ok")
    return {"ok": True, "catalog": catalog, "info": scope_catalog.catalog_info()}


@app.get("/ui/timeoff", response_class=HTMLResponse)
def ui_timeoff(request: Request) -> HTMLResponse:
    """
    Time-off page: embeds the company Google Form. Everyone provisioned holds the
    `timeoff` baseline grant, so all signed-in employees can reach it. The form
    URL is config (GVC_TIMEOFF_FORM_URL) so an admin can set/rotate it without a code
    change; use the Form's 'Send → <> embed' URL.
    """
    email = require_feature(request, "timeoff")
    activity.log_event("tool.open", actor=email, target="timeoff")
    path = WEB_DIR / "timeoff.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    form_url = (os.environ.get("GVC_TIMEOFF_FORM_URL") or "").strip()
    if form_url.startswith("https://"):
        body = (f'<iframe class="form" src="{html_escape(form_url)}" '
                f'title="Time Off Request" loading="lazy"></iframe>')
    else:
        body = (
            '<div class="notice"><h2>Time Off form not configured yet</h2>'
            '<p class="muted">The form link hasn\'t been set on the service. '
            'Ask an admin to set <code>GVC_TIMEOFF_FORM_URL</code> to the Google Form '
            'embed URL.</p></div>'
        )
    html = path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)).replace("{{FORM_IFRAME}}", body)
    return HTMLResponse(html)


@app.get("/ui/fieldguide", response_class=HTMLResponse)
def ui_fieldguide(request: Request) -> HTMLResponse:
    """
    Field Manual: GVC's own production procedures (framing, drywall hang/scrape/
    finish, acoustical ceilings, patch, touch-up, stocking). Static page, no
    external calls — the detail-level toggle and the checklist state live in the
    browser's localStorage, so nothing is written server-side.

    `fieldguide` is a BASELINE grant, so every signed-in employee reaches it
    without an admin having to provision anything. It carries no customer or
    financial data, and crew need it in one tap from a phone.
    """
    email = require_feature(request, "fieldguide")
    activity.log_event("tool.open", actor=email, target="fieldguide")
    path = WEB_DIR / "fieldguide.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    html = path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email))
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Field Manual CHECKLIST RUNS — a crew member starts a procedure checklist
# against a specific job, works it, and anyone on the crew can resume it.
#
# Gated by `fieldguide`, which is a BASELINE grant — so every signed-in employee
# can start and resume runs with no provisioning. That deliberately also exposes
# the active-job list (names/addresses) to everyone signed in; it is not
# confidential information and crews need it to pick their job. Flagged for
# Jordan rather than assumed: if that should be narrower, gate the /jobs route
# on `jobcheck` instead and have crew pick from a text field.
#
# NO MONDAY WRITEBACK by design — Job Check remains the only writer of the
# Projects-board stage columns. See subsystems/fieldguide/runs.py.
# ---------------------------------------------------------------------------

@app.get("/ui/api/fieldguide/runs")
def ui_fieldguide_runs_list(request: Request, procedure: Optional[str] = None,
                            include_done: bool = False) -> dict:
    """List shared, resumable checklist runs. Optionally filtered to one
    procedure. Completed runs are hidden unless include_done=true."""
    require_feature(request, "fieldguide")
    try:
        runs = fieldguide_runs.list_runs(procedure=procedure, include_done=include_done)
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    return {"ok": True, "runs": runs}


@app.get("/ui/api/fieldguide/runs/{run_id}")
def ui_fieldguide_run_get(run_id: str, request: Request) -> dict:
    """Fetch one run in full (including its checked step keys) so another device
    can resume exactly where the last one left off."""
    require_feature(request, "fieldguide")
    try:
        record = fieldguide_runs.get_run(run_id)
    except fieldguide_runs.RunValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_RUN", "detail": str(e),
                    "advice": "Invalid run id."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "code": "RUN_NOT_FOUND",
                    "detail": f"No checklist run {run_id}.",
                    "advice": "It may have been finished and cleared. Start a new one."},
        )
    return {"ok": True, "run": record}


@app.put("/ui/api/fieldguide/runs/{run_id}")
def ui_fieldguide_run_upsert(run_id: str, req: FieldGuideRunUpsertRequest,
                             request: Request) -> dict:
    """Create or update one run (server copy of the browser's autosave).

    A write whose updated_at is older than what's stored comes back
    `stale: true` and is DROPPED — a phone that was offline for an hour must not
    roll back a run somebody else has since advanced."""
    actor = require_feature(request, "fieldguide")
    try:
        record, stale = fieldguide_runs.upsert_run(
            run_id, payload=req.payload, updated_at=req.updated_at, actor=actor,
        )
    except fieldguide_runs.RunValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_RUN", "detail": str(e),
                    "advice": "This is a client bug — the page sent an invalid run."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    activity.log_event("fieldguide.run.save", actor=actor,
                       target=f"{record.get('procedure')}/{run_id}",
                       result="stale" if stale else "ok")
    return {"ok": True, "run": record, "stale": stale}


@app.delete("/ui/api/fieldguide/runs/{run_id}")
def ui_fieldguide_run_delete(run_id: str, request: Request) -> dict:
    """Delete one run. Used by an explicit delete from the resume list."""
    actor = require_feature(request, "fieldguide")
    try:
        existed = fieldguide_runs.remove_run(run_id)
    except fieldguide_runs.RunValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_RUN", "detail": str(e),
                    "advice": "Invalid run id."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    activity.log_event("fieldguide.run.delete", actor=actor, target=run_id,
                       result="ok" if existed else "noop")
    return {"ok": True, "existed": existed}


@app.get("/ui/api/fieldguide/jobs")
def ui_fieldguide_jobs(request: Request) -> dict:
    """Active Monday jobs, for attaching a checklist run to a job. Reuses the
    Job Check reader verbatim so the two tools always show the same job list."""
    require_feature(request, "fieldguide")
    return jobcheck_flow.list_active_jobs()


# ---------------------------------------------------------------------------
# Change Order routes — standalone CO program. Gated by the `estimate` grant
# (a CO is estimate-adjacent; Jake already has estimate). Mirrors /ui/estimate:
# dry-run renders the PDF; finalize files it to Drive, drafts in hello@, posts
# #change-orders, and creates the Monday CO subitem (Status=Drafted).
# ---------------------------------------------------------------------------

_MONDAY_ITEM_ID_RE = re.compile(r"(?:pulses|items)/(\d+)")


def _parse_monday_item_id(value: str) -> Optional[int]:
    """Extract a Monday item id from a board URL or accept a bare numeric id."""
    s = (value or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    m = _MONDAY_ITEM_ID_RE.search(s)
    return int(m.group(1)) if m else None


@app.get("/ui/change-order", response_class=HTMLResponse)
def ui_change_order_form(request: Request) -> HTMLResponse:
    """Serve the office-staff Change Order form."""
    email = require_feature(request, "change_order")
    activity.log_event("tool.open", actor=email, target="change-order")
    path = WEB_DIR / "change-order.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


@app.get("/ui/api/change-order/lookup")
def ui_change_order_lookup(request: Request, monday_url: str = "") -> dict:
    """
    Autofill helper: given a Monday Project item URL (or id), return the
    client/job/site/estimate#/Drive-folder context + existing CO identifiers
    (incl. `existing_cos` — the job's CO items with their status, so the form
    can offer "Load for revision"), so the form fills itself from the single
    source of truth. Read-only.
    """
    require_feature(request, "change_order")
    item_id = _parse_monday_item_id(monday_url)
    if not item_id:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_MONDAY_URL",
                    "detail": "Couldn't find a Monday item id in that value.",
                    "advice": "Paste the Project item's URL (it contains /pulses/<id>) "
                              "or just the numeric item id."},
        )
    try:
        ctx = monday_co.get_project_context(MondayClient(), item_id)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001 — item not found / API error → friendly 422
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "MONDAY_LOOKUP_FAILED", "detail": f"{type(e).__name__}: {e}",
                    "advice": "Check the Project URL points to a real item on the Projects board, "
                              "or use the Drive folder backup."},
        )
    return {"ok": True, "context": ctx}


@app.get("/ui/api/change-order/search")
def ui_change_order_search(request: Request, q: str = "") -> dict:
    """
    "Find the Project" text search: the Projects board by item name OR the
    Project # column (which also matches CO ids). Returns light rows:
    [{item_id, name, group, project_number, url}]. Read-only.
    """
    require_feature(request, "change_order")
    if len((q or "").strip()) < 2:
        return {"ok": True, "results": []}
    try:
        results = monday_co.search_projects(MondayClient(), q)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to set MONDAY_API_TOKEN."},
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "MONDAY_SEARCH_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Try again, or paste the Monday item URL directly."},
        )
    return {"ok": True, "results": results}


@app.get("/ui/api/change-order/original")
def ui_change_order_original(request: Request, identifier: str = "") -> dict:
    """
    Revision helper: load the as-sent JSON sidecar for a previously finalized
    CO (by its CO number) so the form can prefill EVERY field including the
    breakdown, mirroring the estimate revision lookup. Read-only. A pre-
    sidecar CO (finalized before this shipped) degrades gracefully —
    sidecar_found=False, the caller falls back to context prefill + re-enters
    the breakdown.
    """
    require_feature(request, "change_order")
    identifier = (identifier or "").strip()
    if not identifier:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_CO_IDENTIFIER", "detail": "identifier is required.",
                    "advice": "Pass the CO number, e.g. CO.1-2026-0615-002."},
        )
    revision: dict = {"co_number": identifier, "sidecar_found": False}
    prefill: Optional[dict] = None
    try:
        from subsystems.change_order.revision import (
            merge_revision_prefill, prior_total, sidecar_filename,
        )
        from adapters.drive import DriveUploader
        uploader = DriveUploader()
        sidecar = uploader.find_file_anywhere(sidecar_filename(identifier))
        if sidecar:
            original = uploader.download_json(sidecar["id"])
            prefill = merge_revision_prefill(original)
            revision["sidecar_found"] = True
            revision["prior_total"] = prior_total(original)
    except Exception as e:  # noqa: BLE001 — degrade gracefully, never 500 a lookup
        revision["sidecar_error"] = f"{type(e).__name__}: {e}"
        print(f"[ui:change-order-original] sidecar load failed (non-fatal): {e}",
              file=sys.stderr)
    return {"ok": True, "revision": revision, "prefill": prefill}


@app.get("/ui/api/change-order/drafts")
def ui_change_order_drafts_list(request: Request) -> dict:
    """List the shared, resumable change-order drafts. Anyone with the
    `change_order` grant sees the whole team's drafts (so a draft started on
    one machine can be finished or deleted from another)."""
    require_feature(request, "change_order")
    try:
        drafts = co_drafts.list_drafts()
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    return {"ok": True, "drafts": drafts}


@app.put("/ui/api/change-order/drafts/{draft_id}")
def ui_change_order_draft_upsert(draft_id: str, req: ChangeOrderDraftUpsertRequest, request: Request) -> dict:
    """Create or update one shared draft (server copy of the browser autosave)."""
    actor = require_feature(request, "change_order")
    try:
        record, stale = co_drafts.upsert_draft(
            draft_id, label=req.label, payload=req.payload,
            updated_at=req.updated_at, actor=actor,
        )
    except co_drafts.DraftValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_DRAFT", "detail": str(e),
                    "advice": "This is a client bug — the form sent an invalid draft."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    activity.log_event("change_order.draft.save", actor=actor, target=draft_id,
                       result="stale" if stale else "ok")
    return {"ok": True, "draft": record, "stale": stale}


@app.delete("/ui/api/change-order/drafts/{draft_id}")
def ui_change_order_draft_delete(draft_id: str, request: Request) -> dict:
    """Delete one shared draft (on explicit delete, or after a successful finalize)."""
    actor = require_feature(request, "change_order")
    try:
        existed = co_drafts.remove_draft(draft_id)
    except co_drafts.DraftValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_DRAFT", "detail": str(e),
                    "advice": "Invalid draft id."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _store_unconfigured(e)
    activity.log_event("change_order.draft.delete", actor=actor, target=draft_id,
                       result="ok" if existed else "noop")
    return {"ok": True, "existed": existed}


@app.post("/ui/api/change-order/run")
def ui_change_order_run(req: ChangeOrderRunRequest, request: Request) -> dict:
    """
    Run the CO flow for the browser form. dry-run renders the PDF; finalize
    files it to Drive, drafts in hello@, posts #change-orders, and
    creates/updates the Monday CO item + Operations task. No Stripe, ever.
    """
    user_email = require_feature(request, "change_order")
    _co_mode = f"{req.mode}+revise" if req.revise else req.mode
    pb = req.data.setdefault("prepared_by", {})
    if not pb.get("email") and "@" in user_email:
        pb["email"] = user_email
    try:
        wb = change_order_flow.process_change_order(
            req.data, OUTPUT_DIR, mode=req.mode, source_label="ui:change-order",
            revise=req.revise)
        if req.mode == "finalize":
            wb["mode_warning"] = (
                "FINALIZED — the change order draft is waiting in hello@. Open it, "
                "review, then click Send."
            )
            if not wb.get("gmail_draft_url") and "gmail_status" not in wb:
                wb["gmail_status"] = "No draft URL returned — check hello@ configuration."
        activity.log_event(
            "change_order.run", actor=user_email, result=activity_detail.result_for(wb),
            **activity_detail.summarize("change_order", req.data, wb, mode=_co_mode))
        return {"ok": True, "writeback": wb}
    except HTTPException:
        raise
    except Exception as e:
        activity.log_event(
            "change_order.run", actor=user_email, result="error", severity="ERROR",
            **activity_detail.summarize("change_order", req.data, None, mode=_co_mode, error=e))
        print(f"[ui:change-order] error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


# ---------------------------------------------------------------------------
# COI Generator routes. Gated by the `coi` grant. Mirrors /ui/estimate:
# dry-run stamps the certificate-holder box onto the stored blank + previews;
# finalize files to Drive ("COIs Sent/<year>/"), drafts in hello@, posts the
# GVC_COI_SLACK_CHANNEL notice, and logs to Monday (placeholder). Template
# management (the annually-renewed blank) is admin-only.
# ---------------------------------------------------------------------------

@app.get("/ui/coi", response_class=HTMLResponse)
def ui_coi_form(request: Request) -> HTMLResponse:
    """Serve the office-staff COI Generator form."""
    email = require_feature(request, "coi")
    activity.log_event("tool.open", actor=email, target="coi")
    path = WEB_DIR / "coi.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


def _coi_template_unavailable(e: Exception) -> HTTPException:
    if isinstance(e, coi_template.CoiTemplateMissing):
        return HTTPException(
            status_code=503,
            detail={"ok": False, "code": "COI_TEMPLATE_MISSING", "detail": str(e),
                    "advice": "An admin needs to upload the current agent-issued "
                              "blank COI in the Template section of the COI page."},
        )
    return HTTPException(
        status_code=503,
        detail={"ok": False, "code": "COI_STORE_NOT_CONFIGURED", "detail": str(e),
                "advice": "Ask an admin — the portal state bucket isn't configured."},
    )


@app.post("/ui/api/coi/run")
def ui_coi_run(req: CoiRunRequest, request: Request) -> dict:
    """
    Run the COI flow for the browser form. dry-run stamps + previews;
    finalize files to Drive, drafts in hello@, posts Slack, logs to Monday
    (placeholder). Nothing is emailed until a human reviews the draft.
    """
    user_email = require_feature(request, "coi")
    try:
        wb = coi_flow.process_coi(req.data, OUTPUT_DIR, mode=req.mode,
                                  source_label="ui:coi")
        if req.mode == "finalize":
            wb["mode_warning"] = (
                "FINALIZED — the COI draft is waiting in hello@. Open it, "
                "review, then click Send."
            )
            if not wb.get("gmail_draft_url") and "gmail_status" not in wb:
                wb["gmail_status"] = "No draft URL returned — check hello@ configuration."
        activity.log_event(
            "coi.run", actor=user_email, result=activity_detail.result_for(wb),
            **activity_detail.summarize("coi", req.data, wb, mode=req.mode))
        return {"ok": True, "writeback": wb}
    except (coi_template.CoiTemplateMissing, portal_store.PortalStoreNotConfigured) as e:
        raise _coi_template_unavailable(e)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "INVALID_INPUT",
                    "detail": humanize_validation_message(str(e)),
                    "advice": "Fix the highlighted field and try again."},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ui:coi] error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


@app.post("/ui/api/coi/bulk/run")
def ui_coi_bulk_run(req: CoiBulkRunRequest, request: Request) -> dict:
    """
    Bulk COI run over the Annual COI List sheet. dry-run parses + reviews
    (no writes); finalize processes ONE CHUNK of ready rows (stamp → Drive →
    hello@ draft → YES/NO written to the Sent column) and returns a cursor —
    the page loops until remaining == 0, so long batches never hit the
    request timeout and interrupted runs resume cleanly (YES rows skip).
    """
    user_email = require_feature(request, "coi")
    activity.log_event("coi.bulk.run", actor=user_email,
                       target=f"{req.mode} after_row={req.after_row}")
    try:
        out = coi_flow.process_coi_bulk(
            req.sheet_url, OUTPUT_DIR, mode=req.mode,
            source_label="ui:coi-bulk", after_row=req.after_row,
            chunk=req.chunk)
        key = "review" if req.mode == "dry-run" else "batch"
        return {"ok": True, key: out}
    except (coi_template.CoiTemplateMissing, portal_store.PortalStoreNotConfigured) as e:
        raise _coi_template_unavailable(e)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "INVALID_INPUT", "detail": str(e),
                    "advice": str(e)},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ui:coi-bulk] error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        if type(e).__name__ == "SheetsNotConfigured":
            status, code = 503, "SHEETS_NOT_CONFIGURED"
            detail, advice = str(e), "Ask an admin — the service-account JSON isn't mounted."
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


@app.get("/ui/api/coi/template")
def ui_coi_template_info(request: Request) -> dict:
    """Current blank-template metadata. Readable by `coi` holders (the
    generator page shows status) AND `admin` holders (the Admin page hosts
    the replace UI). `template` is null when nothing has been uploaded yet."""
    email = require_ui_access(request)
    feats = access.effective_features(email)
    if "coi" not in feats and "admin" not in feats:
        raise HTTPException(
            status_code=403,
            detail={"ok": False, "code": "FEATURE_NOT_GRANTED",
                    "detail": "Your account isn't granted 'coi' or 'admin'.",
                    "advice": "Ask an admin to grant you access on the Admin page."},
        )
    try:
        info = coi_template.template_info()
    except portal_store.PortalStoreNotConfigured as e:
        raise _coi_template_unavailable(e)
    return {"ok": True, "template": info,
            "can_manage": access.has_feature(email, "admin")}


@app.post("/ui/api/coi/template")
async def ui_coi_template_upload(
    request: Request,
    file: UploadFile = File(...),
    expiry_label: str = Form(""),
) -> dict:
    """
    Replace the stored blank COI (admin only) — the annual-renewal path.
    Validates + normalizes the PDF before storing; the state bucket's object
    versioning keeps every prior template recoverable.
    """
    admin_email = require_admin(request)
    data = await file.read()
    try:
        meta = coi_template.put_template(
            data, expiry_label=expiry_label, actor=admin_email)
    except coi_template.CoiTemplateInvalid as e:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "COI_TEMPLATE_INVALID", "detail": str(e),
                    "advice": "Upload the agent-issued blank COI as a PDF."},
        )
    except portal_store.PortalStoreNotConfigured as e:
        raise _coi_template_unavailable(e)
    activity.log_event("coi.template.upload", actor=admin_email,
                       target=meta.get("expiry_label") or "(no label)",
                       result="ok")
    return {"ok": True, "template": meta}


# ---------------------------------------------------------------------------
# Lien Watch routes (P1). Gated by the `lien` grant. Mirrors /ui/estimate's
# page pattern. READ-ONLY against Monday: the status page lists every active
# job's notice/lien/retainage deadlines from shared/lien_rules.json. Slack
# alerts are BUILT DARK in orchestrators/lien_flow.py behind
# GVC_LIEN_ALERTS_ENABLED (only Jordan enables it) — deliberately NO route,
# NO scheduler, NO wiring here.
# ---------------------------------------------------------------------------

@app.get("/ui/lien", response_class=HTMLResponse)
def ui_lien_page(request: Request) -> HTMLResponse:
    """Serve the Lien Watch status page."""
    email = require_feature(request, "lien")
    activity.log_event("tool.open", actor=email, target="lien")
    path = WEB_DIR / "lien.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


@app.get("/ui/api/lien/status")
def ui_lien_status(request: Request) -> dict:
    """
    The full tracker payload: every active Projects-board job with its state,
    first-furnishing basis, and computed deadline set, most-urgent first.
    Read-only sweep — one Monday board read per call, no writebacks.
    """
    email = require_feature(request, "lien")
    activity.log_event("lien.status", actor=email, target="tracker")
    try:
        return lien_flow.build_tracker()
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:lien] error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Morning Brief — employee daily control center + GM huddle + Owner Pulse
# (docs/MORNING_BRIEF_BUILD_SPEC.md). `morning` is baseline; morning_gm /
# morning_owner / morning_ops are role grants in /ui/admin.
# ---------------------------------------------------------------------------

class MorningPrepRequest(BaseModel):
    criterion_id: str
    done: bool = True
    note: Optional[str] = None


class MorningOriginRequest(BaseModel):
    kind: str
    label: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class MorningRouteRequest(BaseModel):
    stops: list = Field(default_factory=list)
    optimized: bool = False
    mark_override: bool = False


class MorningCompleteStopRequest(BaseModel):
    item_id: int
    note: Optional[str] = None
    post_monday: bool = True


class MorningActionRequestBody(BaseModel):
    needed_from_email: str
    category: str = "other"
    need: str
    trade_subtype: Optional[str] = None
    project_item_id: Optional[int] = None
    project_name: Optional[str] = None
    due_at: Optional[str] = None


class MorningNotesRequest(BaseModel):
    text: str = ""


class MorningMeetingActionRequest(BaseModel):
    text: str
    owner: str
    due: Optional[str] = None


class MorningMeetingParkingRequest(BaseModel):
    topic: str
    owner: str
    follow_up: Optional[str] = None


def _serve_morning_html(name: str, email: str) -> HTMLResponse:
    path = WEB_DIR / name
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


@app.get("/ui/morning", response_class=HTMLResponse)
def ui_morning_page(request: Request) -> HTMLResponse:
    email = require_feature(request, "morning")
    activity.log_event("tool.open", actor=email, target="morning")
    return _serve_morning_html("morning.html", email)


@app.get("/ui/morning/gm", response_class=HTMLResponse)
@app.get("/ui/morning-gm", response_class=HTMLResponse)
def ui_morning_gm_page(request: Request) -> HTMLResponse:
    email = require_feature(request, "morning_gm")
    activity.log_event("tool.open", actor=email, target="morning_gm")
    return _serve_morning_html("morning-gm.html", email)


@app.get("/ui/morning/owner", response_class=HTMLResponse)
@app.get("/ui/morning-owner", response_class=HTMLResponse)
def ui_morning_owner_page(request: Request) -> HTMLResponse:
    email = require_feature(request, "morning_owner")
    # Superadmins also reach Owner Pulse via morning_role — allow morning feature
    # if they hold owner flag through superadmin even without explicit grant.
    from shared.access import morning_role
    if not morning_role(email).get("is_owner"):
        require_feature(request, "morning_owner")
    activity.log_event("tool.open", actor=email, target="morning_owner")
    return _serve_morning_html("morning-owner.html", email)


@app.get("/ui/api/morning/brief")
def ui_morning_brief(request: Request) -> dict:
    email = require_feature(request, "morning")
    try:
        return morning_flow.build_employee_brief(email)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Set GVC_MONDAY_TOKEN (or MONDAY_API_TOKEN) on the service."},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ui:morning] brief error: {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "MORNING_BRIEF_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Check Cloud Run logs for [morning] / Monday errors."},
        )


@app.get("/ui/api/morning/hub")
def ui_morning_hub(request: Request) -> dict:
    email = require_feature(request, "morning")
    try:
        return morning_flow.hub_summary(email)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Set GVC_MONDAY_TOKEN (or MONDAY_API_TOKEN) on the service."},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ui:morning] hub error: {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "MORNING_HUB_FAILED",
                    "detail": f"{type(e).__name__}: {e}",
                    "advice": "Check Cloud Run logs for [morning] / Monday errors."},
        )


@app.get("/ui/api/morning/gm")
def ui_morning_gm_api(request: Request) -> dict:
    email = require_feature(request, "morning_gm")
    try:
        out = morning_flow.build_gm_view(email)
        if not out.get("ok"):
            raise HTTPException(status_code=403, detail=out)
        return out
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "MORNING_GM_FAILED",
                    "detail": f"{type(e).__name__}: {e}"},
        )


@app.get("/ui/api/morning/owner")
def ui_morning_owner_api(request: Request) -> dict:
    email = require_feature(request, "morning")
    from shared.access import morning_role
    if not morning_role(email).get("is_owner"):
        require_feature(request, "morning_owner")
    try:
        out = morning_flow.build_owner_pulse(email)
        if not out.get("ok", True) and out.get("code") == "FORBIDDEN":
            raise HTTPException(status_code=403, detail=out)
        return out
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "MORNING_OWNER_FAILED",
                    "detail": f"{type(e).__name__}: {e}"},
        )


@app.post("/ui/api/morning/prep")
def ui_morning_prep(request: Request, body: MorningPrepRequest) -> dict:
    email = require_feature(request, "morning")
    try:
        out = morning_flow.set_prep_criterion(
            email, body.criterion_id, done=body.done, note=body.note)
        activity.log_event("morning.prep", actor=email,
                           target=body.criterion_id, result="ok")
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"ok": False, "detail": str(e)})


@app.post("/ui/api/morning/origin")
def ui_morning_origin(request: Request, body: MorningOriginRequest) -> dict:
    email = require_feature(request, "morning")
    try:
        return morning_flow.save_origin(email, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"ok": False, "detail": str(e)})


@app.post("/ui/api/morning/route")
def ui_morning_route(request: Request, body: MorningRouteRequest) -> dict:
    email = require_feature(request, "morning")
    return morning_flow.save_stops(
        email, body.stops, optimized=body.optimized,
        mark_override=body.mark_override)


@app.post("/ui/api/morning/route/optimize")
def ui_morning_route_optimize(request: Request, body: MorningRouteRequest) -> dict:
    email = require_feature(request, "morning")
    return morning_flow.optimize_stops(email, body.stops or None)


@app.post("/ui/api/morning/route/complete")
def ui_morning_route_complete(request: Request, body: MorningCompleteStopRequest) -> dict:
    email = require_feature(request, "morning")
    try:
        out = morning_flow.complete_stop(
            email, body.item_id, note=body.note, post_monday=body.post_monday)
        activity.log_event("morning.stop_complete", actor=email,
                           target=str(body.item_id), result="ok")
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"ok": False, "detail": str(e)})


@app.post("/ui/api/morning/action-requests")
def ui_morning_ar_create(request: Request, body: MorningActionRequestBody) -> dict:
    email = require_feature(request, "morning")
    try:
        out = morning_flow.create_action_request(email, body.model_dump())
        activity.log_event("morning.ar_create", actor=email,
                           target=body.needed_from_email, result="ok")
        return out
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"ok": False, "detail": str(e)})


@app.post("/ui/api/morning/action-requests/{request_id}/ack")
def ui_morning_ar_ack(request: Request, request_id: str) -> dict:
    email = require_feature(request, "morning")
    try:
        return morning_flow.ack_action_request(email, request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"ok": False, "detail": "not found"})


@app.post("/ui/api/morning/action-requests/{request_id}/complete")
def ui_morning_ar_complete(request: Request, request_id: str) -> dict:
    email = require_feature(request, "morning")
    try:
        return morning_flow.complete_action_request(email, request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"ok": False, "detail": "not found"})


@app.put("/ui/api/morning/notes")
def ui_morning_notes(request: Request, body: MorningNotesRequest) -> dict:
    email = require_feature(request, "morning")
    from subsystems.morning import personal as personal
    return {"ok": True, **personal.set_notes(email, body.text)}


@app.post("/ui/api/morning/update")
async def ui_morning_update(request: Request) -> dict:
    """Multipart: item_id, note, files — Drive Pictures + Monday update."""
    email = require_feature(request, "morning")
    form = await request.form()
    try:
        item_id = int(form.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    if not item_id:
        raise HTTPException(status_code=422, detail={"ok": False, "detail": "item_id required"})
    note = str(form.get("note") or "")
    files = []
    uploads = []
    if hasattr(form, "getlist"):
        uploads = list(form.getlist("files") or [])
    if not uploads:
        for key in form.keys():
            if key in ("item_id", "note"):
                continue
            for val in (form.getlist(key) if hasattr(form, "getlist") else [form.get(key)]):
                if val is not None:
                    uploads.append(val)
    for val in uploads:
        if not hasattr(val, "read"):
            continue
        raw = await val.read()
        if not raw:
            continue
        files.append((
            getattr(val, "filename", None) or "photo.jpg",
            raw,
            getattr(val, "content_type", None) or "image/jpeg",
        ))
    try:
        out = morning_flow.add_project_update(email, item_id, note=note, files=files or None)
        activity.log_event(
            "morning.update", actor=email, target=str(item_id),
            result="ok" if not out.get("warning") else "partial",
            detail=f"photos={out.get('photos_uploaded', 0)}/{out.get('photos_requested', 0)}",
        )
        return out
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "code": "MORNING_UPDATE_FAILED",
                    "detail": f"{type(e).__name__}: {e}"},
        )



@app.post("/ui/api/morning/meeting/start")
def ui_morning_meeting_start(request: Request) -> dict:
    email = require_feature(request, "morning_gm")
    from subsystems.morning import meeting as meet
    from datetime import datetime
    from zoneinfo import ZoneInfo
    workdate = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    return {"ok": True, "meeting": meet.start(workdate, email)}


@app.post("/ui/api/morning/meeting/end")
def ui_morning_meeting_end(request: Request) -> dict:
    email = require_feature(request, "morning_gm")
    from subsystems.morning import meeting as meet
    from datetime import datetime
    from zoneinfo import ZoneInfo
    workdate = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    activity.log_event("morning.meeting_end", actor=email, target=workdate, result="ok")
    return {"ok": True, "meeting": meet.end(workdate)}


@app.post("/ui/api/morning/meeting/parking")
def ui_morning_meeting_parking(request: Request, body: MorningMeetingParkingRequest) -> dict:
    require_feature(request, "morning_gm")
    from subsystems.morning import meeting as meet
    from datetime import datetime
    from zoneinfo import ZoneInfo
    workdate = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    return {"ok": True, "meeting": meet.add_parking(
        workdate, topic=body.topic, owner=body.owner, follow_up=body.follow_up)}


@app.post("/ui/api/morning/meeting/action")
def ui_morning_meeting_action(request: Request, body: MorningMeetingActionRequest) -> dict:
    require_feature(request, "morning_gm")
    from subsystems.morning import meeting as meet
    from datetime import datetime
    from zoneinfo import ZoneInfo
    workdate = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    return {"ok": True, "meeting": meet.add_action(
        workdate, text=body.text, owner=body.owner, due=body.due)}


@app.post("/v1/tasks/morning-prep-cutoff")
def v1_morning_prep_cutoff(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Cloud Scheduler ~6:50 AM ET — miss streaks + AR escalations."""
    require_api_key(x_api_key)
    return morning_flow.run_prep_cutoff_sweep()


# ---------------------------------------------------------------------------

@app.post("/ui/api/monday/warm")
def ui_monday_warm(request: Request) -> dict:
    """
    Hub-triggered warm of Monday list caches. Any signed-in portal user can
    kick this; the hub fires it after tile filtering so Job Start / Morning /
    Job Check open on last-known data. Returns immediately.
    """
    require_ui_access(request)
    threading.Thread(
        target=_warm_monday_caches, name="ui-warm-monday", daemon=True
    ).start()
    return {"ok": True, "started": True}


# Job Check routes — the portal's FIRST Monday write surface (designed
# 2026-07-27, docs/portal-job-check-design.md). Gated by the `jobcheck`
# grant. Reads mirror the lien fetch; the ONE write is the explicit POST
# below — user-tap only, allowlisted columns only (shared/boards.py
# JOBCHECK_COLUMNS with hard-excluded money/link/relation types re-checked
# server-side), column updates on existing items only (never create/delete).
# Every save is audit-logged old→new to the activity store.
# ---------------------------------------------------------------------------

class JobCheckSaveRequest(BaseModel):
    values: dict = Field(
        ...,
        description="{column_id: raw value} — Job Check allowlisted columns "
                    "only; anything else is rejected per-column.",
    )


@app.get("/ui/jobcheck", response_class=HTMLResponse)
def ui_jobcheck_page(request: Request) -> HTMLResponse:
    """Serve the Job Check page (field-crew quality-check form)."""
    email = require_feature(request, "jobcheck")
    activity.log_event("tool.open", actor=email, target="jobcheck")
    path = WEB_DIR / "jobcheck.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


@app.get("/ui/api/jobcheck/jobs")
def ui_jobcheck_jobs(request: Request) -> dict:
    """Active Projects-board jobs for the dropdown. Read-only."""
    require_feature(request, "jobcheck")
    try:
        return jobcheck_flow.list_active_jobs()
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobcheck] jobs error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


@app.get("/ui/api/jobcheck/job/{item_id}")
def ui_jobcheck_job(item_id: int, request: Request) -> dict:
    """One job's context header + allowlisted columns with current values.
    Read-only."""
    require_feature(request, "jobcheck")
    try:
        detail = jobcheck_flow.get_job_detail(item_id)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobcheck] job error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail_msg, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail_msg, "advice": advice},
        )
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "code": "ITEM_NOT_FOUND",
                    "detail": f"Monday item {item_id} doesn't exist.",
                    "advice": "Reload the job list and pick again."},
        )
    return detail


@app.post("/ui/api/jobcheck/job/{item_id}")
def ui_jobcheck_save(item_id: int, req: JobCheckSaveRequest, request: Request) -> dict:
    """
    THE Monday write: save the crew's checked values to the selected item.
    User-initiated (the gold Save tap), allowlist-validated server-side,
    audit-logged old→new. Returns confirmed values + per-column failures —
    no silent partial writes.
    """
    actor = require_feature(request, "jobcheck")
    if not isinstance(req.values, dict) or not req.values:
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "code": "NO_VALUES",
                    "detail": "No column values submitted.",
                    "advice": "Change at least one field, then tap Save."},
        )
    try:
        return jobcheck_flow.save_job_check(item_id, req.values, actor)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobcheck] save error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


class JobCheckUpdateRequest(BaseModel):
    text: str = Field(..., description="Monday update body to post on the Ops item.")


@app.get("/ui/api/jobcheck/job/{item_id}/updates")
def ui_jobcheck_updates(item_id: int, request: Request) -> dict:
    """Recent Monday updates on the Operations item. Read-only."""
    require_feature(request, "jobcheck")
    try:
        out = jobcheck_flow.list_updates(item_id)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobcheck] updates error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail_msg, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail_msg, "advice": advice},
        )
    if not out.get("ok") and out.get("error") == "ITEM_NOT_FOUND":
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "code": "ITEM_NOT_FOUND",
                    "detail": out.get("detail") or f"Monday item {item_id} doesn't exist.",
                    "advice": "Reload the job list and pick again."},
        )
    return out


@app.post("/ui/api/jobcheck/job/{item_id}/updates")
def ui_jobcheck_post_update(item_id: int, req: JobCheckUpdateRequest,
                            request: Request) -> dict:
    """Post a Monday update on the Operations item. Never changes Stage."""
    actor = require_feature(request, "jobcheck")
    try:
        out = jobcheck_flow.post_update(item_id, req.text, actor)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobcheck] post update error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail_msg, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail_msg, "advice": advice},
        )
    if not out.get("ok"):
        code = out.get("error") or "UPDATE_FAILED"
        status = 404 if code == "ITEM_NOT_FOUND" else 400
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code,
                    "detail": out.get("detail") or "Couldn't post the update.",
                    "advice": "Write a short note and try again."},
        )
    return out


@app.post("/ui/api/jobcheck/job/{item_id}/photos")
async def ui_jobcheck_photos(item_id: int, request: Request) -> dict:
    """
    Multipart photo upload → project Drive Pictures folder → Monday update
    on the Operations item (note + Drive links). Never changes Stage.
    """
    actor = require_feature(request, "jobcheck")
    form = await request.form()
    note = str(form.get("note") or "")
    files = []
    for key in form.keys():
        if key == "note":
            continue
        val = form.get(key)
        # form.getlist for repeated keys
        values = form.getlist(key) if hasattr(form, "getlist") else [val]
        for v in values:
            if hasattr(v, "read"):
                raw = await v.read()
                files.append((
                    getattr(v, "filename", None) or "photo.jpg",
                    raw,
                    getattr(v, "content_type", None) or "image/jpeg",
                ))
    try:
        out = jobcheck_flow.upload_photos(item_id, files, actor, note=note)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobcheck] photos error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail_msg, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail_msg, "advice": advice},
        )
    if not out.get("ok") and not out.get("uploaded"):
        code = out.get("error") or "PHOTO_FAILED"
        status = 404 if code == "ITEM_NOT_FOUND" else 400
        if code in ("GFOLDER_MISSING", "DRIVE_UNAVAILABLE"):
            status = 422
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code,
                    "detail": out.get("detail") or "Photo upload failed.",
                    "advice": out.get("advice") or "Fix the Drive folder link and retry."},
        )
    return out


# ---------------------------------------------------------------------------
# Job Start routes — the Sales → Operations handoff (designed 2026-07-29,
# docs/portal-job-start-design.md). Gated by the `jobstart` grant.
#
# This is a HARD GATE (Jordan's call): the handoff POST refuses with 422 and a
# named missing-field list until the packet is complete. The UI's disabled
# button is a courtesy — jobstart_flow.require_complete() is the enforcement,
# re-run server-side on every submit.
#
# The draft PUT is deliberately NOT gated: autosaving a partial packet is the
# whole mitigation that makes a hard gate survivable in the field.
# ---------------------------------------------------------------------------

class JobStartDraftRequest(BaseModel):
    values: dict = Field(..., description="{field_key: raw value} — partial is fine.")
    job_name: Optional[str] = Field(
        None, description="Item name to create on Projects/Operations.")
    label: Optional[str] = Field(None, description="Bid name, for the draft list.")
    updated_at: Optional[str] = Field(
        None, description="Client ISO timestamp; older than stored ⇒ ignored as stale.")


class JobStartSendRequest(BaseModel):
    values: dict = Field(..., description="{field_key: raw value} — the full packet.")
    job_name: Optional[str] = Field(
        None, description="Item name to create on Projects/Operations.")


class JobStartSendBackRequest(BaseModel):
    note: str = Field(..., description="What Operations still needs.")


@app.get("/ui/jobstart", response_class=HTMLResponse)
def ui_jobstart_page(request: Request) -> HTMLResponse:
    """Serve the Job Start page (Sales → Operations handoff)."""
    email = require_feature(request, "jobstart")
    activity.log_event("tool.open", actor=email, target="jobstart")
    path = WEB_DIR / "jobstart.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


@app.get("/ui/api/jobstart/bids")
def ui_jobstart_bids(request: Request) -> dict:
    """Accepted bids awaiting handoff, with draft + handed-off state. Read-only."""
    require_feature(request, "jobstart")
    try:
        return jobstart_flow.list_open_handoffs()
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobstart] bids error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


@app.get("/ui/api/jobstart/bid/{bid_id}")
def ui_jobstart_bid(bid_id: int, request: Request) -> dict:
    """One bid's context, packet spec, prefilled values and gate state."""
    actor = require_feature(request, "jobstart")
    try:
        detail = jobstart_flow.get_handoff_detail(bid_id, actor)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobstart] bid error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail_msg, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail_msg, "advice": advice},
        )
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "code": "BID_NOT_FOUND",
                    "detail": f"Bid {bid_id} doesn't exist on the Bid Board.",
                    "advice": "Reload the list and pick again."},
        )
    return detail


@app.put("/ui/api/jobstart/bid/{bid_id}/draft")
def ui_jobstart_save_draft(bid_id: int, req: JobStartDraftRequest,
                           request: Request) -> dict:
    """
    Autosave a partial packet. NEVER gated — this is what lets a salesperson
    start the packet in a driveway and finish it at a desk.
    """
    actor = require_feature(request, "jobstart")
    try:
        return jobstart_flow.save_packet_draft(
            bid_id, req.values, actor, job_name=req.job_name,
            label=req.label, updated_at=req.updated_at)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobstart] draft error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )


def _jobstart_guard(result: dict) -> dict:
    """Shared result → HTTP mapping for the three handoff actions."""
    if result.get("blocked"):
        missing = result.get("missing") or []
        names = ", ".join(m["label"] for m in missing)
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "PACKET_INCOMPLETE",
                    "detail": f"The packet needs {len(missing)} more "
                              f"field(s): {names}.",
                    "advice": "Fill the highlighted fields. Your packet is "
                              "saved — you can come back to it.",
                    "missing": missing},
        )
    if result.get("field_errors"):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "PACKET_INVALID",
                    "detail": result.get("detail") or "Some fields couldn't be saved.",
                    "advice": "Fix the highlighted fields and try again.",
                    "field_errors": result["field_errors"]},
        )
    if not result.get("ok"):
        raise HTTPException(
            status_code=409,
            detail={"ok": False,
                    "code": "SELF_ACCEPT" if result.get("self_accept") else "HANDOFF_STATE",
                    "detail": result.get("detail") or "That action isn't available.",
                    "advice": "Reload the job to see where the packet stands."},
        )
    return result


@app.post("/ui/api/jobstart/bid/{bid_id}/send")
def ui_jobstart_send(bid_id: int, req: JobStartSendRequest,
                     request: Request) -> dict:
    """
    Sales sends the packet to Operations. Gated: an incomplete packet is refused
    with 422 and the named missing fields. Renders the packet PDF and pings ops.
    NOTHING is written to Monday here — that happens on acceptance.
    """
    actor = require_feature(request, "jobstart")
    try:
        result = jobstart_flow.send_to_ops(bid_id, req.values, actor,
                                           job_name=req.job_name)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobstart] send error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )
    return _jobstart_guard(result)


@app.post("/ui/api/jobstart/bid/{bid_id}/accept")
def ui_jobstart_accept(bid_id: int, request: Request) -> dict:
    """
    THE handoff. Operations accepts, and only now does the job become real:
    adopt-or-create the Projects item, adopt-or-create the Operations item (the
    one the legacy automation never made), stamp the bid, file the accepted
    packet PDF into the job's Drive folder, and post the links to Slack.

    Refused (409) if the packet isn't waiting on ops, or if the accepter is the
    person who sent it — a handoff with one signature isn't a handoff.
    """
    actor = require_feature(request, "jobstart")
    try:
        result = jobstart_flow.accept(bid_id, actor)
    except MondayNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "MONDAY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin — MONDAY_API_TOKEN isn't set on the service."},
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobstart] accept error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )
    return _jobstart_guard(result)


@app.post("/ui/api/jobstart/bid/{bid_id}/gc-email")
def ui_jobstart_gc_email(bid_id: int, request: Request) -> dict:
    """
    Draft the GC scope-confirmation email into hello@ Drafts. DRAFT ONLY — the
    locked architecture never auto-sends to a customer; a human reviews and hits
    send. Re-running updates the same draft in place.
    """
    actor = require_feature(request, "jobstart")
    try:
        result = jobstart_flow.email_scope_to_gc(bid_id, actor)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobstart] gc-email error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )
    if not result.get("ok"):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": result.get("code") or "GC_EMAIL_BLOCKED",
                    "detail": result.get("detail") or "Couldn't draft the email.",
                    "advice": result.get("advice")
                              or "Fill in the missing detail and try again."},
        )
    return result


@app.post("/ui/api/jobstart/bid/{bid_id}/send-back")
def ui_jobstart_send_back(bid_id: int, req: JobStartSendBackRequest,
                          request: Request) -> dict:
    """Operations returns a packet to Sales naming what's missing."""
    actor = require_feature(request, "jobstart")
    try:
        result = jobstart_flow.send_back(bid_id, req.note, actor)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ui:jobstart] send-back error: {type(e).__name__}: {e}", file=sys.stderr)
        status, code, detail, advice = _friendly_error(e)
        raise HTTPException(
            status_code=status,
            detail={"ok": False, "code": code, "detail": detail, "advice": advice},
        )
    return _jobstart_guard(result)


# ---------------------------------------------------------------------------
# Paid-by-Check routes (read-only path: OCR → parse → match → confirm modal).
# Gated by the `invoice` grant. The commit step (writes) is not enabled yet.
# ---------------------------------------------------------------------------

@app.get("/ui/check", response_class=HTMLResponse)
def ui_check_page(request: Request) -> HTMLResponse:
    """Serve the check-deposit page."""
    email = require_feature(request, "check")
    activity.log_event("tool.open", actor=email, target="check")
    path = WEB_DIR / "check.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


@app.post("/ui/api/check/extract")
async def ui_check_extract(request: Request, file: UploadFile = File(...)) -> dict:
    """
    READ ONLY. OCR the uploaded check, parse fields, and match against open
    invoices on the Monday ledger. Thin wrapper — orchestration lives in
    orchestrators.check_flow.extract_check.
    """
    email = require_feature(request, "check")
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "code": "NO_FILE", "detail": "No image received.",
                    "advice": "Choose a check image and try again."},
        )
    return check_flow.extract_check(content, email=email)


@app.post("/ui/api/check/commit")
async def ui_check_commit(
    request: Request,
    file: UploadFile = File(...),
    monday_item_ids: Optional[str] = Form(None),   # CSV — one check can pay several invoices
    monday_item_id: Optional[int] = Form(None),    # legacy single-invoice field, still honored
    check_no: Optional[str] = Form(None),
    amount: Optional[str] = Form(None),
    date: Optional[str] = Form(None),
    payer: Optional[str] = Form(None),
    reference: Optional[str] = Form(None),
    allow_mismatch: Optional[str] = Form(None),    # "1" = user overrode the sum gate
    allocations: Optional[str] = Form(None),       # JSON {item_id: cents} — partial-payment split
) -> dict:
    """
    Record a check against one or more open invoices — in full, or split via
    per-invoice `allocations` (partial payments). Thin wrapper — orchestration
    lives in orchestrators.check_flow.commit_check.
    """
    email = require_feature(request, "check")
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail={"ok": False, "code": "NO_FILE",
            "detail": "No image received.", "advice": "Re-upload the check/stub image and confirm again."})
    try:
        ids = [int(x) for x in (monday_item_ids or "").split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail={"ok": False, "code": "INVALID_INPUT",
            "detail": f"monday_item_ids must be comma-separated ids, got: {monday_item_ids!r}.",
            "advice": "Re-open the check and pick the invoice(s) from the list."})
    if not ids and monday_item_id is not None:
        ids = [monday_item_id]
    if not ids:
        raise HTTPException(status_code=400, detail={"ok": False, "code": "NO_INVOICES",
            "detail": "No invoices selected.",
            "advice": "Pick the invoice(s) this check pays before recording."})
    alloc_map = None
    if allocations:
        try:
            raw = json.loads(allocations)
            alloc_map = {int(k): int(v) for k, v in raw.items()}
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=400, detail={"ok": False, "code": "INVALID_INPUT",
                "detail": f"allocations must be a JSON object of item_id -> cents, got: {allocations!r}.",
                "advice": "Re-open the check and re-enter the per-invoice amounts."})
    return check_flow.commit_check(
        monday_item_ids=ids, image_bytes=image_bytes,
        content_type=file.content_type, check_no=check_no, amount=amount,
        date_str=date, email=email, allow_mismatch=(allow_mismatch == "1"),
        allocations=alloc_map,
    )


# ---------------------------------------------------------------------------
# Admin routes (manage who can access which tools) — require the `admin` grant
# ---------------------------------------------------------------------------

@app.get("/ui/admin", response_class=HTMLResponse)
def ui_admin_page(request: Request) -> HTMLResponse:
    """Serve the admin page (Jordan/Andrea — anyone with the `admin` grant)."""
    email = require_admin(request)
    activity.log_event("tool.open", actor=email, target="admin")
    path = WEB_DIR / "admin.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


@app.get("/ui/api/admin/users")
def ui_admin_list_users(request: Request) -> dict:
    """List everyone with portal access + the feature catalog, for the admin UI."""
    require_admin(request)
    supers = access.superadmin_emails()
    rows: dict[str, dict] = {}

    # Store-managed users (only meaningful on the gcs backend).
    if access.backend() == "gcs":
        try:
            for em, rec in portal_store.list_users().items():
                rows[em] = {
                    "email": em,
                    "features": rec.get("features", []),
                    "effective": sorted(access.effective_features(em)),
                    "person": rec.get("person", {}),
                    "updated_by": rec.get("updated_by"),
                    "updated_at": rec.get("updated_at"),
                    "managed_by": "store",
                }
        except portal_store.PortalStoreNotConfigured as e:
            raise HTTPException(
                status_code=503,
                detail={"ok": False, "code": "STORE_NOT_CONFIGURED", "detail": str(e),
                        "advice": "Ask an admin to set GVC_PORTAL_STATE_BUCKET / service-account JSON."},
            )

    # Break-glass superadmins (env allowlist) — always shown, can't be edited here.
    for em in sorted(supers):
        rows.setdefault(em, {"email": em, "features": ["*"], "person": {},
                             "updated_by": None, "updated_at": None})
        rows[em]["effective"] = sorted(access.ALL_FEATURES)
        rows[em]["managed_by"] = "env"

    return {
        "ok": True,
        "backend": access.backend(),
        "features": list(access.FEATURES),
        "users": [rows[k] for k in sorted(rows)],
    }


def _validate_features(features: list[str]) -> list[str]:
    """Allow ['*'] or any subset of the known feature names; reject the rest."""
    cleaned = [f.strip().lower() for f in features if f and f.strip()]
    if access.WILDCARD in cleaned:
        return [access.WILDCARD]
    bad = [f for f in cleaned if f not in access.ALL_FEATURES]
    if bad:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "UNKNOWN_FEATURE",
                    "detail": f"Unknown feature(s): {', '.join(bad)}.",
                    "advice": f"Valid features: {', '.join(access.FEATURES)} (or '*')."},
        )
    return sorted(set(cleaned))


@app.post("/ui/api/admin/users")
def ui_admin_upsert_user(req: AdminUpsertRequest, request: Request) -> dict:
    """Add or update a user's grants (and optional person fields)."""
    actor = require_admin(request)
    if access.backend() != "gcs":
        raise HTTPException(
            status_code=409,
            detail={"ok": False, "code": "BACKEND_READONLY",
                    "detail": "Grant editing requires the GCS backend.",
                    "advice": "Set GVC_GRANTS_BACKEND=gcs on the service, then redeploy."},
        )
    target = portal_store.normalize_email(req.email)
    if "@" not in target:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_EMAIL", "detail": "A valid work email is required.",
                    "advice": "Enter the employee's @greenvalleycontractors.com address."},
        )
    features = _validate_features(req.features)
    try:
        portal_store.upsert_user(target, features=features, person=req.person, actor=actor)
    except portal_store.PortalStoreNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "STORE_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to configure the portal store bucket."},
        )
    activity.log_event("admin.grant.update", actor=actor, target=target,
                       features=",".join(features))
    return {"ok": True, "email": target, "features": features,
            "effective": sorted(access.effective_features(target))}


@app.post("/ui/api/admin/users/remove")
def ui_admin_remove_user(req: AdminRemoveRequest, request: Request) -> dict:
    """Remove a user from the portal store (revokes all access)."""
    actor = require_admin(request)
    target = portal_store.normalize_email(req.email)
    if target in access.superadmin_emails():
        raise HTTPException(
            status_code=409,
            detail={"ok": False, "code": "SUPERADMIN_PROTECTED",
                    "detail": f"{target} is a break-glass superadmin (env allowlist).",
                    "advice": "Remove them from GVC_PORTAL_ALLOWED_EMAILS to revoke."},
        )
    if access.backend() != "gcs":
        raise HTTPException(
            status_code=409,
            detail={"ok": False, "code": "BACKEND_READONLY",
                    "detail": "Grant editing requires the GCS backend.",
                    "advice": "Set GVC_GRANTS_BACKEND=gcs on the service, then redeploy."},
        )
    try:
        existed = portal_store.remove_user(target, actor=actor)
    except portal_store.PortalStoreNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "STORE_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Ask an admin to configure the portal store bucket."},
        )
    activity.log_event("admin.user.remove", actor=actor, target=target,
                       result="ok" if existed else "noop")
    return {"ok": True, "email": target, "existed": existed}


# ---------------------------------------------------------------------------
# Activity tracker (admin) — read portal audit events back from Cloud Logging
# ---------------------------------------------------------------------------

@app.get("/ui/activity", response_class=HTMLResponse)
def ui_activity_page(request: Request) -> HTMLResponse:
    """Serve the Activity page (anyone with the `activity` grant; admins inherit it)."""
    email = require_feature(request, "activity")
    activity.log_event("tool.open", actor=email, target="activity")
    path = WEB_DIR / "activity.html"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail={"ok": False, "code": "UI_MISSING",
                    "detail": f"{path} not found in the deployed image.",
                    "advice": "Ask an admin to confirm web/ was COPYed in the Dockerfile."},
        )
    return HTMLResponse(path.read_text(encoding="utf-8").replace("{{EMAIL}}", html_escape(email)))


@app.get("/ui/api/activity/events")
def ui_activity_events(
    request: Request,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    range: str = "7d",
    page_size: int = activity_read.DEFAULT_PAGE_SIZE,
    page_token: Optional[str] = None,
) -> dict:
    """Return a page of portal activity events (newest first) for the admin UI."""
    admin_email = require_feature(request, "activity")
    try:
        out = activity_read.fetch_events(
            actor=actor, action=action, result=result,
            range_key=range, page_size=page_size, page_token=page_token,
        )
    except activity_read.ActivityReadNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "ACTIVITY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Grant the service account roles/logging.viewer, then reload."},
        )
    activity.log_event("activity.view", actor=admin_email,
                       target=f"range={range}", result="ok")
    return {"ok": True, "events": out["events"], "next_page_token": out["next_page_token"]}


@app.get("/ui/api/activity/export.csv")
def ui_activity_export(
    request: Request,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    range: str = "7d",
) -> Response:
    """
    Export the current filtered view as CSV. Pulls up to the max page size so a
    single click captures the visible window; for deep history, narrow the range.
    """
    admin_email = require_feature(request, "activity")
    try:
        out = activity_read.fetch_events(
            actor=actor, action=action, result=result,
            range_key=range, page_size=activity_read.MAX_PAGE_SIZE,
        )
    except activity_read.ActivityReadNotConfigured as e:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "code": "ACTIVITY_NOT_CONFIGURED", "detail": str(e),
                    "advice": "Grant the service account roles/logging.viewer, then reload."},
        )
    csv_text = activity_read.to_csv(out["events"])
    activity.log_event("activity.export", actor=admin_email,
                       target=f"range={range}", result="ok", rows=len(out["events"]))
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="gvc-activity.csv"'},
    )


# Plain-English labels for the fields validate() can reject. Keys are the
# dotted paths used in invoice.validate()'s ValueError messages; values are
# what an office user sees on the form. Keep in sync with web/invoice.html.













