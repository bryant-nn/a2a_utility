"""Application-layer DTO — ExtendedRequestContext, the shape
AgentHandlerPort's first argument is expressed in.

Wraps the native a2a RequestContext and exposes the subset of it a handler
actually needs, converted into `a2a_utility.schema` types at the boundary:
`.message` is an `ExtendedMessage`, `.current_task` an `ExtendedTask`. A
handler reading either of those never touches a protobuf object, which is the
whole point — previously both returned native `a2a.types` messages, so any
handler that inspected the incoming turn or the prior task state was forced
to `import a2a`.

The constructor still takes the native type: this IS the conversion boundary,
the same pragmatic trade-off `schema/parts.py` makes with `a2a.types.Part`.

There is no declarative task-ending result type here (no HandlerResult) — a
handler drives `ExtendedTaskUpdater` directly and returns None, the same
shape as writing native `AgentExecutor.execute(context, event_queue) ->
None`. See that module's docstring for the reasoning.
"""

from __future__ import annotations

from typing import Any, Optional

from a2a.server.agent_execution import RequestContext

from ...schema import ExtendedMessage, ExtendedTask, ExtendedTaskState


class ExtendedRequestContext:
    def __init__(self, context: RequestContext) -> None:
        self._context = context
        self._message: Optional[ExtendedMessage] = None
        self._current_task: Optional[ExtendedTask] = None
        self._current_task_read = False

    @property
    def _native(self) -> RequestContext:
        """The wrapped native RequestContext.

        Package-internal, not a domain-agent escape hatch: `ExtendedTaskUpdater`
        needs the native context to build the initial `Task` protobuf, the same
        way it reaches `ExtendedEventQueue._eq` for the native queue. Single
        underscore because it is a2a_utility's own plumbing between sibling
        classes — a handler reaching for it is working around the boundary this
        package exists to hold, and nothing here is designed to support that.
        """
        return self._context

    def get_user_input(self, delimiter: str = "\n") -> str:
        """The incoming turn's text content, joined by `delimiter`."""
        return self._context.get_user_input(delimiter)

    @property
    def task_id(self) -> Optional[str]:
        return self._context.task_id

    @property
    def context_id(self) -> Optional[str]:
        """The conversation id — stable across the turns of one exchange,
        where task_id identifies a single unit of work within it."""
        return self._context.context_id

    @property
    def message(self) -> Optional[ExtendedMessage]:
        """The incoming turn, as a typed message.

        Converted once and cached: protobuf -> pydantic is not free, and a
        handler that reads `.message` in a loop shouldn't pay for it each
        time.
        """
        if self._message is None and self._context.message is not None:
            self._message = ExtendedMessage.from_protobuf(self._context.message)
        return self._message

    @property
    def current_task(self) -> Optional[ExtendedTask]:
        """The task as the server already knows it, or None on a first call.

        Non-None mainly when resuming after requires_input()/requires_auth()
        — see `is_resuming`. Cached like `.message`; the `_current_task_read`
        flag distinguishes "not converted yet" from "converted, and there was
        no task".
        """
        if not self._current_task_read:
            native = self._context.current_task
            self._current_task = ExtendedTask.from_protobuf(native) if native is not None else None
            self._current_task_read = True
        return self._current_task

    @property
    def metadata(self) -> dict[str, Any]:
        """Request-level metadata sent by the caller."""
        return self._context.metadata

    @property
    def is_resuming(self) -> bool:
        """True if this execute() call is the framework re-invoking a task
        previously paused via requires_input()/requires_auth().

        The framework does not resume the old coroutine — it starts a fresh
        execute() with the same task id — so a handler that paused must read
        `.current_task` for the prior state and history to pick up where it
        left off.
        """
        task = self.current_task
        return task is not None and task.state.is_interrupted
