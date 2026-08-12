"""InMemoryRegistryAdapter — an in-process store of known agents, with TTL.

Implements AgentRegistryPort. Each entry is stamped with a last_seen time on
register(); list/search only return entries seen within `ttl_seconds`, so an
agent that stops heartbeating drops off automatically — the same liveness model
as registry/server.py (Consul/Eureka style), letting this back a DISCOVERY node
that is a drop-in replacement for that registry. Matching is delegated to the
pure domain service.
"""

import asyncio
import time

from ...application.ports.outbound.registry_port import AgentRegistryPort
from ...domain.models.agent_card import AgentDescriptor
from ...domain.services.discovery_service import rank_agents


class InMemoryRegistryAdapter(AgentRegistryPort):
    def __init__(
        self,
        ttl_seconds: float | None = 15.0,
        seed: list[AgentDescriptor] | None = None,
    ) -> None:
        # name -> (descriptor, last_seen_epoch)
        self._agents: dict[str, tuple[AgentDescriptor, float]] = {}
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        now = time.time()
        for descriptor in seed or []:
            self._agents[descriptor.name] = (descriptor, now)

    def _alive(self) -> list[AgentDescriptor]:
        if self._ttl is None:
            return [descriptor for descriptor, _ in self._agents.values()]
        cutoff = time.time() - self._ttl
        return [
            descriptor
            for descriptor, last_seen in self._agents.values()
            if last_seen >= cutoff
        ]

    async def register(self, descriptor: AgentDescriptor) -> None:
        async with self._lock:
            self._agents[descriptor.name] = (descriptor, time.time())

    async def list_agents(self) -> list[AgentDescriptor]:
        async with self._lock:
            return self._alive()

    async def search(self, query: str) -> list[AgentDescriptor]:
        agents = await self.list_agents()
        return rank_agents(agents, query)
