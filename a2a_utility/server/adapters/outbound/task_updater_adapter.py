"""ExtendedTaskUpdater — the object a handler drives a task with.

A real subclass of native `TaskUpdater`, not a wrapper: `isinstance(tu,
TaskUpdater)` is True, and the id generation, terminal-state guard, and event
construction underneath are native's own.

Not constructed by `AgentExecutor` — `AgentHandlerPort`/`OnCancelPort` hand
the handler an `ExtendedEventQueue` (mirroring native `AgentExecutor.execute
(context, event_queue)` exactly), and a handler that wants TaskUpdater-level
convenience builds one itself as its first line, the way a native
implementation would build a `TaskUpdater` from the queue it was handed.

Every public method is overridden, for one reason: **the typed boundary**.
Native's signatures speak protobuf — `update_status` takes an int-valued
`TaskState`, every `message=` takes an `a2a.types.Message`, `add_artifact`
takes `list[Part]` — so inheriting any of them unchanged would put a
protobuf type back in a handler's face and undo the boundary this package
exists to hold. An earlier version inherited the eight status shorthands
unmodified, which read elegantly but meant `complete(message=...)` silently
required a hand-built native `Message`.

What each override buys beyond the type swap:

  - `update_status()`: injects `_ensure_task()`. The protocol requires a
    `Task` event before any `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent`
    — `EventConsumer._handle_task_modification_event` raises
    `InvalidAgentResponseError` otherwise — so the first call from any method
    enqueues it lazily. Plumbing, not a control-flow decision, so a handler
    never has to think about it.
  - the eight status shorthands: accept `str` / `list[ExtendedPart]` /
    `ExtendedMessage` for `message=`, so attaching a note to a status is one
    argument rather than a message-construction ritual.
  - `add_artifact()` / `new_agent_message()`: translate `list[ExtendedPart]`
    to `list[Part]`, catching the native `Part` oneof footgun (protobuf lets
    you set more than one of text/raw/url/data without raising — it silently
    keeps whichever was assigned last).

Task state is entirely the handler's decision: which method it calls is how
the task ends. Nothing here auto-completes, auto-starts, or second-guesses.
"""

from __future__ import annotations

import uuid
from typing import Optional, Union

from a2a.helpers import new_task_from_user_message
from a2a.server.id_generator import IDGenerator, IDGeneratorContext
from a2a.server.tasks import TaskUpdater
from a2a.types import Message as _ProtoMessage
from a2a.types import Task, TaskState, TaskStatus

from ....schema import ExtendedMessage, ExtendedPart, ExtendedTaskState, MessageRole, PartEmitter
from ...application.dtos import ExtendedRequestContext
from .event_queue_adapter import ExtendedEventQueue

__all__ = ["ExtendedTaskUpdater", "MessageLike"]

MessageLike = Union[ExtendedMessage, list[ExtendedPart], ExtendedPart, str]
"""What any `message=` argument accepts. A bare `str` becomes a single text
part, a part or list of parts becomes an agent message, and an
`ExtendedMessage` is used as given (with ids filled in if absent)."""


def _require_part(part: object) -> None:
    if not isinstance(part, ExtendedPart):
        raise TypeError(
            f"ExtendedTaskUpdater expected an ExtendedPart, got {type(part).__name__}. "
            "Build parts with a2a_utility.schema.ExtendedPart (from_text/thinking/"
            "source_reference/file) — raw dicts and native a2a protobuf Parts are "
            "rejected here so a wrongly-typed value fails at the source instead of "
            "silently reaching the native queue and blowing up downstream."
        )


