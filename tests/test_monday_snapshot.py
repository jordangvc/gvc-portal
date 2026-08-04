"""Unit tests for adapters/monday/snapshot.py (no GCS network)."""
from __future__ import annotations

import time

from adapters.monday import snapshot as monday_snapshot


def setup_function() -> None:
    monday_snapshot.clear_hooks()


def test_object_name_sanitizes_key():
    name = monday_snapshot.object_name("list:jobstart:bids")
    assert name.startswith("monday-cache/")
    assert name.endswith(".json")
    assert ":" in name or "jobstart" in name


def test_hooks_roundtrip_and_max_age(monkeypatch):
    store: dict = {}
    monday_snapshot.set_hooks(
        load=lambda key: (store[key]["v"], store[key]["t"]) if key in store else None,
        save=lambda key, value: store.__setitem__(
            key, {"v": value, "t": time.time()}
        ),
        delete=lambda key: store.pop(key, None),
    )
    assert monday_snapshot.save("k", {"ok": True}) is True
    hit = monday_snapshot.load("k")
    assert hit is not None
    assert hit[0] == {"ok": True}

    # Age out via max_age
    monkeypatch.setenv("GVC_MONDAY_SNAPSHOT_MAX_AGE", "0.01")
    store["k"]["t"] = time.time() - 1
    assert monday_snapshot.load("k") is None
    monday_snapshot.delete("k")
    assert "k" not in store
