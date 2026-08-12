"""RegisterAgentCardUseCase: a domain agent announces (or refreshes) its card.

Called on every heartbeat. Storing an existing name again just refreshes its
liveness stamp inside the registry adapter.
"""

from ...domain.models.agent_card import AgentDescriptor
from ..ports.outbound.registry_port import AgentRegistryPort


class RegisterAgentCardUseCase:
    def __init__(self, registry: AgentRegistryPort) -> None:
        self._registry = registry

    async def register(self, descriptor: AgentDescriptor) -> None:
        await self._registry.register(descriptor)
