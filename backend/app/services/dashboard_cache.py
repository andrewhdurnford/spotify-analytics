"""Small in-process cache for dashboard aggregate queries."""

from collections.abc import Callable, Hashable
from threading import Lock
from time import monotonic
from typing import TypeVar

T = TypeVar("T")

ALL_TIME_TTL_SECONDS = 15 * 60
FILTERED_TTL_SECONDS = 60
RECENTLY_PLAYED_TTL_SECONDS = 60
DETAIL_TTL_SECONDS = 5 * 60
RELATED_TRACKS_TTL_SECONDS = 60 * 60


class DashboardCache:
    def __init__(self, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._entries: dict[Hashable, tuple[float, object]] = {}
        self._lock = Lock()

    def get_or_set(
        self,
        key: Hashable,
        ttl_seconds: int,
        loader: Callable[[], T],
        *,
        cache_none: bool = True,
    ) -> T:
        now = self._clock()
        with self._lock:
            cached = self._entries.get(key)
            if cached and cached[0] > now:
                return cached[1]  # type: ignore[return-value]

        value = loader()

        if value is not None or cache_none:
            with self._lock:
                self._entries[key] = (self._clock() + ttl_seconds, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


dashboard_cache = DashboardCache()


def dashboard_ttl(timeframe: str) -> int:
    if timeframe == "all":
        return ALL_TIME_TTL_SECONDS
    return FILTERED_TTL_SECONDS
