"""DiscoveryAgentExecutor — A2A inbound adapter for DISCOVERY mode.

Zero LLM. Interprets the incoming task text as a discovery query ("who can do
X?"), asks the DiscoveryUseCase, and returns the matching agents as a JSON
artifact. An empty query returns the full list.

Drives a native `TaskUpdater`/`EventQueue` directly, sharing `_native_task.
py`'s `initial_task()` with `agent_executor.py` (AGENT mode) — both adapters
need the same "build the Task that must precede any status event" logic, and
this keeps it in one place rather than two. DISCOVERY's own flow is strictly
linear (always produces content, never pauses or replies message-mode), so
unlike AGENT mode there's no lazy/conditional Task creation here — it's
always sent up front.
"""

import contextlib
import json
import uuid

from a2a.helpers import new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Task

from ...application.ports.inbound.discovery_use_case_port import DiscoveryUseCasePort
from ._native_task import initial_task


class DiscoveryAgentExecutor(AgentExecutor):
    def __init__(self, discovery_use_case: DiscoveryUseCasePort) -> None:
        self._discovery_use_case = discovery_use_case

    def _build(self, context: RequestContext, event_queue: EventQueue) -> tuple[TaskUpdater, Task]:
        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        task = initial_task(context, task_id, context_id)
        return TaskUpdater(event_queue, task.id, task.context_id), task

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater, task = self._build(context, event_queue)
        await event_queue.enqueue_event(task)
        await updater.start_work()

        query = context.get_user_input()
        try:
            if query.strip():
                agents = await self._discovery_use_case.search(query)
            else:
                agents = await self._discovery_use_case.list_all()
        except Exception as e:
            await updater.failed(updater.new_agent_message([new_text_part(f"Discovery error: {e}")]))
            return

        result = json.dumps({"agents": [a.to_dict() for a in agents]}, ensure_ascii=False)
        await updater.add_artifact([new_text_part(result, media_type="application/json")])
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Acknowledges an externally-requested cancellation.

        Discovery lookups are short enough that a cancel almost never arrives
        mid-flight, but this must not raise: the framework calls it on the
        producer task, so an exception here gets caught by
        `ActiveTask._run_producer` and turns the task FAILED — a worse
        outcome than simply honoring the cancel.
        """
        updater, task = self._build(context, event_queue)
        with contextlib.suppress(RuntimeError):
            await event_queue.enqueue_event(task)
            await updater.cancel()
