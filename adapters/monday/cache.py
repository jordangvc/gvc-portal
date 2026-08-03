"""
Short-TTL in-process cache + singleflight for Monday read-heavy portal paths.
=========================================================================
Search/list endpoints hit Monday on every request today. Cloud Run keeps a
warm instance around, so a small per-process cache (30–120s) makes repeated
lookups and picker reloads feel instant without inventing a shared store.

Env:
  GVC_MONDAY_SEARCH_CACHE_TTL   seconds for search keys (default 60)
  GVC_MONDAY_LIST_CACHE_TTL     seconds for full-list keys (default 90)

Not a correctness layer — writes should invalidate the keys they stale, and
callers must tolerate brief staleness.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

_LOCK = threading.Lock()
_STORE: dict[str, tuple[float, Any]] = {}
# key -> Event that waiters block on while the first caller populates
_INFLIGHT: dict[str, threading.Event] = {}


def search_ttl() -> float:
    try:
        return float(os.environ.get("GVC_MONDAY_SEARCH_CACHE_TTL") or "60")
    except ValueError:
        return 60.0


def list_ttl() -> float:
    try:
        return float(os.environ.get("GVC_MONDAY_LIST_CACHE_TTL") or "90")
    except ValueError:
        return 90.0


def get(key: str) -> Optional[Any]:
    """Return cached value or None when missing/expired."""
    now = time.monotonic()
    with _LOCK:
        hit = _STORE.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if now >= expires_at:
            _STORE.pop(key, None)
            return None
        return value


def put(key: str, value: Any, *, ttl: Optional[float] = None) -> Any:
    ttl_s = search_ttl() if ttl is None else float(ttl)
    with _LOCK:
        _STORE[key] = (time.monotonic() + max(0.0, ttl_s), value)
    return value


def invalidate(*keys: str) -> None:
    """Drop exact keys."""
    if not keys:
        return
    with _LOCK:
        for key in keys:
            _STORE.pop(key, None)


def invalidate_prefix(prefix: str) -> None:
    """Drop every key that starts with `prefix`."""
    with _LOCK:
        for key in [k for k in _STORE if k.startswith(prefix)]:
            _STORE.pop(key, None)


def clear() -> None:
    """Test helper: wipe the whole cache."""
    with _LOCK:
        _STORE.clear()
        _INFLIGHT.clear()


def get_or_set(key: str, factory: Callable[[], T], *,
               ttl: Optional[float] = None) -> T:
    """
    Return cached value, or call `factory` once (singleflight) and store it.
    Concurrent callers for the same key wait for the first factory to finish
    instead of stampeding Monday.
    """
    cached = get(key)
    if cached is not None:
        return cached

    leader = False
    event: Optional[threading.Event] = None
    with _LOCK:
        cached_hit = _STORE.get(key)
        if cached_hit is not None and time.monotonic() < cached_hit[0]:
            return cached_hit[1]
        event = _INFLIGHT.get(key)
        if event is None:
            event = threading.Event()
            _INFLIGHT[key] = event
            leader = True

    if not leader:
        # Wait for the leader; then read through (or build if leader failed).
        assert event is not None
        event.wait(timeout=35)
        cached = get(key)
        if cached is not None:
            return cached
        return factory()

    try:
        value = factory()
        put(key, value, ttl=ttl)
        return value
    finally:
        with _LOCK:
            _INFLIGHT.pop(key, None)
        event.set()
