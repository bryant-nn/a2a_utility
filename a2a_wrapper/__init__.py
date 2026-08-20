"""a2a_wrapper — a domain-agent-executor-only anti-corruption layer over the
native a2a-sdk, in progress toward the same completeness as a2a_utility.

Currently in scope: writing and serving a domain agent (`DomainAgentExecutorPort`
+ `create_a2a_server`). Not yet in scope: a discovery node — see a2a_utility's
server for that, this package doesn't have an equivalent yet.
"""

from .events import (
    ArtifactResult,
    AuthRequired,
    InputRequired,
    Rejected,
    StatusMessage,
    StreamEvent,
    TextChunk,
)
from .server.card import ExtendedAgentCard, ExtendedAgentProvider, ExtendedAgentSkill
from .server.ports import DomainAgentExecutorPort
from .server.server_factory import create_a2a_server
from .types import CustomizedData, DataType, DomainContext, ExtendedPart

__all__ = [
    "StreamEvent",
    "TextChunk",
    "StatusMessage",
    "ArtifactResult",
    "InputRequired",
    "AuthRequired",
    "Rejected",
    "DomainContext",
    "ExtendedPart",
    "CustomizedData",
    "DataType",
    "DomainAgentExecutorPort",
    "ExtendedAgentCard",
    "ExtendedAgentProvider",
    "ExtendedAgentSkill",
    "create_a2a_server",
]
