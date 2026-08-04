"""
Gmail draft creation as billing@greenvalleycontractors.com.
=========================================================================
Authenticates as billing@ via OAuth (one-time browser flow), caches the
refresh token, and creates email drafts in billing@'s drafts folder with
the GVC-branded PDF attached.

The drafts are NOT sent. Andrea (or whoever is signed into billing@) opens
the draft, reviews it, and clicks Send manually. This matches the locked
architecture: admin-approved send via Gmail.

One-time setup: run `python gmail.py setup`. See docs/gmail-setup.md.

For Cloud Run: the refresh token from .gmail-token.json moves to Secret
Manager, loaded at startup. The OAuth flow happens once on the original dev Mac.
"""
from __future__ import annotations

from shared import paths
import base64
import html as html_lib
import json
import os
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    # Read-only mail access for the sent-watcher (find_sent_message): detects
    # when a draft actually left Drafts for Sent. Added 2026-07-24 — tokens
    # minted before then lack it; the watcher degrades to GmailScopeMissing
    # while draft creation keeps working (see _load_credentials).
    "https://www.googleapis.com/auth/gmail.readonly",
]
# Cloud Run mounts the gmail-token secret at /secrets/.gmail-token.json.
# Locally the file lives next to gmail.py. The env var lets ops override.
TOKEN_PATH = Path(
    os.environ.get("GMAIL_TOKEN_PATH")
    or (
        # Cloud Run mounts the gmail-token secret at /gmail-token/token.json.
        # Locally the file lives next to gmail.py as .gmail-token.json.
        "/gmail-token/token.json"
        if Path("/gmail-token/token.json").exists()
        else str(paths.DEFAULT_GMAIL_TOKEN_PATH)
    )
)
# The estimate flow drafts FROM hello@greenvalleycontractors.com. At GVC, hello@
# and billing@ are the SAME Google account (billing@ was renamed/aliased to
# hello@), so there is ONE authorized Gmail token — the existing gmail-token. A
# token only has authority over its own mailbox, but since it's one account that
# token already covers hello@, and From: hello@ is a valid send-as on it.
#
# Resolution order:
#   1. GMAIL_HELLO_TOKEN_PATH env var, if set (explicit override).
#   2. A dedicated hello token mount, IF one was ever provisioned
#      (/gmail-hello-token/token.json) — supports a future true 2nd account.
#   3. Fall back to the shared billing token (TOKEN_PATH) — the normal case:
#      same account, same token, no separate OAuth needed.
HELLO_TOKEN_PATH = Path(
    os.environ.get("GMAIL_HELLO_TOKEN_PATH")
    or (
        "/gmail-hello-token/token.json"
        if Path("/gmail-hello-token/token.json").exists()
        else TOKEN_PATH
    )
)

CLIENT_SECRETS_PATH = paths.DEFAULT_OAUTH_CLIENT_PATH

GMAIL_DRAFTS_URL_TEMPLATE = "https://mail.google.com/mail/u/0/#drafts?compose={draft_id}"


class GmailNotConfigured(Exception):
    """Raised when OAuth hasn't been completed yet."""


