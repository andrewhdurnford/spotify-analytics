"""In-process, per-key rate limiting.

A fixed-window counter per key (typically a client IP). Simple and cheap:
good enough to blunt casual abuse/scraping of expensive endpoints on a
single-process deployment. It intentionally does not attempt to be a
perfectly smooth sliding window -- a client can burst up to ~2x the limit
across a window boundary, which is an acceptable tradeoff for the
simplicity here.

The tracked-key table is itself bounded (LRU-evicted) so that a flood of
requests from many distinct/spoofed keys cannot grow memory without bound.
"""

from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from time import monotonic

DEFAULT_MAX_TRACKED_KEYS = 5000


class RateLimiter:
    """Fixed-window per-key rate limiter, bounded to a max number of keys."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        max_tracked_keys: int = DEFAULT_MAX_TRACKED_KEYS,
        clock: Callable[[], float] = monotonic,
    ):
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_tracked_keys = max_tracked_keys
        self._clock = clock
        # key -> (window_index, count)
        self._buckets: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Return True and record the request if `key` is within its
        current window's budget; return False (without recording anything
        further) once the budget is exhausted.
        """
        window_index = int(self._clock() // self.window_seconds)
        with self._lock:
            entry = self._buckets.get(key)
            if entry is None or entry[0] != window_index:
                self._buckets[key] = (window_index, 1)
                self._buckets.move_to_end(key)
                self._evict_locked()
                return True

            _, count = entry
            if count >= self.max_requests:
                self._buckets.move_to_end(key)
                return False

            self._buckets[key] = (window_index, count + 1)
            self._buckets.move_to_end(key)
            return True

    def _evict_locked(self) -> None:
        while len(self._buckets) > self.max_tracked_keys:
            self._buckets.popitem(last=False)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# One bucket per concern, per the roadmap thresholds.
general_limiter = RateLimiter(max_requests=60, window_seconds=60)
currently_playing_limiter = RateLimiter(max_requests=10, window_seconds=60)
related_tracks_limiter = RateLimiter(max_requests=5, window_seconds=60)
cron_limiter = RateLimiter(max_requests=2, window_seconds=60)

_all_limiters = (
    general_limiter,
    currently_playing_limiter,
    related_tracks_limiter,
    cron_limiter,
)


def reset_all_rate_limiters() -> None:
    """Test helper: clear all module-level limiter state."""
    for limiter in _all_limiters:
        limiter.reset()
