"""Centralized filesystem paths — single source for repo-relative locations.

Modules live in subpackages now, so ``Path(__file__).parent`` no longer equals
the repo root. Everything that needs templates/, assets/, web/, .env, or the
local credential fallbacks imports from here instead.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT  # backwards-compat alias

TEMPLATES_DIR = REPO_ROOT / "templates"
ASSETS_DIR = REPO_ROOT / "assets"
WEB_DIR = REPO_ROOT / "web"
OUTPUT_DIR = Path(os.environ.get("GVC_OUTPUT_DIR", REPO_ROOT / "output"))
LOGS_DIR = Path(os.environ.get("GVC_LOGS_DIR", REPO_ROOT / "logs"))

ENV_FILE = REPO_ROOT / ".env"
DEFAULT_SA_PATH = REPO_ROOT / ".google-service-account.json"
DEFAULT_GMAIL_TOKEN_PATH = REPO_ROOT / ".gmail-token.json"
DEFAULT_OAUTH_CLIENT_PATH = REPO_ROOT / ".google-oauth-client.json"
