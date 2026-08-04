"""Small in-process, bounded TTL/LRU cache used across the API.

Used for dashboard aggregate queries and the currently-playing lookup. A
single bounded pool keeps total memory use
predictable regardless of how many distinct query-parameter combinations
callers request (arbitrary timeframe/date/limit/track_id combinations
cannot grow the cache without bound -- once `maxsize` entries are held, the
least-recently-used entry is evicted to make room for a new one).

This cache intentionally stays process-local. Local playback sync clears the
current process after writes; out-of-process imports and multi-worker
deployments rely on the short TTLs below for freshness.
"""

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Hashable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import TypeVar

T = TypeVar("T")

# Sentinel distinguishing "no fresh entry" from a cached ``None`` value.
_MISS = object()

ALL_TIME_TTL_SECONDS = 15 * 60
FILTERED_TTL_SECONDS = 60
CURRENTLY_PLAYING_TTL_SECONDS = 12
RECENTLY_PLAYED_TTL_SECONDS = 60
DETAIL_TTL_SECONDS = 5 * 60

DEFAULT_MAXSIZE = 250


class DashboardCache:
    """Bounded, thread-safe TTL cache with LRU eviction and single-flight loads.

    Entries are stored with a per-entry expiry. When the number of stored
    entries would exceed `maxsize`, the least-recently-used entry is evicted
    (an entry counts as "used" both when it's stored and when it's read).

    Concurrent misses on the same key run the loader once: the first caller
    loads while the others wait on a per-key lock and then read the stored
    value. Without that, a burst of simultaneous requests would each run the
    expensive query the cache exists to avoid.
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
        # key -> [lock, waiter_count]; entries are dropped once the last
        # in-flight loader for that key finishes, so these can't accumulate.
        self._sync_loads: dict[Hashable, list] = {}
        self._async_loads: dict[Hashable, list] = {}

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_or_set(
        self,
        key: Hashable,
        ttl_seconds: int,
        loader: Callable[[], T],
        cache_none: bool = True,
    ) -> T:
        """Return the cached value for `key`, loading it if absent/expired.

        `cache_none=False` skips storing a `None` result, so lookups for
        nonexistent entities don't occupy a slot (and evict real aggregates)
        for the whole TTL.
        """
        cached = self._get_fresh(key)
        if cached is not _MISS:
            return cached  # type: ignore[return-value]

        with self._single_flight(key):
            # Another caller may have populated the entry while we waited.
            cached = self._get_fresh(key)
            if cached is not _MISS:
                return cached  # type: ignore[return-value]

            value = loader()
            if value is not None or cache_none:
                with self._lock:
                    self._store(key, ttl_seconds, value)
            return value

    async def get_or_set_async(
        self,
        key: Hashable,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[T]],
        cache_none: bool = True,
    ) -> T:
        """Async counterpart of `get_or_set` for async loaders (e.g. outbound
        HTTP calls). The lock is only held around the dict access, never
        across the `await`, so it's safe to share with the sync API.
        """
        cached = self._get_fresh(key)
        if cached is not _MISS:
            return cached  # type: ignore[return-value]

        async with self._single_flight_async(key):
            cached = self._get_fresh(key)
            if cached is not _MISS:
                return cached  # type: ignore[return-value]

            value = await loader()
            if value is not None or cache_none:
                with self._lock:
                    self._store(key, ttl_seconds, value)
            return value

    def _get_fresh(self, key: Hashable) -> object:
        """Return the unexpired cached value for `key`, or `_MISS`."""
        now = self._clock()
        with self._lock:
            self._delete_expired(now)
            cached = self._entries.get(key)
            if cached is not None and cached[0] > now:
                self._entries.move_to_end(key)
                return cached[1]
        return _MISS

    def _checkout_load_lock(self, registry: dict, key: Hashable, factory: Callable):
        with self._lock:
            entry = registry.get(key)
            if entry is None:
                entry = [factory(), 0]
                registry[key] = entry
            entry[1] += 1
            return entry

    def _release_load_lock(self, registry: dict, key: Hashable, entry: list) -> None:
        with self._lock:
            entry[1] -= 1
            if entry[1] <= 0 and registry.get(key) is entry:
                del registry[key]

    @contextmanager
    def _single_flight(self, key: Hashable) -> Iterator[None]:
        entry = self._checkout_load_lock(self._sync_loads, key, Lock)
        try:
            with entry[0]:
                yield
        finally:
            self._release_load_lock(self._sync_loads, key, entry)

    @asynccontextmanager
    async def _single_flight_async(self, key: Hashable) -> AsyncIterator[None]:
        entry = self._checkout_load_lock(self._async_loads, key, asyncio.Lock)
        try:
            async with entry[0]:
                yield
        finally:
            self._release_load_lock(self._async_loads, key, entry)

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

    def _delete_expired(self, now: float) -> None:
        expired_keys = [
            key for key, (expires_at, _) in self._entries.items() if expires_at <= now
        ]
        for key in expired_keys:
            self._entries.pop(key, None)


dashboard_cache = DashboardCache()


def dashboard_ttl(timeframe: str) -> int:
    if timeframe == "all":
        return ALL_TIME_TTL_SECONDS
    return FILTERED_TTL_SECONDS


def detail_ttl(timeframe: str) -> int:
    if timeframe == "all":
        return DETAIL_TTL_SECONDS
    return dashboard_ttl(timeframe)


def cache_window_key(timeframe: str, base_date: datetime, date_param: str | None) -> str:
    if timeframe == "all":
        return "all"
    if date_param:
        return base_date.isoformat()
    return "current"
