from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.services.dashboard_cache import (
    DEFAULT_MAXSIZE,
    DashboardCache,
    dashboard_cache,
    dashboard_ttl,
)


def test_default_maxsize_is_250():
    assert DEFAULT_MAXSIZE == 250
    assert DashboardCache().maxsize == 250
    assert dashboard_cache.maxsize == 250


def test_maxsize_must_be_at_least_one():
    with pytest.raises(ValueError):
        DashboardCache(maxsize=0)


def test_cache_reuses_value_until_ttl_expires():
    now = [100.0]
    cache = DashboardCache(clock=lambda: now[0])
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return {"calls": calls}

    assert cache.get_or_set("stats", 60, load) == {"calls": 1}
    assert cache.get_or_set("stats", 60, load) == {"calls": 1}

    now[0] += 61

    assert cache.get_or_set("stats", 60, load) == {"calls": 2}


def test_clear_invalidates_cached_values():
    cache = DashboardCache()
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return calls

    assert cache.get_or_set("songs", 60, load) == 1
    cache.clear()
    assert cache.get_or_set("songs", 60, load) == 2


def test_can_skip_caching_none_values():
    cache = DashboardCache()
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return None

    assert cache.get_or_set("missing", 60, load, cache_none=False) is None
    assert cache.get_or_set("missing", 60, load, cache_none=False) is None
    assert calls == 2


def test_cache_evicts_least_recently_used_value():
    cache = DashboardCache(maxsize=2)
    calls: dict[str, int] = {}

    def load(key: str) -> str:
        calls[key] = calls.get(key, 0) + 1
        return f"{key}-{calls[key]}"

    assert cache.get_or_set("a", 60, lambda: load("a")) == "a-1"
    assert cache.get_or_set("b", 60, lambda: load("b")) == "b-1"
    assert cache.get_or_set("a", 60, lambda: load("a")) == "a-1"
    assert cache.get_or_set("c", 60, lambda: load("c")) == "c-1"

    assert cache.get_or_set("b", 60, lambda: load("b")) == "b-2"
    assert calls == {"a": 1, "b": 2, "c": 1}


def test_concurrent_cold_loads_share_loader_result():
    cache = DashboardCache()
    calls = 0
    loader_started = Event()
    release_loader = Event()

    def load():
        nonlocal calls
        calls += 1
        loader_started.set()
        assert release_loader.wait(2)
        return {"calls": calls}

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get_or_set, "stats", 60, load)
        assert loader_started.wait(2)
        second = executor.submit(cache.get_or_set, "stats", 60, load)

        release_loader.set()

        assert first.result(timeout=2) == {"calls": 1}
        assert second.result(timeout=2) == {"calls": 1}

    assert calls == 1


def test_all_time_results_have_longer_ttl():
    assert dashboard_ttl("all") > dashboard_ttl("month")


class TestBoundedEviction:
    def test_len_reflects_stored_entries(self):
        cache = DashboardCache(maxsize=10)
        cache.get_or_set("a", 1000, lambda: 1)
        cache.get_or_set("b", 1000, lambda: 2)
        assert len(cache) == 2

    def test_never_exceeds_maxsize(self):
        cache = DashboardCache(maxsize=3)
        for i in range(10):
            cache.get_or_set(f"k{i}", 1000, lambda i=i: i)
            assert len(cache) <= 3
        assert len(cache) == 3

    def test_evicts_least_recently_used_entry_when_over_capacity(self):
        cache = DashboardCache(maxsize=3)
        for key in ("k0", "k1", "k2"):
            cache.get_or_set(key, 1000, lambda key=key: key)

        # Insert a 4th distinct key -> k0 (least recently used) is evicted.
        cache.get_or_set("k3", 1000, lambda: "k3")

        calls = []
        cache.get_or_set("k0", 1000, lambda: calls.append("reloaded") or "k0-reloaded")
        assert calls == ["reloaded"]  # loader was called again: k0 was evicted

    def test_reading_an_entry_refreshes_its_lru_position(self):
        cache = DashboardCache(maxsize=3)
        for key in ("k0", "k1", "k2"):
            cache.get_or_set(key, 1000, lambda key=key: key)

        # Touch k0 so it's no longer the least-recently-used entry.
        cache.get_or_set("k0", 1000, lambda: "should-not-reload")

        # k1 is now the least-recently-used entry and should be evicted.
        cache.get_or_set("k3", 1000, lambda: "k3")

        calls = []
        cache.get_or_set("k0", 1000, lambda: calls.append("k0-reloaded") or "k0-reloaded")
        assert calls == []  # k0 was still cached, untouched

        calls = []
        cache.get_or_set("k1", 1000, lambda: calls.append("k1-reloaded") or "k1-reloaded")
        assert calls == ["k1-reloaded"]  # k1 was evicted

    def test_arbitrary_key_combinations_cannot_grow_cache_unbounded(self):
        """Simulates many distinct (timeframe, date, limit) combinations --
        the cache must stay within maxsize regardless of how many distinct
        query-parameter combinations callers request.
        """
        cache = DashboardCache(maxsize=250)
        for i in range(10_000):
            key = ("songs", "month", f"2025-{i % 12 + 1:02d}-01", i % 50 + 1)
            cache.get_or_set(key, 1000, lambda i=i: i)
        assert len(cache) == 250


