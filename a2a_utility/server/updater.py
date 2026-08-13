"""TypedTaskUpdater — the typed façade over the native a2a EventQueue.

A domain agent writes its own AgentExecutor.execute(context, event_queue) and
uses this to emit results, so everything placed on the EventQueue conforms to
a2a_utility's data contract (ExtendedPart). It wraps a native a2a TaskUpdater:

  - start_work()         -> the initial Task + a WORKING status
  - emit_thinking(text)  -> a live WORKING status message (plain text, so ANY
    client/coordinator shows it as it streams)
  - add_artifact(parts)  -> a TaskArtifactUpdateEvent of ExtendedParts
  - complete(parts)      -> add the final Artifact then COMPLETED, with NO
    trailing status message (so the artifact stays the unambiguous answer)
  - failed(text)         -> FAILED status

Protocol rule (enforced by the a2a stream): a Task event MUST be enqueued before
any TaskStatusUpdateEvent/TaskArtifactUpdateEvent. This class enqueues the Task
lazily on the first emit, so the agent never has to think about it.
"""

from __future__ import annotations

from typing import Optional

from a2a.helpers import new_task_from_user_message, new_text_message
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from ..types import ExtendedPart


class TypedTaskUpdater:
    def __init__(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
        self._task = task
        self._event_queue = event_queue
        self._u = TaskUpdater(event_queue, task.id, task.context_id)
        self._task_enqueued = False

    @classmethod
    def of(cls, context: RequestContext, event_queue: EventQueue) -> "TypedTaskUpdater":
        return cls(context, event_queue)

    async def _ensure_task(self) -> None:
        # The stream requires a Task event before any status/artifact event.
        if not self._task_enqueued:
            await self._event_queue.enqueue_event(self._task)
            self._task_enqueued = True

    async def start_work(self, note: str = "Processing request...") -> None:
        await self._ensure_task()
        await self._u.update_status(
            state=TaskState.TASK_STATE_WORKING, message=new_text_message(note)
        )

    async def emit_thinking(self, text: str) -> None:
        """Stream one live 'thinking' line (plain text WORKING message)."""
        await self._ensure_task()
        await self._u.update_status(
            state=TaskState.TASK_STATE_WORKING, message=new_text_message(text)
        )

    async def add_artifact(
        self, parts: list[ExtendedPart], *, name: Optional[str] = None
    ) -> None:
        await self._ensure_task()
        await self._u.add_artifact(parts=[p.to_protobuf() for p in parts], name=name)

    async def complete(
        self, parts: Optional[list[ExtendedPart]] = None, *, name: Optional[str] = None
    ) -> None:
        await self._ensure_task()
        if parts:
            await self.add_artifact(parts, name=name)
        # No message on COMPLETED — keeps the artifact the unambiguous final answer.
        await self._u.update_status(state=TaskState.TASK_STATE_COMPLETED)

    async def failed(self, text: str) -> None:
        await self._ensure_task()
        await self._u.update_status(
            state=TaskState.TASK_STATE_FAILED, message=new_text_message(text)
        )
