"""
LLM adapter — the portal's ONLY module that talks to a language model.
=========================================================================
Two transports, checked in order:

  1. ANTHROPIC_API_KEY            -> api.anthropic.com directly
                                     (model: GVC_LLM_MODEL, default sonnet)
  2. GVC_CLAUDE_PROXY_URL         -> the GVC Claude proxy the Takeoff app
                                     already runs on Netlify
                                     (netlify/functions/claude.js in the
                                     takeoff repo; task-based contract;
                                     ships the GVC voice/system prompt and
                                     its own Anthropic key). DEFAULTS ON —
                                     set the env to "" to disable.

Neither configured (or proxy disabled) -> LLMNotConfigured, and callers show
their honest "coach isn't configured" state. Mirrors vision.py's contract:
import is always safe; network only on call; stdlib urllib like slack_notify
(no new deps in the deploy image).

NEVER used for customer-facing output. Suggestions land behind a human.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

DEFAULT_PROXY_URL = "https://gvctakeoff.netlify.app/.netlify/functions/claude"
DEFAULT_MODEL = "claude-sonnet-5"
_TIMEOUT_S = 90


class LLMNotConfigured(Exception):
    pass


class LLMError(Exception):
    pass


def _api_key() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def proxy_url() -> str:
    """Empty string == proxy explicitly disabled."""
    raw = os.environ.get("GVC_CLAUDE_PROXY_URL")
    if raw is None:
        return DEFAULT_PROXY_URL
    return raw.strip()


def transport() -> str:
    """'direct' | 'proxy' | '' (unconfigured). Direct key wins."""
    if _api_key():
        return "direct"
    if proxy_url():
        return "proxy"
    return ""


def _post_json(url: str, body: dict, headers: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise LLMError(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise LLMError(f"network: {e.reason}")


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def complete_json(task: str, prompt: str, *, max_tokens: int = 2000) -> dict:
    """Run `prompt`, expecting a JSON object back. Returns the parsed dict.

    `task` is a short slug — it names the proxy task (generic default branch)
    and shows up in error messages. Raises LLMNotConfigured / LLMError.
    """
    mode = transport()
    if mode == "direct":
        out = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": os.environ.get("GVC_LLM_MODEL") or DEFAULT_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"x-api-key": _api_key(), "anthropic-version": "2023-06-01"},
        )
        text = ""
        for block in out.get("content") or []:
            if block.get("type") == "text":
                text += block.get("text") or ""
        try:
            parsed = json.loads(_strip_fences(text))
        except json.JSONDecodeError:
            raise LLMError(f"{task}: model did not return valid JSON")
        if not isinstance(parsed, dict):
            raise LLMError(f"{task}: expected a JSON object")
        parsed["_model"] = out.get("model") or ""
        parsed["_source"] = "direct"
        return parsed

    if mode == "proxy":
        out = _post_json(proxy_url(),
                         {"task": task, "input": prompt,
                          "max_tokens": max_tokens}, {})
        if out.get("stub"):
            # Proxy lost ITS key — treat as unconfigured, not as a result.
            raise LLMNotConfigured(
                "The GVC Claude proxy is in stub mode (no API key on Netlify)."
            )
        if not out.get("ok"):
            raise LLMError(f"{task}: proxy error: {out.get('error') or 'unknown'}")
        parsed = out.get("output")
        if isinstance(parsed, dict) and "raw" in parsed and len(parsed) == 1:
            try:
                parsed = json.loads(_strip_fences(str(parsed["raw"])))
            except json.JSONDecodeError:
                raise LLMError(f"{task}: proxy returned non-JSON text")
        if not isinstance(parsed, dict):
            raise LLMError(f"{task}: expected a JSON object from the proxy")
        parsed["_model"] = out.get("model_used") or ""
        parsed["_source"] = "proxy"
        return parsed

    raise LLMNotConfigured(
        "No LLM configured — set ANTHROPIC_API_KEY or GVC_CLAUDE_PROXY_URL."
    )


# ---- /health probe (cached; a real call, not env presence) -----------------

_probe_cache: dict[str, Any] = {"at": 0.0, "result": None}
_PROBE_TTL_S = 3600.0


def probe() -> dict:
    """Cheap LIVE check for /health. Proxy: token-free __healthcheck task.
    Direct: models endpoint auth check. Cached 1h per instance."""
    now = time.monotonic()
    if _probe_cache["result"] is not None and now - _probe_cache["at"] < _PROBE_TTL_S:
        return _probe_cache["result"]
    mode = transport()
    result: dict[str, Any] = {"configured": bool(mode), "transport": mode,
                              "ok": False, "error": None}
    try:
        if mode == "proxy":
            out = _post_json(proxy_url(), {"task": "__healthcheck"}, {})
            result["ok"] = bool(out.get("hasKey"))
            if not out.get("hasKey"):
                result["error"] = "proxy has no ANTHROPIC_API_KEY (stub mode)"
        elif mode == "direct":
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/models?limit=1")
            req.add_header("x-api-key", _api_key())
            req.add_header("anthropic-version", "2023-06-01")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result["ok"] = resp.status == 200
        else:
            result["error"] = "not configured"
    except Exception as e:  # noqa: BLE001 — health must never raise
        result["error"] = f"{type(e).__name__}: {e}"
    _probe_cache["at"] = now
    _probe_cache["result"] = result
    return result
