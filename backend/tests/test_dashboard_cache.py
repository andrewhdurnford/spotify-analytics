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


def test_all_time_results_have_longer_ttl():
    assert dashboard_ttl("all") > dashboard_ttl("month")
