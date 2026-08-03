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