class ExtendedTaskUpdater(TaskUpdater):
    def __init__(
        self,
        context: ExtendedRequestContext,
        event_queue: ExtendedEventQueue,
        *,
        artifact_id_generator: Optional[IDGenerator] = None,
        message_id_generator: Optional[IDGenerator] = None,
    ) -> None:
        native_context = context._native
        if native_context.current_task is not None:
            task = native_context.current_task
        elif native_context.message is not None:
            task = new_task_from_user_message(native_context.message)
        else:
            # No existing task and no message to build one from — the shape
            # AgentExecutor.cancel() hits when a cancel RPC arrives for a
            # task that hasn't sent its own Task event yet. Falling through
            # to new_task_from_user_message(None) raises AttributeError deep
            # in native code instead of anything callable here, which turns
            # a plain cancel into an unrelated crash.
            task = Task(
                id=native_context.task_id or str(uuid.uuid4()),
                context_id=native_context.context_id or str(uuid.uuid4()),
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        super().__init__(
            # Native TaskUpdater needs the raw queue. ExtendedEventQueue is this
            # package's own sibling class, not domain-agent-facing — reaching its
            # private attribute here is internal plumbing, not a public escape hatch.
            event_queue._eq,
            task.id,
            task.context_id,
            artifact_id_generator=artifact_id_generator,
            message_id_generator=message_id_generator,
        )
        # Overwrite native's raw assignment with the typed instance we were given,
        # so the validation in ExtendedEventQueue.enqueue_event applies to every
        # event this updater publishes.
        self.event_queue = event_queue
        self._task = task
        self._task_enqueued = False

    # ---- internals ---------------------------------------------------- #
    async def _ensure_task(self) -> None:
        if not self._task_enqueued:
            # Set before awaiting, not after: two concurrent status updates
            # would otherwise both see False and enqueue the Task twice, and
            # native logs an error and drops the second one.
            self._task_enqueued = True
            await self.event_queue.enqueue_event(self._task)

    def _coerce(self, message: Optional[MessageLike]) -> Optional[_ProtoMessage]:
        """Normalizes anything MessageLike into the native Message native
        methods want, filling in this task's ids and a generated message id
        when the caller didn't supply them."""
        if message is None:
            return None
        if isinstance(message, str):
            return self.new_agent_message([ExtendedPart.from_text(message)]).to_protobuf()
        if isinstance(message, ExtendedPart):
            return self.new_agent_message([message]).to_protobuf()
        if isinstance(message, list):
            return self.new_agent_message(message).to_protobuf()
        if isinstance(message, ExtendedMessage):
            filled = message.model_copy(
                update={
                    "message_id": message.message_id or self._new_message_id(),
                    "task_id": message.task_id or self.task_id,
                    "context_id": message.context_id or self.context_id,
                }
            )
            return filled.to_protobuf()
        raise TypeError(
            f"message= expected str, ExtendedPart, list[ExtendedPart], or "
            f"ExtendedMessage; got {type(message).__name__}."
        )

    def _new_message_id(self) -> str:
        return self._message_id_generator.generate(
            IDGeneratorContext(task_id=self.task_id, context_id=self.context_id)
        )

    # ---- status ------------------------------------------------------- #
    async def update_status(
        self,
        state: ExtendedTaskState,
        message: Optional[MessageLike] = None,
        timestamp: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Publishes a status update, enqueueing the Task first if this is the
        first event for it.

        Raises:
            RuntimeError: the task already reached a terminal state (native's
                own per-instance guard).
        """
        await self._ensure_task()
        await super().update_status(
            state.to_proto(),
            message=self._coerce(message),
            timestamp=timestamp,
            metadata=metadata,
        )

    async def submit(self, message: Optional[MessageLike] = None) -> None:
        """Marks the task SUBMITTED — acknowledged, not started."""
        await self.update_status(ExtendedTaskState.SUBMITTED, message)

    async def start_work(self, message: Optional[MessageLike] = None) -> None:
        """Marks the task WORKING. Nothing sends this for you — a handler
        that wants the caller to see work starting sends it itself, exactly
        as a native implementation would."""
        await self.update_status(ExtendedTaskState.WORKING, message)

    async def complete(self, message: Optional[MessageLike] = None) -> None:
        """Terminal: the task succeeded."""
        await self.update_status(ExtendedTaskState.COMPLETED, message)

    async def failed(self, message: Optional[MessageLike] = None) -> None:
        """Terminal: the task could not be completed."""
        await self.update_status(ExtendedTaskState.FAILED, message)

    async def cancel(self, message: Optional[MessageLike] = None) -> None:
        """Terminal: the task was abandoned, by the agent or on request."""
        await self.update_status(ExtendedTaskState.CANCELED, message)

    async def reject(self, message: Optional[MessageLike] = None) -> None:
        """Terminal: the agent declined the task — not attempted and failed,
        but refused. This is the state an authorization failure lands in."""
        await self.update_status(ExtendedTaskState.REJECTED, message)

    async def requires_input(self, message: Optional[MessageLike] = None) -> None:
        """Pauses the task pending more input from the caller. The framework
        resumes it with a *new* execute() call — see
        ExtendedRequestContext.is_resuming."""
        await self.update_status(ExtendedTaskState.INPUT_REQUIRED, message)

    async def requires_auth(self, message: Optional[MessageLike] = None) -> None:
        """Pauses the task pending credentials from the caller. Resumed the
        same way as requires_input()."""
        await self.update_status(ExtendedTaskState.AUTH_REQUIRED, message)

    # ---- content ------------------------------------------------------ #
    async def add_artifact(
        self,
        parts: list[ExtendedPart],
        artifact_id: Optional[str] = None,
        name: Optional[str] = None,
        metadata: Optional[dict] = None,
        append: Optional[bool] = None,
        last_chunk: Optional[bool] = None,
        extensions: Optional[list[str]] = None,
    ) -> None:
        """Publishes an artifact chunk. Callable as many times as the handler
        likes — use `append`/`last_chunk` to stream one artifact in pieces."""
        for part in parts:
            _require_part(part)
        await self._ensure_task()
        await super().add_artifact(
            [p.to_protobuf() for p in parts],
            artifact_id=artifact_id,
            name=name,
            metadata=metadata,
            append=append,
            last_chunk=last_chunk,
            extensions=extensions,
        )

    def new_agent_message(
        self, parts: list[ExtendedPart], metadata: Optional[dict] = None
    ) -> ExtendedMessage:
        """Builds an agent message bound to this task. Only *creates* it —
        pass the result to a `message=` argument to actually send it.

        Most callers don't need this: every `message=` accepts a plain string
        or a list of parts and builds the message itself.
        """
        for part in parts:
            _require_part(part)
        return ExtendedMessage(
            role=MessageRole.AGENT,
            parts=parts,
            message_id=self._new_message_id(),
            task_id=self.task_id,
            context_id=self.context_id,
            metadata=metadata,
        )

    def as_part_emitter(self) -> PartEmitter:
        """Adapts this updater into a `PartEmitter` — a one-argument callback
        that streams a part as a WORKING status message.

        For business logic that wants a callback, not a method surface: wrap
        it in `as_thinking_emitter(...)` to get the plain `str -> None`
        shape that progress callbacks (nanobot's `on_progress=`, deepagents'
        `emit_thought`) already expect.
        """

        async def emit(part: ExtendedPart) -> None:
            await self.update_status(ExtendedTaskState.WORKING, [part])

        return emit
