"""a2a_utility.server — a thin typed wrapper over the native a2a-sdk.

Domain agents write their OWN AgentExecutor.execute(context, event_queue) and
emit results through TypedTaskUpdater (so everything on the EventQueue conforms
to the ExtendedPart data contract). Permission/identity rides on the native
RequestContext.call_context via a2a_utility's ServerCallContextBuilder.

Public surface:
  - Composition:   build_agent_card, create_app, serve, serve_as_a2a
  - Emit toolkit:  TypedTaskUpdater
  - Data contract: ExtendedPart, ExtendedArtifact, ExtendedMessage, A2ATaskResult,
                   ThinkingResponse, SourceReferenceResponse, CustomizedData
  - Permission:    Principal, get_principal, A2AUtilityCallContextBuilder
  - Standalone:    run_agent_server, run_discovery_server, ServerMode, A2ASettings
  - Re-exports:    AgentExecutor, RequestContext, EventQueue (from a2a, for convenience)
"""

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .app import build_agent_card, create_app, serve, serve_as_a2a
from .config import A2ASettings, ServerMode
from .context import A2AUtilityCallContextBuilder, Principal, get_principal
from ..types import (
    A2ATaskResult,
    CustomizedData,
    ExtendedArtifact,
    ExtendedMessage,
    ExtendedPart,
    SourceReferenceResponse,
    ThinkingResponse,
)
from .main import run_agent_server, run_discovery_server
from .updater import TypedTaskUpdater

__all__ = [
    # composition
    "build_agent_card",
    "create_app",
    "serve",
    "serve_as_a2a",
    # emit toolkit
    "TypedTaskUpdater",
    # data contract
    "ExtendedPart",
    "ExtendedArtifact",
    "ExtendedMessage",
    "A2ATaskResult",
    "ThinkingResponse",
    "SourceReferenceResponse",
    "CustomizedData",
    # permission
    "Principal",
    "get_principal",
    "A2AUtilityCallContextBuilder",
    # standalone nodes
    "run_agent_server",
    "run_discovery_server",
    "ServerMode",
    "A2ASettings",
    # native re-exports
    "AgentExecutor",
    "RequestContext",
    "EventQueue",
]
