"""Small in-process cache for dashboard aggregate queries.

This cache intentionally stays process-local. Local playback sync clears the
current process after writes; out-of-process imports and multi-worker
deployments rely on the short TTLs below for freshness.
"""

from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event, Lock
from time import monotonic
from typing import TypeVar

T = TypeVar("T")

ALL_TIME_TTL_SECONDS = 15 * 60
FILTERED_TTL_SECONDS = 60
RECENTLY_PLAYED_TTL_SECONDS = 60
DETAIL_TTL_SECONDS = 5 * 60
RELATED_TRACKS_TTL_SECONDS = 60 * 60
DEFAULT_MAX_ENTRIES = 512


@dataclass
class _InflightLoad:
    done: Event = field(default_factory=Event)
    value: object | None = None
    error: BaseException | None = None


class DashboardCache:
    def __init__(
        self,
        clock: Callable[[], float] = monotonic,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._clock = clock
        self._max_entries = max_entries
        self._entries: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()
        self._inflight: dict[Hashable, _InflightLoad] = {}
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
            self._delete_expired(now)
            cached = self._entries.get(key)
            if cached and cached[0] > now:
                self._entries.move_to_end(key)
                return cached[1]  # type: ignore[return-value]

            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = _InflightLoad()
                self._inflight[key] = inflight
                should_load = True
            else:
                should_load = False

        if not should_load:
            inflight.done.wait()
            if inflight.error is not None:
                raise inflight.error
            return inflight.value  # type: ignore[return-value]

        try:
            value = loader()
        except BaseException as exc:
            with self._lock:
                inflight.error = exc
                self._inflight.pop(key, None)
                inflight.done.set()
            raise

        with self._lock:
            inflight.value = value
            if value is not None or cache_none:
                self._entries[key] = (self._clock() + ttl_seconds, value)
                self._entries.move_to_end(key)
                self._evict_oldest()
            self._inflight.pop(key, None)
            inflight.done.set()
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _delete_expired(self, now: float) -> None:
        expired_keys = [
            key for key, (expires_at, _) in self._entries.items() if expires_at <= now
        ]
        for key in expired_keys:
            self._entries.pop(key, None)

    def _evict_oldest(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


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
