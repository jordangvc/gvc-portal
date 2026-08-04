"""
Short-TTL in-process cache + singleflight for Monday read-heavy portal paths.
=========================================================================
Search/list endpoints hit Monday on every request today. Cloud Run keeps a
warm instance around, so a small per-process cache (30–120s) makes repeated
lookups and picker reloads feel instant without inventing a shared store.

Stale-while-revalidate (SWR): list hot paths can keep serving the last-known
payload for a longer stale window while a single background refresh runs, so
Job Start / Morning / Job Check feel instant after the first fill.

Env:
  GVC_MONDAY_SEARCH_CACHE_TTL   seconds for search keys (default 60)
  GVC_MONDAY_LIST_CACHE_TTL     seconds for full-list keys (default 90)
  GVC_MONDAY_STALE_TTL          seconds stale entries stay servable (default 900)

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
# key -> (fresh_until, stale_until, value)
_STORE: dict[str, tuple[float, float, Any]] = {}
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


def stale_ttl() -> float:
    """How long a cached list remains servable after write (incl. fresh window)."""
    try:
        return float(os.environ.get("GVC_MONDAY_STALE_TTL") or "900")
    except ValueError:
        return 900.0


def get(key: str) -> Optional[Any]:
    """Return cached value only while still fresh; None when missing/stale/expired."""
    now = time.monotonic()
    with _LOCK:
        hit = _STORE.get(key)
        if hit is None:
            return None
        fresh_until, entry_stale_until, value = hit
        if now >= entry_stale_until:
            _STORE.pop(key, None)
            return None
        if now >= fresh_until:
            return None
        return value


def put(key: str, value: Any, *, ttl: Optional[float] = None,
        stale_ttl: Optional[float] = None) -> Any:
    """
    Store `value`.

    `ttl` is the fresh window. `stale_ttl` is the total servable lifetime from
    now (defaults to `ttl` so non-SWR callers keep strict expire-on-fresh).
    """
    ttl_s = search_ttl() if ttl is None else float(ttl)
    ttl_s = max(0.0, ttl_s)
    if stale_ttl is None:
        stale_s = ttl_s
    else:
        stale_s = max(ttl_s, float(stale_ttl))
    now = time.monotonic()
    with _LOCK:
        _STORE[key] = (now + ttl_s, now + stale_s, value)
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


def stats() -> dict:
    """Optional debug snapshot for the warm endpoint / local inspection."""
    now = time.monotonic()
    with _LOCK:
        fresh = 0
        stale = 0
        for fresh_until, entry_stale_until, _value in _STORE.values():
            if now < fresh_until:
                fresh += 1
            elif now < entry_stale_until:
                stale += 1
        return {
            "keys": len(_STORE),
            "fresh": fresh,
            "stale": stale,
            "inflight": len(_INFLIGHT),
        }


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
            return cached_hit[2]
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


def get_or_set_swr(key: str, factory: Callable[[], T], *,
                   ttl: Optional[float] = None,
                   stale_ttl: Optional[float] = None) -> T:
    """
    Stale-while-revalidate get-or-set.

    - Fresh (within ttl): return cached.
    - Stale (past ttl, within stale_ttl): return cached immediately and kick a
      daemon thread to refresh (singleflight — only one refresh at a time).
    - Missing / past stale_ttl: blocking factory, same as get_or_set.
    """
    ttl_s = list_ttl() if ttl is None else float(ttl)
    # Param name shadows the module-level stale_ttl(); resolve env default here.
    try:
        if stale_ttl is None:
            stale_s = float(os.environ.get("GVC_MONDAY_STALE_TTL") or "900")
        else:
            stale_s = float(stale_ttl)
    except ValueError:
        stale_s = 900.0

    now = time.monotonic()
    serve_stale = False
    stale_value: Any = None
    start_refresh = False
    refresh_event: Optional[threading.Event] = None
    blocking_event: Optional[threading.Event] = None
    blocking_leader = False

    with _LOCK:
        hit = _STORE.get(key)
        if hit is not None:
            fresh_until, entry_stale_until, value = hit
            if now >= entry_stale_until:
                _STORE.pop(key, None)
                hit = None
            elif now < fresh_until:
                return value
            else:
                # Stale but still servable.
                serve_stale = True
                stale_value = value
                if key not in _INFLIGHT:
                    refresh_event = threading.Event()
                    _INFLIGHT[key] = refresh_event
                    start_refresh = True

        if not serve_stale:
            # Missing / fully expired — blocking singleflight populate.
            event = _INFLIGHT.get(key)
            if event is None:
                event = threading.Event()
                _INFLIGHT[key] = event
                blocking_leader = True
            blocking_event = event

    if serve_stale:
        if start_refresh:
            assert refresh_event is not None

            def _bg_refresh() -> None:
                try:
                    value = factory()
                    put(key, value, ttl=ttl_s, stale_ttl=stale_s)
                except Exception as exc:  # noqa: BLE001 — keep serving stale
                    print(f"[monday:cache] SWR refresh failed for {key}: "
                          f"{type(exc).__name__}: {exc}", flush=True)
                finally:
                    with _LOCK:
                        _INFLIGHT.pop(key, None)
                    refresh_event.set()

            threading.Thread(
                target=_bg_refresh, name=f"monday-swr:{key}", daemon=True
            ).start()
        return stale_value

    # Blocking path (same semantics as get_or_set).
    assert blocking_event is not None
    if not blocking_leader:
        blocking_event.wait(timeout=35)
        with _LOCK:
            hit = _STORE.get(key)
            if hit is not None and time.monotonic() < hit[1]:
                return hit[2]
        return factory()

    try:
        value = factory()
        put(key, value, ttl=ttl_s, stale_ttl=stale_s)
        return value
    finally:
        with _LOCK:
            _INFLIGHT.pop(key, None)
        blocking_event.set()
