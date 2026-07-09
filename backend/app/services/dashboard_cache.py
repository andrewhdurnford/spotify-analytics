"""Small in-process, bounded TTL/LRU cache used across the API.

Used for dashboard aggregate queries, the currently-playing lookup, and
related-track calculations. A single bounded pool keeps total memory use
predictable regardless of how many distinct query-parameter combinations
callers request (arbitrary timeframe/date/limit/track_id combinations
cannot grow the cache without bound -- once `maxsize` entries are held, the
least-recently-used entry is evicted to make room for a new one).
"""

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from threading import Lock
from time import monotonic
from typing import TypeVar

T = TypeVar("T")

ALL_TIME_TTL_SECONDS = 15 * 60
FILTERED_TTL_SECONDS = 60
CURRENTLY_PLAYING_TTL_SECONDS = 12
RELATED_TRACKS_TTL_SECONDS = 15 * 60

DEFAULT_MAXSIZE = 250


class DashboardCache:
    """Bounded, thread-safe TTL cache with LRU eviction.

    Entries are stored with a per-entry expiry. When the number of stored
    entries would exceed `maxsize`, the least-recently-used entry is evicted
    (an entry counts as "used" both when it's stored and when it's read).
    """

    def __init__(
        self,
        clock: Callable[[], float] = monotonic,
        maxsize: int = DEFAULT_MAXSIZE,
    ):
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self._clock = clock
        self._maxsize = maxsize
        self._entries: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()
        self._lock = Lock()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_or_set(self, key: Hashable, ttl_seconds: int, loader: Callable[[], T]) -> T:
        now = self._clock()
        with self._lock:
            cached = self._entries.get(key)
            if cached and cached[0] > now:
                self._entries.move_to_end(key)
                return cached[1]  # type: ignore[return-value]

        value = loader()

        with self._lock:
            self._store(key, ttl_seconds, value)
        return value

    async def get_or_set_async(
        self, key: Hashable, ttl_seconds: int, loader: Callable[[], Awaitable[T]]
    ) -> T:
        """Async counterpart of `get_or_set` for async loaders (e.g. outbound
        HTTP calls). The lock is only held around the dict access, never
        across the `await`, so it's safe to share with the sync API.
        """
        now = self._clock()
        with self._lock:
            cached = self._entries.get(key)
            if cached and cached[0] > now:
                self._entries.move_to_end(key)
                return cached[1]  # type: ignore[return-value]

        value = await loader()

        with self._lock:
            self._store(key, ttl_seconds, value)
        return value

    def _store(self, key: Hashable, ttl_seconds: int, value: object) -> None:
        """Insert/refresh an entry and evict LRU entries over capacity.

        Must be called while holding `self._lock`.
        """
        self._entries[key] = (self._clock() + ttl_seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._maxsize:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


dashboard_cache = DashboardCache()


def dashboard_ttl(timeframe: str) -> int:
    if timeframe == "all":
        return ALL_TIME_TTL_SECONDS
    return FILTERED_TTL_SECONDS
