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


class TestAsyncCache:
    async def test_get_or_set_async_caches_result(self):
        cache = DashboardCache()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            return {"calls": calls}

        first = await cache.get_or_set_async("key", 1000, load)
        second = await cache.get_or_set_async("key", 1000, load)
        assert first == {"calls": 1}
        assert second == {"calls": 1}

    async def test_get_or_set_async_respects_ttl_expiry(self):
        now = [0.0]
        cache = DashboardCache(clock=lambda: now[0])
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            return calls

        assert await cache.get_or_set_async("key", 10, load) == 1
        now[0] += 11
        assert await cache.get_or_set_async("key", 10, load) == 2

    async def test_get_or_set_async_participates_in_lru_eviction(self):
        cache = DashboardCache(maxsize=2)
        await cache.get_or_set_async("a", 1000, _const_async("a"))
        await cache.get_or_set_async("b", 1000, _const_async("b"))
        await cache.get_or_set_async("c", 1000, _const_async("c"))
        assert len(cache) == 2

        calls = []

        async def reload_a():
            calls.append("a")
            return "a-reloaded"

        await cache.get_or_set_async("a", 1000, reload_a)
        assert calls == ["a"]  # "a" was evicted when "c" was inserted


def _const_async(value):
    async def _load():
        return value

    return _load
