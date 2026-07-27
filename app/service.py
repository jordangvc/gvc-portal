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
from shared import activity_read as activity_read
from shared import portal_store as portal_store
from subsystems.estimate import drafts as estimate_drafts
from subsystems.estimate import scope_catalog as scope_catalog
from subsystems.invoice import drafts as invoice_drafts
from subsystems.change_order import drafts as co_drafts
from adapters import vision as vision
from adapters import slack_notify as slack_notify
from subsystems.checks import deposit as check_deposit
from orchestrators import change_order_flow as change_order_flow
from orchestrators import check_flow
from orchestrators import coi_flow
from orchestrators import lien_flow
from orchestrators import jobcheck_flow
from subsystems.coi import template as coi_template
from adapters.monday import co as monday_co
from adapters.monday import estimate as monday_estimate
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


@app.post("/ui/api/invoice/run")
def ui_invoice_run(req: FromJSONRequest, request: Request) -> dict:
    """Run the invoice flow for the browser form. Same core as /v1/from-json."""
    email = require_feature(request, "invoice")
    activity.log_event("invoice.run", actor=email, target=req.mode)
    return _run(req.data, mode=req.mode, finalize=req.finalize, source_label="ui:invoice")


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
def ui_invoice_lookup(request: Request, project_number: str = "") -> dict:
    """
    Prefill the invoice form from a canonical Project # (Projects board = SoT).
    Pulls client (linked customer + billing identity), job name/site, the
    Residential/Commercial/AIA classification, the project's Drive folder, and a
    suggested identifier (GVC-<year>-<Project #>). Sets job.monday_item_id so the
    live run links the ledger row + writes back to THIS project. Every field stays
    editable in the form — the office still enters the dollar line items.
    """
    email = require_feature(request, "invoice")
    pn = (project_number or "").strip()
    if not pn:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "code": "BAD_PROJECT_NUMBER",
                    "detail": "Enter a Project # to look up.",
                    "advice": "Type the project's Project # (e.g. C-005 or MV-001)."},
        )
    try:
        mc = MondayClient()
        match = mc.find_project_by_number(pn)
        if not match:
            raise HTTPException(
                status_code=404,
                detail={"ok": False, "code": "PROJECT_NOT_FOUND",
                        "detail": f"No project found with Project # '{pn}'.",
                        "advice": "Check the Project # on the Projects board, or add it there."},
            )
        prefill = mc.build_invoice_prefill(match["item_id"])
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
    activity.log_event("invoice.lookup", actor=email, target=pn)
    return {"ok": True, "prefill": prefill}


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
    activity.log_event("estimate.run", actor=user_email,
                       target=f"{req.mode}+revise" if req.revise else req.mode)
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
                "review, then click Send."
            )
            if not wb.get("gmail_draft_url") and "gmail_status" not in wb:
                wb["gmail_status"] = "No draft URL returned — check hello@ configuration."
            _log_estimate_slack(wb, actor=user_email)
        return {"ok": True, "writeback": wb}
    except HTTPException:
        raise
    except Exception as e:
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
    Search the Bid Board by bid name or Estimate # (for the
    estimate form's find-a-previous-estimate box). Returns light rows:
    [{item_id, name, estimate_number, stage, url}]. Read-only.
    """
    require_feature(request, "estimate")
    if len((q or "").strip()) < 2:
        return {"ok": True, "results": []}
    try:
        results = monday_estimate.search_bids(MondayClient(), q)
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
    activity.log_event("change_order.run", actor=user_email,
                       target=f"{req.mode}+revise" if req.revise else req.mode)
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
        return {"ok": True, "writeback": wb}
    except HTTPException:
        raise
    except Exception as e:
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
    activity.log_event("coi.run", actor=user_email, target=req.mode)
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













