"""a2a_utility.server — a thin typed wrapper over the native a2a-sdk.

Domain agents write ONE plain callable — an AgentHandlerPort:
`async def handle(context: ExtendedRequestContext, event_queue: ExtendedEventQueue) -> None`
(a bare function for stateless agents, or an object with `__call__` for agents
that want to hold construction state) — and hand it to `serve_as_a2a(handler=...)`.
No a2a_utility class to import or subclass: `AgentExecutor` (adapters/inbound/
agent_executor.py) is a real, unmodified subclass of the native a2a-sdk ABC,
built internally around the injected handler, the same way discovery mode's
DiscoveryAgentExecutor is constructor-injected with a DiscoveryUseCasePort
rather than subclassed per agent.

This mirrors writing a native `AgentExecutor.execute(context, event_queue) ->
None` directly, parameter for parameter: native hands the implementer the raw
`EventQueue`, not a pre-built `TaskUpdater` — building one is the
implementer's own choice, not something the framework decides. A domain
agent that wants `TaskUpdater`-level convenience builds
`ExtendedTaskUpdater(context, event_queue)` itself, as its own first line,
exactly like a native implementation would build a `TaskUpdater` itself:

    async def handle(context, event_queue):
        task_updater = ExtendedTaskUpdater(context, event_queue)
        await task_updater.start_work()
        answer = await run_my_agent(context.get_user_input())
        await task_updater.add_artifact([ExtendedPart.from_text(answer)])
        await task_updater.complete()

`ExtendedTaskUpdater` is a real subclass of native `TaskUpdater` (not a
wrapper) — `isinstance(tu, TaskUpdater)` is True, and the id generation,
terminal-state guard, and event construction underneath are native's own.
Every public method is nonetheless an explicit override, for the typed
boundary this package holds: native's signatures speak protobuf
(`update_status` takes an int-valued `TaskState`, every `message=` takes an
`a2a.types.Message`, `add_artifact` takes `list[Part]`), so inheriting any of
them unchanged would put a native type back in a handler's face.
`ExtendedTaskUpdater`'s versions take `ExtendedTaskState`/`list[ExtendedPart]`
instead, and every `message=` accepts a plain `str`, an `ExtendedPart`, a
`list[ExtendedPart]`, or an `ExtendedMessage` — no message-construction
ritual to attach a note to a status. `add_artifact`/`new_agent_message` also
catch a real native footgun (the `Part` proto's oneof silently keeps
whichever of text/raw/url/data was set last instead of raising). There is no
declarative return-value contract to learn (no HandlerResult) — the domain
agent decides how the task ends by which method it calls, exactly like
native usage.

`AgentExecutor.execute()` does NOT auto-send the initial WORKING status or
check whether the handler ever reached a terminal state — native doesn't do
either, so this class holds handlers to exactly the same expectations native
does. The one thing it adds beyond pure pass-through: uncaught exceptions
become a FAILED status *carrying the error text* (via a throwaway
`ExtendedTaskUpdater` built in the except block). Under a2a-sdk 1.1.2 the
framework does mark the task FAILED by itself (`ActiveTask._run_producer`
and `EventConsumer.run` both catch), but it sends a bare status with no
message; this layer exists so the caller learns *what* failed.

`ExtendedEventQueue` exposes two methods. `enqueue_message(ExtendedMessage)`
is the one a handler should reach for: it publishes a standalone reply —
native's other "immediate response" workflow, answering without ever
creating a `Task` at all — and takes and needs nothing from `a2a.*`.
`enqueue_event(Event)` is the advanced escape hatch underneath it, mirroring
the native `EventQueue` ABC directly (whose docstring is explicit that it is
the producer-side interface and that the consumer half — `dequeue_event`/
`tap`/`close` — is framework-managed and not public); using it directly means
holding a native `a2a.types` object yourself, which `ExtendedTaskUpdater`
already does correctly for `Task`/`TaskStatusUpdateEvent`/
`TaskArtifactUpdateEvent`. Both validate eagerly that a published event's
`task_id` matches this request's — native rejects the mismatch deep in
`a2a.server.tasks.task_manager.TaskManager`, outside any exception handler
reachable from a handler, so a catchable `ValueError` is raised first here
instead. Events carrying no task id at all (the standalone message case) are
exempt.

Neither `ExtendedEventQueue` nor `ExtendedTaskUpdater` exposes the
underlying native object via a `.native`-style escape hatch — a domain
agent's code never imports anything from `a2a.*`.

Permission/identity rides on the native RequestContext.call_context via a
`ServerCallContextBuilder`, reachable opt-in via
ExtendedRequestContext.principal. a2a_utility's own
`A2AUtilityCallContextBuilder` is a stub (reads plain headers, doesn't
validate); pass a subclass via `context_builder=` on `create_app()`/
`serve_as_a2a()` to plug in real JWT/session validation without forking this
package.

Public surface:
  - Composition:   ExtendedAgentCard, ExtendedAgentSkill, ExtendedAgentProvider,
                   create_app, serve, serve_as_a2a (mode=AGENT|DISCOVERY)
  - Handler contract: AgentHandlerPort, OnCancelPort, ExtendedRequestContext
  - Task-driving objects: ExtendedEventQueue, ExtendedTaskUpdater, MessageLike
  - Data contract: ExtendedPart, ExtendedArtifact, ExtendedMessage, MessageRole,
                   ExtendedTask, ExtendedTaskState, A2ATaskResult,
                   VercelThinkingResponse, SourceReferenceResponse, CustomizedData,
                   PartEmitter, as_thinking_emitter (re-exported from a2a_utility.schema)
  - Auth:          BearerAuth, ApiKeyAuth, Principal, get_principal,
                   A2AUtilityCallContextBuilder
  - Standalone:    run_agent_server, run_discovery_server, ServerMode, A2ASettings
  - Native re-exports: IDGenerator, ServerCallContextBuilder — the two types a
                   caller needs to *name* in order to pass `message_id_generator=`
                   or subclass `context_builder=`. Nothing else native is
                   re-exported: `RequestContext`/`EventQueue` used to be, which
                   quietly told handlers the protobuf types were fair game.
"""

