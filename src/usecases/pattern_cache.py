"""Async TTL + LRU pattern cache.

Port of stunnel.concurrent.Cached + KeyedCache (Scala).
Thread-safe via asyncio.Lock; backed by cachetools.TTLCache. [RM][PA]
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from cachetools import TTLCache

from src.domain.models import Pattern
from src.framework.constants import MAX_CACHED_PATTERNS, PATTERN_CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)


class PatternCache:
    """Thread-safe async cache for Pattern objects keyed by pattern_id.

    Uses a TTLCache so stale patterns expire automatically, plus an LRU
    eviction policy when capacity is reached.
    """

    def __init__(
        self,
        maxsize: int = MAX_CACHED_PATTERNS,
        ttl: int = PATTERN_CACHE_TTL_SECONDS,
    ) -> None:
        self._cache: TTLCache[int, Pattern | None] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = asyncio.Lock()
        self._in_flight: dict[int, asyncio.Event] = {}

    async def get_or_load(
        self,
        pattern_id: int,
        loader: Callable[[int], Awaitable[Pattern | None]],
    ) -> Pattern | None:
        """Return cached pattern or call `loader` to fetch it.

        Only one coroutine fetches a given pattern_id at a time; others wait
        for the in-flight request to complete (dog-pile prevention). [PA]
        """
        async with self._lock:
            if pattern_id in self._cache:
                return self._cache[pattern_id]

            # If another coroutine is already fetching this id, wait for it
            if pattern_id in self._in_flight:
                event = self._in_flight[pattern_id]
            else:
                event = asyncio.Event()
                self._in_flight[pattern_id] = event
                event = None  # caller must do the fetch

        if event is not None:
            # Another coroutine is fetching — wait and retry
            await event.wait()
            async with self._lock:
                return self._cache.get(pattern_id)

        # We are responsible for loading
        try:
            result = await loader(pattern_id)
            async with self._lock:
                self._cache[pattern_id] = result
            return result
        except Exception:
            logger.exception("Pattern cache load failed for pattern_id=%d", pattern_id)
            return None
        finally:
            async with self._lock:
                event = self._in_flight.pop(pattern_id, None)
            if event is not None:
                event.set()

    def invalidate(self, pattern_id: int) -> None:
        """Remove a specific pattern from cache."""
        self._cache.pop(pattern_id, None)

    def clear(self) -> None:
        """Clear all cached patterns."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
