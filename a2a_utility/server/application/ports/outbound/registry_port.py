"""Outbound port: storage/lookup for known agents (used by discovery)."""

from typing import Protocol, runtime_checkable

from ....domain.models.agent_card import AgentDescriptor


@runtime_checkable
class AgentRegistryPort(Protocol):
    async def register(self, descriptor: AgentDescriptor) -> None:
        """Add or replace an agent entry (keyed by name)."""
        ...

    async def list_agents(self) -> list[AgentDescriptor]:
        """Return every registered agent."""
        ...

    async def search(self, query: str) -> list[AgentDescriptor]:
        """Return agents matching `query`, best match first."""
        ...