from a2a.server.id_generator import IDGenerator
from a2a.server.routes.common import ServerCallContextBuilder

from .adapters.inbound.agent_executor import AgentExecutor
from .adapters.inbound.call_context_builder import A2AUtilityCallContextBuilder, get_user_context
from .adapters.inbound.gate_middleware import GateMiddleware
from .adapters.outbound.cached_permission_service import CachedPermissionService
from .adapters.outbound.http_permission_service import (
    HttpPermissionService,
    PermissionServiceUnavailable,
)
from .adapters.outbound.event_queue_adapter import ExtendedEventQueue
from .adapters.outbound.task_updater_adapter import ExtendedTaskUpdater, MessageLike
from .app import create_app, serve, serve_as_a2a
from .card import (
    ApiKeyAuth,
    AuthScheme,
    BearerAuth,
    ExtendedAgentCard,
    ExtendedAgentProvider,
    ExtendedAgentSkill,
)
from .application.dtos import ExtendedRequestContext
from .application.ports.inbound.agent_handler_port import AgentHandlerPort
from .application.ports.inbound.gate_keeper_port import GateKeeperPort
from .application.ports.inbound.on_cancel_port import OnCancelPort
from .application.ports.outbound.permission_service_port import PermissionServicePort
from .application.ports.outbound.token_verifier_port import InvalidToken, TokenVerifierPort
from .application.services.gate_keeper import (
    AllowAllGateKeeper,
    ClaimNames,
    GateKeeper,
)
from .config import A2ASettings, ServerMode
from .fastapi import add_to_fastapi
from .domain.models.auth_decision import Allow, AuthDecision, AuthRequired, Reject
from .domain.models.user_context import PermissionDenied, UserContext
from ..schema import (
    A2ATaskResult,
    CustomizedData,
    ExtendedArtifact,
    ExtendedMessage,
    ExtendedPart,
    ExtendedTask,
    ExtendedTaskState,
    MessageRole,
    PartEmitter,
    SourceReferenceResponse,
    VercelThinkingResponse,
    as_thinking_emitter,
)
from .main import run_agent_server, run_discovery_server

__all__ = [
    # composition
    "ExtendedAgentSkill",
    "ExtendedAgentCard",
    "ExtendedAgentProvider",
    "create_app",
    "serve",
    "serve_as_a2a",
    "add_to_fastapi",
    # handler contract
    "AgentHandlerPort",
    "OnCancelPort",
    "ExtendedRequestContext",
    # task-driving objects
    "ExtendedEventQueue",
    "ExtendedTaskUpdater",
    "MessageLike",
    # data contract
    "ExtendedPart",
    "ExtendedArtifact",
    "ExtendedMessage",
    "MessageRole",
    "ExtendedTask",
    "ExtendedTaskState",
    "A2ATaskResult",
    "VercelThinkingResponse",
    "SourceReferenceResponse",
    "CustomizedData",
    "PartEmitter",
    "as_thinking_emitter",
    # auth — card declaration
    "BearerAuth",
    "ApiKeyAuth",
    "AuthScheme",
    # auth — the gate
    "GateKeeper",
    "GateKeeperPort",
    "AllowAllGateKeeper",
    "ClaimNames",
    "GateMiddleware",
    "Allow",
    "AuthRequired",
    "Reject",
    "AuthDecision",
    # auth — identity a handler reads
    "UserContext",
    "PermissionDenied",
    "get_user_context",
    # auth — pluggable backends
    "TokenVerifierPort",
    "InvalidToken",
    "PermissionServicePort",
    "CachedPermissionService",
    "HttpPermissionService",
    "PermissionServiceUnavailable",
    "A2AUtilityCallContextBuilder",
    # standalone nodes
    "run_agent_server",
    "run_discovery_server",
    "ServerMode",
    "A2ASettings",
    # advanced: composing your own Starlette app instead of create_app()
    "AgentExecutor",
    # native re-exports, for typing a custom context_builder=/message_id_generator=
    "IDGenerator",
    "ServerCallContextBuilder",
]
