"""Discovery-side domain object: a minimal descriptor of a reachable agent.

This is intentionally NOT a2a.types.AgentCard — the registry stores only what
discovery needs (name, what it does, where its card lives). The full A2A
AgentCard is an adapter/transport detail fetched from agent_card_url on demand.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    description: str
    agent_card_url: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "agent_card_url": self.agent_card_url,
        }
