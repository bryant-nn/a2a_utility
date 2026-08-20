"""Agent-card builder models — the Pydantic wrappers a domain agent fills in.

`a2a.types.AgentCard` / `AgentSkill` / `AgentInterface` / `AgentCapabilities`
are protobuf messages and cannot be subclassed, so these WRAP them: a domain
agent fills only the meaningful values (name / description / host / port + at
least one skill) and the A2A boilerplate (version, input/output modes,
capabilities, the JSONRPC interface) is defaulted internally but stays
overridable. `.to_agent_card()` produces the genuine a2a message the SDK
expects.

Mirrors a2a_utility/server/card.py — kept in its own module (not
server_factory.py) so the card-building data models live together.
"""

from __future__ import annotations

from typing import Optional

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill
from pydantic import BaseModel, Field

__all__ = [
    "ExtendedAgentSkill",
    "ExtendedAgentCard",
    "ExtendedAgentProvider",
]


class ExtendedAgentProvider(BaseModel):
    """Who runs this agent — surfaced in directories and consoles."""

    organization: str
    url: Optional[str] = None

    def to_proto(self) -> AgentProvider:
        """Convert to a native `a2a.types.AgentProvider`.

        Returns:
            A native AgentProvider with `url` set only if given.
        """
        provider = AgentProvider(organization=self.organization)
        if self.url:
            provider.url = self.url
        return provider


class ExtendedAgentSkill(BaseModel):
    """Wraps a2a's protobuf AgentSkill.

    A domain agent fills id/name/description/examples; input/output modes and
    tags (default `[id]`) are defaulted internally.
    """

    id: str
    name: str
    description: str
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    tags: Optional[list[str]] = None  # defaults to [id]

    def to_proto(self) -> AgentSkill:
        """Convert to a native `a2a.types.AgentSkill`.

        Returns:
            A native AgentSkill; `tags` falls back to `[id]` if unset.
        """
        return AgentSkill(
            id=self.id,
            name=self.name,
            description=self.description,
            input_modes=self.input_modes,
            output_modes=self.output_modes,
            tags=self.tags if self.tags is not None else [self.id],
            examples=self.examples,
        )


class ExtendedAgentCard(BaseModel):
    """Wraps a2a's protobuf AgentCard.

    A domain agent fills name / description / host / port and at least one
    skill; version, input/output modes, capabilities, and the JSONRPC
    interface are defaulted internally (still overridable).
    """

    name: str
    description: str
    port: int
    # a2a AgentCard.skills is a repeated field — a card advertises one or more
    # skills; at least one is required.
    skills: list[ExtendedAgentSkill] = Field(min_length=1)
    host: str = "127.0.0.1"
    version: str = "0.1.0"
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    streaming: bool = True
    protocol_binding: str = "JSONRPC"
    protocol_version: str = "1.0"

    # ---- discovery metadata ----
    provider: Optional[ExtendedAgentProvider] = None
    documentation_url: Optional[str] = None
    icon_url: Optional[str] = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def agent_card_url(self) -> str:
        return f"{self.url}/.well-known/agent-card.json"

    def to_agent_card(self) -> AgentCard:
        """Convert to a native `a2a.types.AgentCard`.

        Returns:
            A native AgentCard ready to hand to `create_a2a_server()` /
            `create_agent_card_routes()` / `DefaultRequestHandler`.
        """
        card = AgentCard(
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
        if self.provider is not None:
            card.provider.CopyFrom(self.provider.to_proto())
        if self.documentation_url:
            card.documentation_url = self.documentation_url
        if self.icon_url:
            card.icon_url = self.icon_url
        return card
