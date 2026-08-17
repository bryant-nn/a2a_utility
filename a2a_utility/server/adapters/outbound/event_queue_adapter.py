"""ExtendedEventQueue — thin typed wrapper over the native a2a EventQueue.

Exposes two methods. `enqueue_message` is the one a handler should reach
for: it publishes a standalone `ExtendedMessage` — native's other "immediate
response" workflow, replying without ever creating a `Task` at all, something
`ExtendedTaskUpdater` structurally cannot do since it's built entirely around
a Task's lifecycle. It takes and needs nothing from `a2a.*`.

`enqueue_event` is the advanced escape hatch underneath it, mirroring the
native `a2a.server.events.EventQueue` ABC directly — whose own docstring
explains why it's a single method: "Producer-side interface passed to
`AgentExecutor.execute`/`cancel`. Exposes only `enqueue_event`. The consumer
is framework-managed and not part of the public surface." It takes a raw
native `Event`, so using it means building or holding an `a2a.types` object
yourself — reasonable for code that already has one (this class's own
`enqueue_message`, or `ExtendedTaskUpdater`), not the first thing a handler
should reach for.

An earlier version of this class also re-exported `dequeue_event`/`tap`/
`close`/`task_done`/`is_closed` for "full 1:1 parity". Those exist on the
concrete implementations (`EventQueueSource`/`EventQueueSink`), not on the
interface the framework hands an executor, and handing them to a domain
agent is actively harmful — a handler calling `close()` tears down the
stream the framework is still consuming from, and `dequeue_event()` steals
events from the framework's own consumer. Parity with the *interface* is the
real parity; parity with an implementation detail is a footgun.

Task-id validation, traced against actual native behavior: an event carrying
a `task_id` that doesn't match the one the framework assigned for this
request is rejected by `a2a.server.tasks.task_manager.TaskManager.
save_task_event()`. That rejection happens on the framework's own
event-processing path, not inside the coroutine running the handler, so
nothing in `AgentExecutor` can catch it. This class runs the same check
earlier — inside the handler's own coroutine, where a plain `ValueError` is
trivially catchable.

The check only applies to events that actually carry a task id. A standalone
`Message` published through native's message-mode workflow legitimately
leaves `task_id` empty (proto3 default `""`), and the framework's
`EventConsumer._handle_message_event` accepts it as-is; validating it against
this request's task id would reject the exact workflow `enqueue_message`
exists to make reachable.

No `.native` escape hatch: the raw native `EventQueue` is never reachable
from domain-agent code, by design (see `application/dtos.py` and
`agent_handler_port.py` for the full "domain agent never touches a2a.*"
boundary this is part of).
"""

from __future__ import annotations

from typing import Optional

from a2a.server.events import Event, EventQueue
from a2a.types import Task

from ....schema import ExtendedMessage

__all__ = ["ExtendedEventQueue"]


def _event_task_id(event: Event) -> Optional[str]:
    """The task id an event carries, or None if it carries none.

    A `Task` names itself with `id`; the other three event types use
    `task_id`. Proto3 scalars have no presence, so an unset id reads as `""`
    — normalized to None here so callers can't confuse "no task id" with a
    task literally identified by the empty string.
    """
    raw = event.id if isinstance(event, Task) else event.task_id
    return raw or None


class ExtendedEventQueue:
    def __init__(self, event_queue: EventQueue, *, expected_task_id: Optional[str] = None) -> None:
        self._eq = event_queue  # private — no public accessor to the raw native queue
        self._expected_task_id = expected_task_id

    async def enqueue_message(self, message: ExtendedMessage) -> None:
        """Publishes a standalone reply — native's message-mode workflow.

        The typed, no-`a2a.*` way to answer without ever creating a `Task`.
        Converts to the native protobuf `Message` internally and runs it
        through the same task-id validation as `enqueue_event`.

        Raises:
            ValueError: `message.task_id` is set and doesn't match this
                request's task_id. Leaving `task_id` unset is the normal case
                for a fresh message-mode reply and is never checked.
        """
        await self.enqueue_event(message.to_protobuf())

    async def enqueue_event(self, event: Event) -> None:
        """Publishes any native Event — the advanced escape hatch.

        Most handlers want `enqueue_message` instead: it takes an
        `ExtendedMessage`, converts internally, and needs no `a2a.*` import.
        Reach for this one directly only when you already hold a native
        `Task`/`TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` — which
        `ExtendedTaskUpdater` already builds and publishes correctly, so
        there is rarely a reason to call this with one of those yourself.

        Raises:
            ValueError: the event carries a task_id that isn't this request's.
                Native would reject the same event deep inside its own
                event-processing path, outside any exception handler reachable
                from the handler; raising here keeps it catchable.
        """
        event_task_id = _event_task_id(event)
        if (
            self._expected_task_id is not None
            and event_task_id is not None
            and event_task_id != self._expected_task_id
        ):
            raise ValueError(
                f"enqueue_event: event's task_id {event_task_id!r} doesn't match "
                f"this request's task_id {self._expected_task_id!r}. Native would "
                "reject this deep inside its own event-processing path — outside "
                "any exception handler here — so it's checked eagerly instead. "
                "For Task/TaskStatusUpdateEvent/TaskArtifactUpdateEvent, prefer "
                "ExtendedTaskUpdater, which already gets this right. A standalone "
                "Message with no task_id at all is fine and is not checked — "
                "prefer enqueue_message() for that case."
            )
        await self._eq.enqueue_event(event)