def _load_credentials(token_path: Optional[Path] = None):
    """
    Load cached OAuth credentials, refreshing if needed.

    token_path defaults to billing@'s TOKEN_PATH (unchanged behavior). Pass
    HELLO_TOKEN_PATH to authenticate as hello@ for the estimate flow.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = Path(token_path) if token_path else TOKEN_PATH
    # Only point people at `setup-hello` when a DEDICATED hello token path is in
    # use (i.e. it differs from the shared billing token). When hello@ falls back
    # to the shared token — the GVC case, one account — `setup` is the right cmd.
    setup_cmd = "setup-hello" if (path == HELLO_TOKEN_PATH and path != TOKEN_PATH) else "setup"

    if not path.exists():
        raise GmailNotConfigured(
            f"Gmail OAuth token not found at {path}. "
            f"Run `python gmail.py {setup_cmd}` once (see docs/gmail-setup.md)."
        )

    # Load with the token's OWN granted scopes (scopes=None), NOT module SCOPES:
    # pinning SCOPES makes refresh fail invalid_scope for any token minted before
    # a scope was added (gmail.readonly, 2026-07-24) — breaking drafts until
    # re-auth. A caller needing a newer scope gets a 403 → GmailScopeMissing.
    creds = Credentials.from_authorized_user_file(str(path))
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        try:
            path.write_text(creds.to_json())
        except OSError:
            # Cloud Run mounts secrets read-only — skip write-back.
            # The refreshed creds are still valid in memory for this request.
            pass
    if not creds or not creds.valid:
        raise GmailNotConfigured(
            "Gmail OAuth credentials invalid or expired beyond refresh. "
            f"Re-run `python gmail.py {setup_cmd}`."
        )
    return creds


def run_oauth_setup(
    token_path: Optional[Path] = None,
    account_label: str = "billing@greenvalleycontractors.com",
) -> None:
    """
    Interactive one-time OAuth flow. Opens browser, user signs in as
    `account_label`, authorizes, token is cached at `token_path`
    (defaults to billing@'s TOKEN_PATH).
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    path = Path(token_path) if token_path else TOKEN_PATH

    if not CLIENT_SECRETS_PATH.exists():
        print(f"ERROR: OAuth client secrets not found at {CLIENT_SECRETS_PATH}",
              file=sys.stderr)
        print("Follow docs/gmail-setup.md Step 1 to create an OAuth 2.0 Client ID "
              "in Google Cloud Console and download the JSON.", file=sys.stderr)
        sys.exit(2)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), SCOPES)
    print(f"Opening browser. Sign in as {account_label} and authorize.")
    creds = flow.run_local_server(port=0, open_browser=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)
    print(f"OK — refresh token cached at {path}")
    print("Test: python -c 'from gmail import verify; verify()'")


def _get_signature_html(service, send_as_email: Optional[str] = None) -> str:
    """
    Fetch the HTML signature configured on the account's primary sendAs address
    (or a specific sendAs address if provided). Returns empty string on failure
    so callers can fall back gracefully.

    Requires the gmail.settings.basic scope. If the cached token predates that
    scope being added, this will fail with 'insufficient scope' until the user
    re-runs `python gmail.py setup`.
    """
    try:
        result = service.users().settings().sendAs().list(userId="me").execute()
        send_as_list = result.get("sendAs", []) or []
        match = None
        for sa in send_as_list:
            if send_as_email and sa.get("sendAsEmail") == send_as_email:
                match = sa
                break
            if not send_as_email and sa.get("isPrimary"):
                match = sa
                break
        if not match and send_as_list:
            match = send_as_list[0]
        return (match or {}).get("signature", "") or ""
    except Exception as e:
        print(
            f"[gmail] could not load signature ({type(e).__name__}: {e}). "
            "If you just added the gmail.settings.basic scope, re-run "
            "`python gmail.py setup` to re-authorize.",
            file=sys.stderr,
        )
        return ""


def verify(token_path: Optional[Path] = None) -> dict:
    """Read-only check that auth works. Returns the authenticated profile."""
    from googleapiclient.discovery import build

    creds = _load_credentials(token_path)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    print(f"OK — authenticated as: {profile.get('emailAddress')}")
    return profile


class GmailScopeMissing(GmailNotConfigured):
    """The cached token lacks a scope the caller needs (e.g. the sent-watcher's
    gmail.readonly). Not an outage: drafts keep working on the old scopes —
    callers skip + surface instead of failing the request."""