class TestSingleFlight:
    def test_concurrent_misses_run_the_loader_once(self):
        import threading

        cache = DashboardCache()
        calls = []
        started = threading.Event()
        release = threading.Event()

        def load():
            calls.append("load")
            started.set()
            release.wait(timeout=5)
            return "value"

        first = threading.Thread(target=lambda: cache.get_or_set("k", 1000, load))
        first.start()
        assert started.wait(timeout=5)

        results = []
        waiters = [
            threading.Thread(target=lambda: results.append(cache.get_or_set("k", 1000, load)))
            for _ in range(4)
        ]
        for waiter in waiters:
            waiter.start()

        release.set()
        first.join(timeout=5)
        for waiter in waiters:
            waiter.join(timeout=5)

        assert calls == ["load"]
        assert results == ["value"] * 4

    def test_distinct_keys_are_not_serialized_behind_each_other(self):
        import threading

        cache = DashboardCache()
        release = threading.Event()
        loaded_b = threading.Event()

        def slow_a():
            release.wait(timeout=5)
            return "a"

        def load_b():
            loaded_b.set()
            return "b"

        thread_a = threading.Thread(target=lambda: cache.get_or_set("a", 1000, slow_a))
        thread_a.start()
        assert cache.get_or_set("b", 1000, load_b) == "b"
        assert loaded_b.is_set()

        release.set()
        thread_a.join(timeout=5)

    def test_load_locks_do_not_accumulate(self):
        cache = DashboardCache()
        for i in range(100):
            cache.get_or_set(f"k{i}", 1000, lambda i=i: i)
        assert cache._sync_loads == {}


class TestNegativeCaching:
    def test_none_results_are_cached_by_default(self):
        cache = DashboardCache()
        calls = []

        def load():
            calls.append("load")
            return None

        assert cache.get_or_set("k", 1000, load) is None
        assert cache.get_or_set("k", 1000, load) is None
        assert calls == ["load"]
        assert len(cache) == 1

    def test_none_results_are_not_stored_when_cache_none_is_false(self):
        """Lookups for nonexistent entities shouldn't occupy a slot in the
        shared pool (and evict real aggregates) for the whole TTL.
        """
        cache = DashboardCache()
        calls = []

        def load():
            calls.append("load")
            return None

        assert cache.get_or_set("k", 1000, load, cache_none=False) is None
        assert cache.get_or_set("k", 1000, load, cache_none=False) is None
        assert calls == ["load", "load"]
        assert len(cache) == 0

    def test_non_none_results_are_still_cached_when_cache_none_is_false(self):
        cache = DashboardCache()
        calls = []

        def load():
            calls.append("load")
            return "value"

        assert cache.get_or_set("k", 1000, load, cache_none=False) == "value"
        assert cache.get_or_set("k", 1000, load, cache_none=False) == "value"
        assert calls == ["load"]
