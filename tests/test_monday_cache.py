"""Unit tests for adapters/monday/cache.py (no Monday network)."""
from __future__ import annotations

import threading
import time

from adapters.monday import cache as monday_cache


def setup_function() -> None:
    monday_cache.clear()


def test_get_or_set_stores_and_hits():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return {"ok": True}

    a = monday_cache.get_or_set("k1", factory, ttl=30)
    b = monday_cache.get_or_set("k1", factory, ttl=30)
    assert a == b == {"ok": True}
    assert calls["n"] == 1


def test_expire_and_invalidate():
    monday_cache.put("k2", "v", ttl=0.05)
    assert monday_cache.get("k2") == "v"
    time.sleep(0.06)
    assert monday_cache.get("k2") is None

    monday_cache.put("pref:a", 1, ttl=30)
    monday_cache.put("pref:b", 2, ttl=30)
    monday_cache.put("other", 3, ttl=30)
    monday_cache.invalidate_prefix("pref:")
    assert monday_cache.get("pref:a") is None
    assert monday_cache.get("other") == 3


def test_singleflight_coalesces_concurrent_misses():
    calls = {"n": 0}
    barrier = threading.Barrier(3)

    def factory():
        calls["n"] += 1
        time.sleep(0.05)
        return "done"

    results: list = []

    def worker():
        barrier.wait()
        results.append(monday_cache.get_or_set("sf", factory, ttl=30))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["done", "done", "done"]
    assert calls["n"] == 1


def test_swr_fresh_hit_skips_factory():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return "fresh"

    a = monday_cache.get_or_set_swr("swr-fresh", factory, ttl=30, stale_ttl=60)
    b = monday_cache.get_or_set_swr("swr-fresh", factory, ttl=30, stale_ttl=60)
    assert a == b == "fresh"
    assert calls["n"] == 1


def test_swr_returns_stale_while_refresh_runs():
    calls = {"n": 0}
    release = threading.Event()

    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            return "v1"
        release.wait(timeout=2)
        return "v2"

    assert monday_cache.get_or_set_swr(
        "swr-stale", factory, ttl=0.05, stale_ttl=2.0
    ) == "v1"
    time.sleep(0.08)  # leave fresh window, stay within stale

    t0 = time.monotonic()
    got = monday_cache.get_or_set_swr(
        "swr-stale", factory, ttl=0.05, stale_ttl=2.0
    )
    elapsed = time.monotonic() - t0
    assert got == "v1"
    assert elapsed < 0.05  # must not block on the slow refresh

    # Background refresh should have started (singleflight).
    deadline = time.time() + 1
    while calls["n"] < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert calls["n"] == 2

    release.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        with monday_cache._LOCK:
            hit = monday_cache._STORE.get("swr-stale")
            inflight = "swr-stale" in monday_cache._INFLIGHT
        if hit and hit[2] == "v2" and not inflight:
            break
        time.sleep(0.02)
    assert monday_cache._STORE["swr-stale"][2] == "v2"


def test_swr_past_stale_blocks_and_refreshes():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return f"v{calls['n']}"

    monday_cache.put("swr-expired", "old", ttl=0.05, stale_ttl=0.1)
    time.sleep(0.12)
    got = monday_cache.get_or_set_swr(
        "swr-expired", factory, ttl=30, stale_ttl=60
    )
    assert got == "v1"
    assert calls["n"] == 1


def test_stats_reports_keys():
    monday_cache.put("stats-a", 1, ttl=30)
    monday_cache.put("stats-b", 2, ttl=30)
    s = monday_cache.stats()
    assert s["keys"] >= 2
    assert s["fresh"] >= 2
