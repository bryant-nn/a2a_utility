"""CachedPermissionService — a TTL cache in front of any PermissionServicePort.

The gate consults the permission service on *every* request, so an
uncached remote lookup puts a network round trip on the critical path of
every A2A call. This wraps any implementation with a small in-process cache:
the "本地快取" half of "本地快取/即時查詢", with the TTL as the dial between
them (0 disables caching entirely and every request queries live).

Composition, not inheritance, so it works over the HTTP adapter below, a
database-backed one, or a test fake, without any of them knowing they're
cached.

Two properties worth being explicit about:

  - **Staleness is bounded by the TTL, in one direction that matters.** A
    permission *granted* shows up within the TTL, which is fine. A permission
    *revoked* also takes up to the TTL to take effect — so the TTL is
    effectively the revocation delay, and should be chosen with that in mind
    rather than purely for hit rate.
  - **Concurrent misses are coalesced.** Without this, N simultaneous
    requests for a cold subject would all hit the backend at once, which is
    exactly what happens to a popular user right after a deploy.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from ...application.ports.outbound.permission_service_port import PermissionServicePort

__all__ = ["CachedPermissionService"]


class CachedPermissionService:
    def __init__(
        self,
        inner: PermissionServicePort,
        *,
        ttl_seconds: float = 30.0,
        max_entries: int = 10_000,
    ) -> None:
        """
        Args:
          inner: the real permission source.
          ttl_seconds: how long an answer stays usable. Doubles as the
            revocation delay — see the module docstring. 0 disables caching.
          max_entries: bound on cache size, so a service handling many
            distinct subjects can't grow the cache without limit.
        """
        self._inner = inner
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._cache: dict[tuple[str, Optional[str]], tuple[set[str], float]] = {}
        self._inflight: dict[tuple[str, Optional[str]], asyncio.Future] = {}

    async def get_permissions(
        self, subject: str, tenant: Optional[str] = None
    ) -> set[str]:
        if self._ttl <= 0:
            return await self._inner.get_permissions(subject, tenant)

        key = (subject, tenant)
        now = time.monotonic()

        cached = self._cache.get(key)
        if cached is not None and cached[1] > now:
            return cached[0]

        # Coalesce concurrent misses for the same key onto one backend call.
        inflight = self._inflight.get(key)
        if inflight is not None:
            return await asyncio.shield(inflight)

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            permissions = await self._inner.get_permissions(subject, tenant)
        except Exception as e:
            future.set_exception(e)
            # Retrieve the exception so Python doesn't warn about it never
            # being consumed if no other waiter is attached.
            future.exception()
            raise
        else:
            self._store(key, permissions, now)
            future.set_result(permissions)
            return permissions
        finally:
            self._inflight.pop(key, None)

    def _store(self, key, permissions: set[str], now: float) -> None:
        if len(self._cache) >= self._max_entries:
            # Cheapest useful eviction: drop everything already expired, and
            # if that frees nothing, drop the oldest entry. Not an LRU — the
            # access pattern here is "many subjects, each hit in a burst",
            # which TTL expiry already handles well.
            self._cache = {k: v for k, v in self._cache.items() if v[1] > now}
            if len(self._cache) >= self._max_entries:
                oldest = min(self._cache, key=lambda k: self._cache[k][1])
                self._cache.pop(oldest, None)
        self._cache[key] = (permissions, now + self._ttl)

    def invalidate(self, subject: str, tenant: Optional[str] = None) -> None:
        """Drop a cached entry, for when a revocation must take effect now
        rather than at TTL expiry."""
        self._cache.pop((subject, tenant), None)

    def clear(self) -> None:
        self._cache.clear()
