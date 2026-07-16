from concurrent.futures import ThreadPoolExecutor
from threading import Event

from app.services.dashboard_cache import DashboardCache, dashboard_ttl


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
    cache = DashboardCache(max_entries=2)
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
