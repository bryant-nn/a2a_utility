"""Agent-card builder models — the Pydantic wrappers a domain agent fills in.

`a2a.types.AgentCard` / `AgentSkill` / `AgentInterface` / `AgentCapabilities`
are protobuf messages and cannot be subclassed, so these WRAP them: a domain
agent fills only the meaningful values (name / description / host / port + an
ExtendAgentSkill) and the a2a boilerplate (version, input/output modes,
capabilities, the JSONRPC interface) is defaulted internally but stays
overridable. `.to_proto()` / `.to_agent_card()` produce the genuine a2a
messages the SDK expects.

Kept in its own module (not app.py) so the card-building data models live
together and app.py stays pure composition/serving.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

__all__ = ["ExtendAgentSkill", "ExtendAgentCard", "build_agent_card"]


class ExtendAgentSkill(BaseModel):
    """Wraps a2a's protobuf AgentSkill.

    A domain agent fills id/name/description/examples; input/output modes and
    tags (default `[id]`) are defaulted internally. `to_proto()` builds the real
    a2a AgentSkill.
    """

    id: str
    name: str
    description: str
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    tags: Optional[list[str]] = None  # defaults to [id]

    def to_proto(self) -> AgentSkill:
        return AgentSkill(
            id=self.id,
            name=self.name,
            description=self.description,
            input_modes=self.input_modes,
            output_modes=self.output_modes,
            tags=self.tags if self.tags is not None else [self.id],
            examples=self.examples,
        )


class ExtendAgentCard(BaseModel):
    """Wraps a2a's protobuf AgentCard.

    A domain agent fills only name / description / host / port and one or more
    ExtendAgentSkill; version, input/output modes, capabilities, and the JSONRPC
    AgentInterface are defaulted internally (still overridable).
    `to_agent_card()` builds the real a2a AgentCard using the genuine
    AgentCard / AgentSkill / AgentInterface / AgentCapabilities messages.
    """

    name: str
    description: str
    port: int
    # a2a AgentCard.skills is a repeated field (list[AgentSkill]) — a card
    # advertises one or more skills; at least one is required.
    skills: list[ExtendAgentSkill] = Field(min_length=1)
    host: str
    version: str = "0.1.0"
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    streaming: bool = True
    protocol_binding: str = "JSONRPC"
    protocol_version: str = "1.0"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def agent_card_url(self) -> str:
        return f"{self.url}/.well-known/agent-card.json"

    def to_agent_card(self) -> AgentCard:
        return AgentCard(
            name=self.name,
            description=self.description,
            version=self.version,
            default_input_modes=self.default_input_modes,
            default_output_modes=self.default_output_modes,
            capabilities=AgentCapabilities(streaming=self.streaming),
            supported_interfaces=[
                AgentInterface(
                    protocol_binding=self.protocol_binding,
                    url=self.url,
                    protocol_version=self.protocol_version,
                )
            ],
            skills=[s.to_proto() for s in self.skills],
        )


def build_agent_card(
    *,
    name: str,
    description: str,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    examples: list[str],
    host: str,
    port: int,
) -> AgentCard:
    """Backward-compatible flat helper — delegates to ExtendAgentCard so there is
    a single card-building source of truth."""
    return ExtendAgentCard(
        name=name,
        description=description,
        host=host,
        port=port,
        skills=[
            ExtendAgentSkill(
                id=skill_id, name=skill_name, description=skill_description, examples=examples
            )
        ],
    ).to_agent_card()
