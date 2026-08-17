"""CachedPermissionService — the cache that keeps the gate off the network
on every request."""

from __future__ import annotations

import asyncio

import pytest

from a2a_utility.server import CachedPermissionService


class CountingService:
    def __init__(self, permissions: set[str] | None = None, delay: float = 0.0) -> None:
        self.permissions = permissions if permissions is not None else {"agent:a"}
        self.delay = delay
        self.calls: list[tuple[str, str | None]] = []

    async def get_permissions(self, subject: str, tenant: str | None = None) -> set[str]:
        self.calls.append((subject, tenant))
        if self.delay:
            await asyncio.sleep(self.delay)
        return set(self.permissions)


async def test_a_repeat_lookup_is_served_from_cache():
    inner = CountingService()
    cache = CachedPermissionService(inner, ttl_seconds=60)

    assert await cache.get_permissions("alice") == {"agent:a"}
    assert await cache.get_permissions("alice") == {"agent:a"}
    assert len(inner.calls) == 1


async def test_different_subjects_are_cached_separately():
    inner = CountingService()
    cache = CachedPermissionService(inner, ttl_seconds=60)

    await cache.get_permissions("alice")
    await cache.get_permissions("bob")
    assert len(inner.calls) == 2


async def test_the_same_subject_in_different_tenants_is_cached_separately():
    inner = CountingService()
    cache = CachedPermissionService(inner, ttl_seconds=60)

    await cache.get_permissions("alice", "acme")
    await cache.get_permissions("alice", "other")
    assert inner.calls == [("alice", "acme"), ("alice", "other")]


async def test_an_expired_entry_is_refetched():
    inner = CountingService()
    cache = CachedPermissionService(inner, ttl_seconds=0.01)

    await cache.get_permissions("alice")
    await asyncio.sleep(0.02)
    await cache.get_permissions("alice")
    assert len(inner.calls) == 2


async def test_a_zero_ttl_disables_caching_entirely():
    """The "即時查詢" end of the dial."""
    inner = CountingService()
    cache = CachedPermissionService(inner, ttl_seconds=0)

    await cache.get_permissions("alice")
    await cache.get_permissions("alice")
    assert len(inner.calls) == 2


async def test_concurrent_misses_for_one_subject_make_a_single_backend_call():
    """Without coalescing, every in-flight request for a cold subject hits the
    backend at once — which is what happens to a busy user after a deploy."""
    inner = CountingService(delay=0.02)
    cache = CachedPermissionService(inner, ttl_seconds=60)

    results = await asyncio.gather(*(cache.get_permissions("alice") for _ in range(10)))

    assert len(inner.calls) == 1
    assert all(r == {"agent:a"} for r in results)


async def test_a_backend_failure_propagates_to_every_waiter_and_is_not_cached():
    class Failing:
        def __init__(self) -> None:
            self.calls = 0

        async def get_permissions(self, subject, tenant=None):
            self.calls += 1
            await asyncio.sleep(0.01)
            raise RuntimeError("permission service unreachable")

    inner = Failing()
    cache = CachedPermissionService(inner, ttl_seconds=60)

    results = await asyncio.gather(
        *(cache.get_permissions("alice") for _ in range(3)), return_exceptions=True
    )
    assert all(isinstance(r, RuntimeError) for r in results)

    # A failure must not be remembered as an answer.
    with pytest.raises(RuntimeError):
        await cache.get_permissions("alice")
    assert inner.calls == 2  # one for the coalesced burst, one for the retry


async def test_invalidate_forces_the_next_lookup_to_refetch():
    """For when a revocation has to take effect now rather than at TTL
    expiry — the cache TTL is otherwise the revocation delay."""
    inner = CountingService()
    cache = CachedPermissionService(inner, ttl_seconds=60)

    await cache.get_permissions("alice")
    cache.invalidate("alice")
    await cache.get_permissions("alice")
    assert len(inner.calls) == 2


async def test_the_cache_is_bounded():
    inner = CountingService()
    cache = CachedPermissionService(inner, ttl_seconds=60, max_entries=5)

    for i in range(20):
        await cache.get_permissions(f"user-{i}")

    assert len(cache._cache) <= 5


async def test_clear_empties_the_cache():
    inner = CountingService()
    cache = CachedPermissionService(inner, ttl_seconds=60)

    await cache.get_permissions("alice")
    cache.clear()
    await cache.get_permissions("alice")
    assert len(inner.calls) == 2
