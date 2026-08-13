"""a2a_utility.server — a thin typed wrapper over the native a2a-sdk.

Domain agents write ONE plain callable — an AgentHandlerPort:
`async def handle(context: ExtendedRequestContext, emit: PartEmitter) -> HandlerResult`
(a bare function for stateless agents, or an object with `__call__` for agents
that want to hold construction state) — and hand it to `serve_as_a2a(handler=...)`.
No a2a_utility class to import or subclass: `AgentExecutor` (adapters/inbound/
agent_executor.py) is a real, unmodified subclass of the native a2a-sdk ABC,
built internally around the injected handler, the same way discovery mode's
DiscoveryAgentExecutor is constructor-injected with a DiscoveryUseCasePort
rather than subclassed per agent.

`emit` streams one ExtendedPart live (thinking, a source reference, a file —
anything, not just text) — the exact same vocabulary as HandlerCompleted's
`parts` field. HandlerResult is a typed discriminated union (HandlerCompleted /
HandlerFailed / HandlerInputRequired / HandlerAuthRequired / HandlerCanceled):
the domain agent's own explicit decision of how the task ends — a2a_utility
just dispatches on it, no decision logic of its own. Externally-requested
cancellation (a client's cancel RPC) is a separate, independently optional
`on_cancel: OnCancelPort` — most agents don't need it.

Permission/identity rides on the native RequestContext.call_context via
a2a_utility's ServerCallContextBuilder, reachable opt-in via
ExtendedRequestContext.principal.

Public surface:
  - Composition:   build_agent_card, create_app, serve, serve_as_a2a (mode=AGENT|DISCOVERY)
  - Handler contract: AgentHandlerPort, OnCancelPort, ExtendedRequestContext
  - Task-ending result: HandlerResult, HandlerCompleted, HandlerFailed,
                   HandlerInputRequired, HandlerAuthRequired, HandlerCanceled, CancelResult
  - Data contract: ExtendedPart, ExtendedArtifact, ExtendedMessage, A2ATaskResult,
                   VercelThinkingResponse, SourceReferenceResponse, CustomizedData,
                   PartEmitter, as_thinking_emitter (all re-exported from a2a_utility.schema)
  - Permission:    Principal, get_principal, A2AUtilityCallContextBuilder
  - Standalone:    run_agent_server, run_discovery_server, ServerMode, A2ASettings
  - Advanced/raw:  AgentExecutor, ExtendedEventQueue (only needed to bypass the
                   AgentHandlerPort contract entirely — e.g. streaming multiple
                   artifacts over time instead of returning one final list)
  - Re-exports:    RequestContext, EventQueue (from a2a, for advanced/raw use)
"""

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue

from .adapters.inbound.agent_executor import AgentExecutor
from .adapters.inbound.call_context_builder import A2AUtilityCallContextBuilder, get_principal
from .adapters.outbound.event_queue_adapter import ExtendedEventQueue
from .app import build_agent_card, create_app, serve, serve_as_a2a
from .application.dtos import (
    CancelResult,
    ExtendedRequestContext,
    HandlerAuthRequired,
    HandlerCanceled,
    HandlerCompleted,
    HandlerFailed,
    HandlerInputRequired,
    HandlerResult,
)
from .application.ports.inbound.agent_handler_port import AgentHandlerPort
from .application.ports.inbound.on_cancel_port import OnCancelPort
from .config import A2ASettings, ServerMode
from .domain.models.principal import Principal
from ..schema import (
    A2ATaskResult,
    CustomizedData,
    ExtendedArtifact,
    ExtendedMessage,
    ExtendedPart,
    PartEmitter,
    SourceReferenceResponse,
    VercelThinkingResponse,
    as_thinking_emitter,
)
from .main import run_agent_server, run_discovery_server

__all__ = [
    # composition
    "build_agent_card",
    "create_app",
    "serve",
    "serve_as_a2a",
    # handler contract
    "AgentHandlerPort",
    "OnCancelPort",
    "ExtendedRequestContext",
    # task-ending result
    "HandlerResult",
    "HandlerCompleted",
    "HandlerFailed",
    "HandlerInputRequired",
    "HandlerAuthRequired",
    "HandlerCanceled",
    "CancelResult",
    # data contract
    "ExtendedPart",
    "ExtendedArtifact",
    "ExtendedMessage",
    "A2ATaskResult",
    "VercelThinkingResponse",
    "SourceReferenceResponse",
    "CustomizedData",
    "PartEmitter",
    "as_thinking_emitter",
    # permission
    "Principal",
    "get_principal",
    "A2AUtilityCallContextBuilder",
    # standalone nodes
    "run_agent_server",
    "run_discovery_server",
    "ServerMode",
    "A2ASettings",
    # advanced/raw
    "AgentExecutor",
    "ExtendedEventQueue",
    # native re-exports
    "RequestContext",
    "EventQueue",
]
