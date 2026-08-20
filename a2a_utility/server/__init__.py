"""a2a_utility.server — a thin typed wrapper over the native a2a-sdk.

Domain agents write a class subclassing `DomainAgentExecutorPort`:

    class MyAgent(DomainAgentExecutorPort):
        async def execute(self, context: ExtendedRequestContext):
            yield Progress("thinking...")
            answer = await run_my_agent(context.get_user_input())
            yield PublishArtifact(parts=[ExtendedPart.from_text(answer)])

and hand an instance to `serve_as_a2a(executor=MyAgent())`. `execute()` is an
async generator: yield `TaskEvent` values to drive the task, `return` to
complete it, `raise` to fail it (the exception's message is preserved on the
wire). Override `cancel()` only to attach a custom message to an externally
requested CANCELED — real cleanup doesn't need it, since the asyncio task
running `execute()` is cancelled by the framework independently and a
handler's own try/finally already runs.

This is a deliberate departure from this package's other ports
(`DiscoveryUseCasePort` is a `Protocol`, no inheritance required) — a real
ABC, subclassed. An earlier shape (`AgentHandlerPort`, a plain callable
imperatively driving `ExtendedTaskUpdater`/`ExtendedEventQueue`) held to "no
forced inheritance"; this replaces it entirely, because the declarative shape
needs somewhere to hang a default `cancel()`. `ExtendedTaskUpdater` and
`ExtendedEventQueue` are gone along with it — nothing else called them once
`agent_executor.py` stopped, so keeping them around would have been orphaned
code. `adapters/inbound/agent_executor.py` (the one class that reads what a
domain agent yields) drives native `TaskUpdater`/`EventQueue` directly; see
`docs/DESIGN.md` for the full reasoning, including why that's safe (the
Task-before-status-event ordering, the cancel-before-any-Task edge case, and
`MessageLike` coercion all moved into `adapters/inbound/_native_task.py`,
shared with DISCOVERY mode's `DiscoveryAgentExecutor`, instead of being
reimplemented twice).

`AgentExecutor` (this module's own inbound adapter) does NOT auto-send the
initial WORKING status or check whether the domain generator ever reached a
terminal state — native doesn't do either, so a domain agent is held to
exactly the same expectations native usage would be. The one thing it adds
beyond mapping typed events onto native calls: uncaught exceptions become a
FAILED status *carrying the error text*. Under a2a-sdk 1.1.2 the framework
does mark the task FAILED by itself (`ActiveTask._run_producer` and
`EventConsumer.run` both catch), but it sends a bare status with no message;
this layer exists so the caller learns *what* failed.

`MessageReply` is the one `TaskEvent` that skips Task creation entirely —
native's other "immediate response" workflow, answering without ever
creating a `Task` at all. It must be the only event yielded: the Task is
built lazily, on the first event that actually needs one, which is what
makes this possible.

Public surface:
  - Composition:   ExtendedAgentCard, ExtendedAgentSkill, ExtendedAgentProvider,
                   create_app, serve, serve_as_a2a (mode=AGENT|DISCOVERY)
  - Executor contract: DomainAgentExecutorPort, ExtendedRequestContext
  - Typed events (what execute() yields): TaskEvent, Progress, PublishArtifact,
                   InputRequired, AuthRequired, Rejected, MessageReply
  - Data contract: ExtendedPart, ExtendedArtifact, ExtendedMessage, MessageRole,
                   ExtendedTask, ExtendedTaskState, A2ATaskResult, MessageLike,
                   VercelThinkingResponse, SourceReferenceResponse, CustomizedData,
                   PartEmitter, as_thinking_emitter (re-exported from a2a_utility.schema)
  - Standalone:    run_agent_server, run_discovery_server, ServerMode, A2ASettings
  - Native re-export: IDGenerator — the one type a caller needs to *name* in
                   order to pass `message_id_generator=` to `AgentExecutor`.
                   Nothing else native is re-exported: `RequestContext`/
                   `EventQueue` used to be, which quietly told handlers the
                   protobuf types were fair game.
"""

from a2a.server.id_generator import IDGenerator

from .adapters.inbound.agent_executor import AgentExecutor
from .app import create_app, serve, serve_as_a2a
from .card import ExtendedAgentCard, ExtendedAgentProvider, ExtendedAgentSkill
from .application.dtos import ExtendedRequestContext
from .application.ports.inbound.domain_agent_executor_port import DomainAgentExecutorPort
from .domain.models.task_events import (
    AuthRequired,
    InputRequired,
    MessageReply,
    Progress,
    PublishArtifact,
    Rejected,
    TaskEvent,
)
from .config import A2ASettings, ServerMode
from ..schema import (
    A2ATaskResult,
    CustomizedData,
    ExtendedArtifact,
    ExtendedMessage,
    ExtendedPart,
    ExtendedTask,
    ExtendedTaskState,
    MessageLike,
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
    # executor contract
    "DomainAgentExecutorPort",
    "ExtendedRequestContext",
    # typed events (what execute() yields)
    "TaskEvent",
    "Progress",
    "PublishArtifact",
    "InputRequired",
    "AuthRequired",
    "Rejected",
    "MessageReply",
    # data contract
    "ExtendedPart",
    "ExtendedArtifact",
    "ExtendedMessage",
    "MessageLike",
    "MessageRole",
    "ExtendedTask",
    "ExtendedTaskState",
    "A2ATaskResult",
    "VercelThinkingResponse",
    "SourceReferenceResponse",
    "CustomizedData",
    "PartEmitter",
    "as_thinking_emitter",
    # standalone nodes
    "run_agent_server",
    "run_discovery_server",
    "ServerMode",
    "A2ASettings",
    # advanced: composing your own Starlette app instead of create_app() —
    # the path for a durable task store, push notifications, or REST routes
    "AgentExecutor",
    # native re-export, for typing a custom message_id_generator=
    "IDGenerator",
]
