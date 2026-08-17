"""DiscoveryAgentExecutor — A2A inbound adapter for DISCOVERY mode.

Zero LLM. Interprets the incoming task text as a discovery query ("who can do
X?"), asks the DiscoveryUseCase, and returns the matching agents as a JSON
artifact. An empty query returns the full list.

Drives the task through `ExtendedTaskUpdater`/`ExtendedPart`, the same
vocabulary a domain agent's handler uses — a registry node is an A2A agent
like any other, so it has no reason to reach for the native `TaskUpdater`
directly.
"""

import json

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from ....schema import ExtendedPart
from ...application.dtos import ExtendedRequestContext
from ...application.ports.inbound.discovery_use_case_port import DiscoveryUseCasePort
from ..outbound.event_queue_adapter import ExtendedEventQueue
from ..outbound.task_updater_adapter import ExtendedTaskUpdater


class DiscoveryAgentExecutor(AgentExecutor):
    def __init__(self, discovery_use_case: DiscoveryUseCasePort) -> None:
        self._discovery_use_case = discovery_use_case

    def _task_updater(self, context: RequestContext, event_queue: EventQueue) -> ExtendedTaskUpdater:
        return ExtendedTaskUpdater(
            ExtendedRequestContext(context),
            ExtendedEventQueue(event_queue, expected_task_id=context.task_id),
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_updater = self._task_updater(context, event_queue)
        await task_updater.start_work()

        query = context.get_user_input()
        try:
            if query.strip():
                agents = await self._discovery_use_case.search(query)
            else:
                agents = await self._discovery_use_case.list_all()
        except Exception as e:
            await task_updater.failed(f"Discovery error: {e}")
            return

        result = json.dumps({"agents": [a.to_dict() for a in agents]}, ensure_ascii=False)
        await task_updater.add_artifact(
            [ExtendedPart.from_text(result, media_type="application/json")]
        )
        await task_updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Acknowledges an externally-requested cancellation.

        Discovery lookups are short enough that a cancel almost never arrives
        mid-flight, but this must not raise: the framework calls it on the
        producer task, so an exception here gets caught by
        `ActiveTask._run_producer` and turns the task FAILED — a worse
        outcome than simply honoring the cancel.
        """
        await self._task_updater(context, event_queue).cancel()
