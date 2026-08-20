"""AgentExecutor — the inbound adapter bridging native a2a execute()/cancel()
calls to an injected DomainAgentExecutorPort.

This *is* a real subclass of the native `a2a.server.agent_execution.
AgentExecutor` ABC (not a reimplementation) — it genuinely satisfies the SDK's
own contract, so `DefaultRequestHandler`/`create_jsonrpc_routes` work with it
unmodified. Domain agents never see this class at all: `serve_as_a2a(executor=
..., ...)`/`create_app(executor=..., ...)` build one internally from whatever
`DomainAgentExecutorPort` the domain agent subclassed.

Drives a native `TaskUpdater`/`EventQueue` directly — no `ExtendedTaskUpdater`/
`ExtendedEventQueue` in the picture; those were deleted once this became the
only caller. `_native_task.py` holds the two pieces of Task-lifecycle
correctness this and `discovery_agent_executor.py` both need (building the
initial Task, coercing a `MessageLike` into a native `Message`), so neither
adapter reimplements them independently.

`execute()` drains the domain's `TaskEvent` generator and maps each yielded
value onto native `TaskUpdater`/`EventQueue` calls:

  Progress          -> update_status(WORKING, message=...)
  PublishArtifact    -> add_artifact(...)
  InputRequired      -> requires_input(...), then stop reading the generator
  AuthRequired       -> requires_auth(...), then stop
  Rejected           -> reject(...), then stop
  MessageReply       -> enqueue_event(a native Message) directly — no Task is
                       ever built or sent for this request
  (generator exhausts normally) -> complete()
  (generator raises) -> failed(f"Agent error: {e}") — the real exception text
                       is preserved on the wire, the same policy the old
                       ExtendedTaskUpdater-based version held

The Task itself is enqueued lazily, on the first event that actually needs
one — this is what makes MessageReply's "never create a Task" promise
possible: nothing is sent until we know which kind of reply this is.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Optional

from a2a.server.agent_execution import AgentExecutor as _NativeAgentExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.server.id_generator import IDGenerator, UUIDGenerator
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from ...application.dtos import ExtendedRequestContext
from ...application.ports.inbound.domain_agent_executor_port import DomainAgentExecutorPort
from ...domain.models.task_events import (
    AuthRequired,
    InputRequired,
    MessageReply,
    Progress,
    PublishArtifact,
    Rejected,
)
from ._native_task import coerce_message, initial_task


class AgentExecutor(_NativeAgentExecutor):
    def __init__(
        self,
        executor: DomainAgentExecutorPort,
        *,
        message_id_generator: Optional[IDGenerator] = None,
    ) -> None:
        self._executor = executor
        self._message_id_generator = message_id_generator or UUIDGenerator()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        ctx = ExtendedRequestContext(context)
        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        task = initial_task(context, task_id, context_id)
        updater = TaskUpdater(
            event_queue, task.id, task.context_id, message_id_generator=self._message_id_generator
        )
        task_started = False

        async def ensure_task() -> None:
            nonlocal task_started
            # Set before await, not after: two concurrent events would
            # otherwise both see False and enqueue the Task twice.
            if not task_started:
                task_started = True
                await event_queue.enqueue_event(task)

        def coerce(message):
            return coerce_message(
                message,
                task_id=task.id,
                context_id=task.context_id,
                message_id_generator=self._message_id_generator,
            )

        try:
            async for event in self._executor.execute(ctx):
                if isinstance(event, Progress):
                    await ensure_task()
                    await updater.update_status(TaskState.TASK_STATE_WORKING, message=coerce(event.message))
                elif isinstance(event, PublishArtifact):
                    await ensure_task()
                    await updater.add_artifact(
                        [p.to_protobuf() for p in event.parts],
                        artifact_id=event.artifact_id,
                        name=event.name,
                        metadata=event.metadata,
                        append=event.append,
                        last_chunk=event.last_chunk,
                        extensions=event.extensions,
                    )
                elif isinstance(event, InputRequired):
                    await ensure_task()
                    await updater.requires_input(message=coerce(event.message))
                    return
                elif isinstance(event, AuthRequired):
                    await ensure_task()
                    await updater.requires_auth(message=coerce(event.message))
                    return
                elif isinstance(event, Rejected):
                    await ensure_task()
                    await updater.reject(message=coerce(event.message))
                    return
                elif isinstance(event, MessageReply):
                    # message-mode: task_started stays False, no Task is ever sent.
                    await event_queue.enqueue_event(coerce(event.message))
                    return
                else:
                    raise TypeError(f"unknown TaskEvent: {type(event)!r}")
            await ensure_task()
            await updater.complete()
        except Exception as e:
            with contextlib.suppress(RuntimeError):  # already-terminal guard
                await ensure_task()
                await updater.failed(message=coerce(f"Agent error: {e}"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Reacts to an externally-requested cancellation (a client's cancel
        RPC). Structurally separate from execute()'s own path — the
        framework calls this independently, in addition to cancelling the
        asyncio task running execute() (which lets a handler's own
        try/finally clean up; no new plumbing needed for that half)."""
        ctx = ExtendedRequestContext(context)
        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        task = initial_task(context, task_id, context_id)
        updater = TaskUpdater(
            event_queue, task.id, task.context_id, message_id_generator=self._message_id_generator
        )
        custom_message = None
        with contextlib.suppress(Exception):  # domain cancel() failing must not block CANCELED
            custom_message = await self._executor.cancel(ctx)
        with contextlib.suppress(RuntimeError):
            # execute()'s own updater may have already sent this exact Task;
            # a duplicate is dropped with a logged error by the framework,
            # not raised — the same behavior the old ExtendedTaskUpdater-
            # based cancel() path already relied on.
            await event_queue.enqueue_event(task)
            await updater.cancel(
                message=coerce_message(
                    custom_message,
                    task_id=task.id,
                    context_id=task.context_id,
                    message_id_generator=self._message_id_generator,
                )
            )
