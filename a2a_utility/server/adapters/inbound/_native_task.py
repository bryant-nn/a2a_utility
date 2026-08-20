"""Private plumbing shared by `agent_executor.py` (AGENT mode) and
`discovery_agent_executor.py` (DISCOVERY mode): the two pieces of
Task-lifecycle correctness that used to live in the now-deleted
`ExtendedTaskUpdater`.

Not a public port or adapter — no `__all__`, nothing re-exported from
`server/__init__.py`. It exists so this logic has exactly one copy instead of
two independently-maintained ones.
"""

from __future__ import annotations

from typing import Optional

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import RequestContext
from a2a.server.id_generator import IDGenerator, IDGeneratorContext
from a2a.types import Message, Task, TaskState, TaskStatus

from ....schema import ExtendedMessage, ExtendedPart, MessageLike, MessageRole


def initial_task(context: RequestContext, task_id: str, context_id: str) -> Task:
    """The Task that must be enqueued before any status/artifact event for
    this request. Three cases, in order:

      - `context.current_task` is set: this execute() call is a resume
        (INPUT_REQUIRED/AUTH_REQUIRED continuation) — reuse it.
      - `context.message` is set: the normal first call — build from it.
      - Neither: a cancel RPC arriving before this task ever sent its own
        Task event. Falling through to `new_task_from_user_message(None)`
        raises deep in native code instead of anything callable here, so
        this constructs a minimal Task directly.
    """
    if context.current_task is not None:
        return context.current_task
    if context.message is not None:
        return new_task_from_user_message(context.message)
    return Task(id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))


def coerce_message(
    message: Optional[MessageLike],
    *,
    task_id: str,
    context_id: str,
    message_id_generator: IDGenerator,
) -> Optional[Message]:
    """`MessageLike` -> native `Message`, filling in ids that weren't given.

    A bare `str`/`ExtendedPart`/`list[ExtendedPart]` becomes an agent message
    bound to this task; an `ExtendedMessage` is used as given, with any
    missing ids filled from this task's own.
    """
    if message is None:
        return None
    if isinstance(message, str):
        message = [ExtendedPart.from_text(message)]
    if isinstance(message, ExtendedPart):
        message = [message]
    if isinstance(message, list):
        message = ExtendedMessage(role=MessageRole.AGENT, parts=message)
    if isinstance(message, ExtendedMessage):
        filled = message.model_copy(
            update={
                "message_id": message.message_id
                or message_id_generator.generate(IDGeneratorContext(task_id=task_id, context_id=context_id)),
                "task_id": message.task_id or task_id,
                "context_id": message.context_id or context_id,
            }
        )
        return filled.to_protobuf()
    raise TypeError(
        f"message= expected str, ExtendedPart, list[ExtendedPart], or "
        f"ExtendedMessage; got {type(message).__name__}."
    )
