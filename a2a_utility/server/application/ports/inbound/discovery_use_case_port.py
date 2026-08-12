"""Inbound port: what an A2A inbound adapter is allowed to ask of discovery."""

from typing import Protocol, runtime_checkable

from ....domain.models.agent_card import AgentDescriptor


@runtime_checkable
class DiscoveryUseCasePort(Protocol):
    async def list_all(self) -> list[AgentDescriptor]:
        """Return every currently known agent."""
        ...

    async def search(self, query: str) -> list[AgentDescriptor]:
        """Return agents matching `query`, best match first."""
        ...