def find_sent_message(subject_query: str, *, newer_than_days: int = 60,
                      token_path: Optional[Path] = None) -> Optional[dict]:
    """
    Return the newest SENT message whose subject contains `subject_query`
    ({message_id, thread_id, sent_epoch_ms}), or None if no match.

    Read-only (messages.list/get on in:sent) — the ONLY Gmail read in the
    portal; it exists so the sent-watcher can tell "draft still waiting" from
    "human clicked Send". Requires gmail.readonly: tokens minted before
    2026-07-24 raise GmailScopeMissing so the watcher skips gracefully.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    creds = _load_credentials(token_path)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    q = f'in:sent subject:"{subject_query}" newer_than:{int(newer_than_days)}d'
    try:
        resp = service.users().messages().list(
            userId="me", q=q, maxResults=3).execute()
        messages = resp.get("messages") or []
        if not messages:
            return None
        # list is newest-first; internalDate needs a messages.get
        msg = service.users().messages().get(
            userId="me", id=messages[0]["id"], format="minimal").execute()
        return {
            "message_id": msg.get("id"),
            "thread_id": msg.get("threadId"),
            "sent_epoch_ms": int(msg.get("internalDate") or 0),
        }
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        low = str(e).lower()
        if status == 403 and ("insufficient" in low or "scope" in low):
            raise GmailScopeMissing(
                "Gmail token lacks the gmail.readonly scope the sent-watcher "
                "needs. Mint a new token (OAuth setup now requests it) and "
                "rotate the gmail-token secret."
            ) from e
        raise


def _find_existing_draft_by_invoice_id(service, invoice_identifier: str) -> Optional[str]:
    """
    Return the draft ID of any existing draft whose subject contains the given
    invoice identifier, or None if no match. Used by create_draft() to avoid
    creating duplicate drafts when the same invoice is run more than once
    (the Stripe idempotency_key returns the same invoice, but without this
    dedup we'd accumulate identical drafts in billing@).

    Search scopes to drafts only (drafts.list naturally excludes sent mail).
    The invoice identifier (e.g. "GVC-2026-MV-007") appears in every subject
    variant generated by draft_invoice_email(), so a subject substring match
    is reliable.
    """
    if not invoice_identifier:
        return None
    q = f'subject:"{invoice_identifier}"'
    resp = service.users().drafts().list(userId="me", q=q, maxResults=5).execute()
    drafts = resp.get("drafts", []) or []
    return drafts[0]["id"] if drafts else None


def create_draft(
    *,
    to: str,
    subject: str,
    body: str,
    attachment_path: Optional[Path] = None,
    attachment_filename: Optional[str] = None,
    attachments: Optional[list] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    from_addr: Optional[str] = None,
    invoice_identifier: Optional[str] = None,
    token_path: Optional[Path] = None,
) -> dict:
    """
    Create a draft email in the authenticated account's Drafts folder.

    Supports multiple PDF attachments via `attachments` (list of Path or
    (Path, filename) tuples). The legacy `attachment_path` / `attachment_filename`
    params are still accepted and prepended to the list.

    When `invoice_identifier` is supplied, the draft is stamped with an
    X-GVC-Invoice-Id header and a pre-create lookup checks Drafts for any
    existing draft mentioning the identifier in its subject; if found, the
    existing draft is updated in place (same draft_id, same Gmail URL)
    instead of creating a duplicate. This protects Andrea from accidentally
    sending the same invoice twice.

    Returns: {draft_id, message_id, gmail_url, replaced_existing}
    The draft is NOT sent — it lives in Drafts until a human clicks Send.
    """
    from googleapiclient.discovery import build

    creds = _load_credentials(token_path)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    signature_html = _get_signature_html(service, send_as_email=from_addr)

    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if from_addr:
        msg["From"] = from_addr
    msg["Subject"] = subject
    if invoice_identifier:
        msg["X-GVC-Invoice-Id"] = invoice_identifier
    msg.set_content(body)

    if signature_html:
        escaped = html_lib.escape(body).replace("\n", "<br>\n")
        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;'
            'font-size:14px;color:#222;">'
            f"{escaped}"
            "</div><br>"
            f"{signature_html}"
        )
        msg.add_alternative(html_body, subtype="html")

    # Build unified attachment list: [(Path, filename), ...]
    all_attachments: list[tuple[Path, str]] = []
    if attachment_path:
        p = Path(attachment_path)
        all_attachments.append((p, attachment_filename or p.name))
    if attachments:
        for item in attachments:
            if isinstance(item, (str, Path)):
                p = Path(item)
                all_attachments.append((p, p.name))
            else:
                p, fname = item
                all_attachments.append((Path(p), fname))

    for path, filename in all_attachments:
        if not path.exists():
            raise FileNotFoundError(f"Attachment not found: {path}")
        msg.add_attachment(
            path.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=filename,
        )

    encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    existing_id = (
        _find_existing_draft_by_invoice_id(service, invoice_identifier)
        if invoice_identifier else None
    )
    if existing_id:
        draft = service.users().drafts().update(
            userId="me", id=existing_id, body={"message": {"raw": encoded}}
        ).execute()
        replaced_existing = True
    else:
        draft = service.users().drafts().create(
            userId="me", body={"message": {"raw": encoded}}
        ).execute()
        replaced_existing = False

    draft_id = draft["id"]
    message_id = draft.get("message", {}).get("id")
    return {
        "draft_id": draft_id,
        "message_id": message_id,
        "gmail_url": GMAIL_DRAFTS_URL_TEMPLATE.format(draft_id=draft_id),
        "replaced_existing": replaced_existing,
    }


def delete_draft_by_invoice_id(
    invoice_identifier: str,
    *,
    token_path: Optional[Path] = None,
) -> dict:
    """
    Delete the draft whose subject mentions `invoice_identifier`, if one exists.
    Used by the correction/reissue flow to remove the SUPERSEDED draft after a
    revision is issued under a new number (the revision gets its own fresh draft).

    Idempotent: returns {deleted: False, draft_id: None} when no matching draft is
    found, so calling it twice (or after a manual delete) is safe.
    """
    from googleapiclient.discovery import build

    if not invoice_identifier:
        return {"deleted": False, "draft_id": None}
    creds = _load_credentials(token_path)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    draft_id = _find_existing_draft_by_invoice_id(service, invoice_identifier)
    if not draft_id:
        return {"deleted": False, "draft_id": None}
    service.users().drafts().delete(userId="me", id=draft_id).execute()
    return {"deleted": True, "draft_id": draft_id}


# ---------------------------------------------------------------------------
# Invoice-specific helper
# ---------------------------------------------------------------------------

def draft_invoice_email(
    *,
    customer_name: str,
    contact_name: Optional[str],
    to: str,
    cc: Optional[str],
    invoice_identifier: str,
    job_name: str,
    amount_pretty: str,
    due_date_pretty: str,
    hosted_invoice_url: str,
    pdf_path: Path,
    invoice_type: str = "standard",
    pay_app_number: Optional[int] = None,
    period_end_date: Optional[str] = None,
    extra_pdfs: Optional[list] = None,
    email_context: Optional[str] = None,
    office_notice: Optional[str] = None,
    subject_prefix: Optional[str] = None,
) -> dict:
    """
    Build a draft email per the GVC Invoice Messaging Guide.
    invoice_type: "standard" | "final" | "progress"
    email_context: optional one-or-two-line note from the billing instructions
      (e.g. "Final draw minus touch-up; pad install Tuesday"). Surfaced as a
      parenthetical line just under the greeting so Andrea sees the project
      context inline without bouncing back to the source folder.
    """
    greeting_name = contact_name.split()[0] if contact_name else customer_name

    if invoice_type == "final":
        subject = f"Final Invoice {invoice_identifier} - {job_name}"
    elif invoice_type == "progress":
        subject = f"{job_name} - Pay Application #{pay_app_number} / Invoice {invoice_identifier}"
    else:
        subject = f"Invoice {invoice_identifier} - {job_name}"
    if subject_prefix:
        subject = f"{subject_prefix.rstrip()} {subject}"

    payment_block: list[str] = []
    if hosted_invoice_url:
        payment_block = [
            "You can pay online here:",
            f"  {hosted_invoice_url}",
            "",
            "ACH is preferred — no processing fee. Credit card payments incur a 3% "
            "processing fee.",
        ]

    context_block: list[str] = []
    if email_context:
        # Strip leading/trailing whitespace per line so JSON multi-line values
        # don't pull awkward indentation into the email.
        clean = "\n".join(line.strip() for line in email_context.strip().splitlines())
        context_block = ["", f"(Project note: {clean})"]

    if invoice_type == "final":
        body_parts = [
            f"{greeting_name},",
        ] + context_block + [
            "",
            f"Attached is the final invoice for {job_name}.",
        ]
        if payment_block:
            body_parts += [""] + payment_block
        body_parts += [
            "",
            "Appreciate the work together on this one. Reach out if anything looks "
            "off or if you need additional documentation for close-out.",
            "",
            "Looking forward to the next one.",
            "",
            "Thanks,",
            "The Green Valley Team c/o Andrea",
        ]
    elif invoice_type == "progress":
        through = f" covering work through {period_end_date}" if period_end_date else ""
        body_parts = [
            f"{greeting_name},",
        ] + context_block + [
            "",
            f"Attached is pay application #{pay_app_number} for {job_name}{through}.",
        ]
        if payment_block:
            body_parts += [""] + payment_block
        body_parts += [
            "",
            "Let me know if you need anything additional to process.",
            "",
            "Thanks,",
            "The Green Valley Team c/o Andrea",
        ]
    else:
        body_parts = [
            f"{greeting_name},",
        ] + context_block + [
            "",
            f"Attached is your invoice for {job_name}.",
        ]
        if payment_block:
            body_parts += [""] + payment_block
        body_parts += [
            "",
            "Let me know if you need anything else from us.",
            "",
            "Thanks,",
            "The Green Valley Team c/o Andrea",
        ]

    body = "\n".join(body_parts)
    if office_notice:
        body = f"{office_notice.strip()}\n\n{body}"

    # Build attachments: GVC invoice PDF first, then any extra docs (G702, G703, etc.)
    extra_attachment_list = []
    if extra_pdfs:
        for p in extra_pdfs:
            extra_attachment_list.append(Path(p))

    return create_draft(
        to=to,
        cc=cc,
        subject=subject,
        body=body,
        attachment_path=pdf_path,
        attachment_filename=f"{invoice_identifier}.pdf",
        attachments=extra_attachment_list or None,
        invoice_identifier=invoice_identifier,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(paths.ENV_FILE)

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python gmail.py setup        # one-time OAuth flow (billing@)")
        print("  python gmail.py verify       # confirm billing@ OAuth works")
        print("  python gmail.py test-draft   # create a test draft to billing@")
        print("  python gmail.py setup-hello  # one-time OAuth flow (hello@, estimates)")
        print("  python gmail.py verify-hello # confirm hello@ OAuth works")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "setup":
        run_oauth_setup()
    elif cmd == "verify":
        verify()
    elif cmd == "setup-hello":
        run_oauth_setup(HELLO_TOKEN_PATH, "hello@greenvalleycontractors.com")
    elif cmd == "verify-hello":
        verify(HELLO_TOKEN_PATH)
    elif cmd == "test-draft":
        result = create_draft(
            to="billing@greenvalleycontractors.com",
            subject="GVC Invoice System — Test Draft",
            body="This is a test draft created by the GVC Invoice System.\n\n"
                 "If you can see this draft in billing@ Gmail, the integration is working.\n\n"
                 "Feel free to delete this draft.",
        )
        print(f"Draft created: {result['draft_id']}")
        print(f"Open it: {result['gmail_url']}")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
