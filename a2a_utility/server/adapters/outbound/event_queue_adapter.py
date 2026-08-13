"""ExtendedEventQueue — the outbound adapter over the native a2a EventQueue.

`adapters/inbound/agent_executor.py` builds one per request and hands its
`.emit` method to the injected AgentHandlerPort as the streaming callback —
that's the entire "callback the domain agent gets" story, no separate port
type needed here. Wraps a native a2a TaskUpdater:

  - start_work()      -> the initial Task + a WORKING status
  - emit(part)        -> a live WORKING status message carrying this part's
    protobuf form (not text-only — an ExtendedPart can be thinking, a source
    reference, a file, anything). This bound method IS the callback handed to
    a domain agent's handler; the same ExtendedPart vocabulary streams live
    here and gets returned in the final `list[ExtendedPart]`.
  - complete(parts)   -> add parts as the final Artifact then COMPLETED, with
    NO trailing status message (so the artifact stays the unambiguous answer)
  - failed(text)      -> FAILED status
  - requires_input(text) -> INPUT_REQUIRED status (task pauses; framework
    re-invokes execute() when the follow-up arrives)
  - requires_auth(text)  -> AUTH_REQUIRED status
  - cancel(text)      -> CANCELED status (agent-initiated, from a
    HandlerCanceled return value, or from AgentExecutor.cancel() reacting to
    an externally-requested cancellation)

No separate outbound Port/Protocol is defined for this (unlike
AgentRegistryPort) — there's exactly one implementation and no anticipated
alternative, so a Protocol here would be ceremony without payoff.

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

from ....schema import ExtendedPart


class ExtendedEventQueue:
    def __init__(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
        self._task = task
        self._event_queue = event_queue
        self._u = TaskUpdater(event_queue, task.id, task.context_id)
        self._task_enqueued = False

    @property
    def native(self) -> EventQueue:
        """Escape hatch to the raw native EventQueue, for anything not wrapped here."""
        return self._event_queue

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

    async def emit(self, part: ExtendedPart) -> None:
        """Stream one live part (WORKING status message carrying this part).

        Pass this bound method directly as the PartEmitter callback into a
        domain agent's own business logic, or wrap it with
        `a2a_utility.schema.as_thinking_emitter()` for code that only ever
        streams plain thinking text.
        """
        await self._ensure_task()
        await self._u.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=self._u.new_agent_message([part.to_protobuf()]),
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

    async def requires_input(self, text: str) -> None:
        await self._ensure_task()
        await self._u.update_status(
            state=TaskState.TASK_STATE_INPUT_REQUIRED, message=new_text_message(text)
        )

    async def requires_auth(self, text: str) -> None:
        await self._ensure_task()
        await self._u.update_status(
            state=TaskState.TASK_STATE_AUTH_REQUIRED, message=new_text_message(text)
        )

    async def cancel(self, text: Optional[str] = None) -> None:
        await self._ensure_task()
        message = new_text_message(text) if text else None
        await self._u.update_status(state=TaskState.TASK_STATE_CANCELED, message=message)
