"""SearchAgentUseCase: answer "who's alive / who can do X?" for the coordinator.

Implements the inbound DiscoveryUseCasePort. `list_all` backs the coordinator's
GET /agents (list every currently-alive agent); `search` backs the plan's
/search discovery query (rank agents against a free-text task description).
"""

from ...domain.models.agent_card import AgentDescriptor
from ..ports.inbound.discovery_use_case_port import DiscoveryUseCasePort
from ..ports.outbound.registry_port import AgentRegistryPort


class SearchAgentUseCase(DiscoveryUseCasePort):
    def __init__(self, registry: AgentRegistryPort) -> None:
        self._registry = registry

    async def list_all(self) -> list[AgentDescriptor]:
        return await self._registry.list_agents()

    async def search(self, query: str) -> list[AgentDescriptor]:
        return await self._registry.search(query)
