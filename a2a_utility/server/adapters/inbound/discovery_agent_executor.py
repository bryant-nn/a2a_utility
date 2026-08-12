"""DiscoveryAgentExecutor — A2A inbound adapter for DISCOVERY mode.

Zero LLM. Interprets the incoming task text as a discovery query ("who can do
X?"), asks the DiscoveryUseCase, and returns the matching agents as a JSON
artifact. An empty query returns the full list.
"""

import json

from a2a.helpers import (
    get_message_text,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from ...application.ports.inbound.discovery_use_case_port import DiscoveryUseCasePort


class DiscoveryAgentExecutor(AgentExecutor):
    def __init__(self, discovery_use_case: DiscoveryUseCasePort) -> None:
        self._discovery_use_case = discovery_use_case

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(
            event_queue=event_queue, task_id=task.id, context_id=task.context_id
        )

        query = get_message_text(context.message) or ""
        try:
            if query.strip():
                agents = await self._discovery_use_case.search(query)
            else:
                agents = await self._discovery_use_case.list_all()
        except Exception as e:
            await task_updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message(f"Discovery error: {e}"),
            )
            return

        result = json.dumps(
            {"agents": [a.to_dict() for a in agents]}, ensure_ascii=False
        )
        await task_updater.add_artifact(
            parts=[new_text_part(text=result, media_type="application/json")]
        )
        await task_updater.update_status(state=TaskState.TASK_STATE_COMPLETED)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
