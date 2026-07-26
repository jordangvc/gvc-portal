"""
Portal auth — in-app Google sign-in for the /ui/* routes.
=========================================================================
Implements Phase 2 of docs/portal-deploy-plan.md: the free auth path
(Cloud Run domain mapping + in-app Google OAuth) replacing the older
IAP + Load Balancer design.

Flow:
  GET /auth/login    -> 302 to Google's consent screen
  GET /auth/callback -> exchange code, verify ID token, require
                        hd == greenvalleycontractors.com AND email in the
                        allowlist, then set a signed session cookie.

Session cookie: HMAC-SHA256-signed JSON {email, exp}, Secure; HttpOnly;
SameSite=Lax. TTL defaults to 1 hour — when it lapses, the /ui gate
redirects back through /auth/login, which is transparent while the
user's Google session is alive (the "silent re-auth" in the plan).

Env (all required for the gate to open; see portal-deploy-plan.md):
  GVC_OAUTH_CLIENT_ID        OAuth 2.0 Web client ID
  GVC_OAUTH_CLIENT_SECRET    OAuth 2.0 Web client secret
  GVC_SESSION_SECRET         random >=32-byte string; signs session cookies
  GVC_PORTAL_ALLOWED_EMAILS  comma-separated allowlist (also the future
                             per-feature role seed)
Optional:
  GVC_PORTAL_HOSTED_DOMAIN   default greenvalleycontractors.com
  GVC_SESSION_TTL_SECONDS    default 3600

Deny-by-default: if any required env is missing, verify_session() returns
None and the /ui gate stays closed (it never fails open).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from typing import Optional
from urllib.parse import urlencode

from shared import access as access

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

SESSION_COOKIE = "gvc_session"
STATE_TTL_SECONDS = 600  # login attempt must complete within 10 minutes


class AuthNotConfigured(Exception):
    """Raised when the OAuth env vars are missing."""


def _env(name: str) -> Optional[str]:
    return (os.environ.get(name) or "").strip() or None


def client_id() -> str:
    cid = _env("GVC_OAUTH_CLIENT_ID")
    if not cid:
        raise AuthNotConfigured("GVC_OAUTH_CLIENT_ID env var not set.")
    return cid


def _client_secret() -> str:
    sec = _env("GVC_OAUTH_CLIENT_SECRET")
    if not sec:
        raise AuthNotConfigured("GVC_OAUTH_CLIENT_SECRET env var not set.")
    return sec


def _session_secret() -> bytes:
    sec = _env("GVC_SESSION_SECRET")
    if not sec:
        raise AuthNotConfigured("GVC_SESSION_SECRET env var not set.")
    return sec.encode("utf-8")


def hosted_domain() -> str:
    return _env("GVC_PORTAL_HOSTED_DOMAIN") or "greenvalleycontractors.com"


def allowed_emails() -> set[str]:
    raw = _env("GVC_PORTAL_ALLOWED_EMAILS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def session_ttl() -> int:
    try:
        return int(_env("GVC_SESSION_TTL_SECONDS") or "3600")
    except ValueError:
        return 3600


# ---------------------------------------------------------------------------
# Signed-blob primitives (stdlib only; also used for the OAuth `state` param)
# ---------------------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_blob(payload: dict, *, secret: Optional[bytes] = None) -> str:
    secret = secret or _session_secret()
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_blob(token: str, *, secret: Optional[bytes] = None) -> Optional[dict]:
    """Return the payload if the signature is valid and not expired, else None."""
    try:
        secret = secret or _session_secret()
    except AuthNotConfigured:
        return None
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if not isinstance(payload, dict):
            return None
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)) or time.time() > exp:
            return None
        return payload
    except Exception:  # noqa: BLE001 — any malformed token is just "not signed in"
        return None


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

def login_redirect_url(*, redirect_uri: str, next_path: str = "/") -> str:
    """Build the Google consent-screen URL (GET /auth/login redirects here)."""
    state = sign_blob({"next": next_path, "exp": time.time() + STATE_TTL_SECONDS})
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "hd": hosted_domain(),
        "state": state,
        "access_type": "online",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code(*, code: str, redirect_uri: str) -> str:
    """
    Exchange the authorization code, verify the ID token, and return the
    authenticated email. Raises PermissionError on any check failure.
    """
    import requests as _requests
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token as g_id_token

    resp = _requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise PermissionError(f"Token exchange failed (HTTP {resp.status_code}).")
    raw_id_token = resp.json().get("id_token")
    if not raw_id_token:
        raise PermissionError("Token exchange returned no id_token.")

    claims = g_id_token.verify_oauth2_token(
        raw_id_token, g_requests.Request(), audience=client_id()
    )

    email = (claims.get("email") or "").lower()
    if not claims.get("email_verified"):
        raise PermissionError("Google account email is not verified.")
    # The hd claim is the hard tenancy check; the `hd` hint on the consent URL
    # is cosmetic and attacker-choosable, so verify the claim itself.
    if claims.get("hd") != hosted_domain():
        raise PermissionError(f"Account is not in the {hosted_domain()} workspace.")
    # Provisioning gate — delegated to access.py so it tracks whichever backend
    # is active (env allowlist today, GCS grant store once flipped). Deny-by-default.
    if not access.is_provisioned(email):
        raise PermissionError(
            f"{email} is not provisioned for the portal. Ask an admin to grant access."
        )
    return email


def parse_state(state: str) -> str:
    """Validate the state blob from the callback; return the `next` path."""
    payload = verify_blob(state)
    if payload is None:
        raise PermissionError("Login state expired or invalid — start again at /auth/login.")
    nxt = payload.get("next") or "/"
    # Only ever redirect within this origin.
    if not isinstance(nxt, str) or not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    return nxt


def make_session_cookie(email: str) -> str:
    return sign_blob({"email": email, "exp": time.time() + session_ttl()})


def verify_session(cookie_value: Optional[str]) -> Optional[str]:
    """Return the signed-in email, or None. Never raises; never fails open."""
    if not cookie_value:
        return None
    payload = verify_blob(cookie_value)
    if payload is None:
        return None
    email = (payload.get("email") or "").lower()
    if not email:
        return None
    # Re-check provisioning on every request so removals take effect
    # immediately, not at cookie expiry.
    if not access.is_provisioned(email):
        print(f"[auth] session for {email} rejected: not provisioned",
              file=sys.stderr)
        return None
    return email
