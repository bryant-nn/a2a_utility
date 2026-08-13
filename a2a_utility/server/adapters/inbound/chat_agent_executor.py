"""ExecutorAgentExecutor — A2A inbound adapter for the Executor-Callback model.

adjust.md §step-3. Flow per request:

  A2A Request
    -> normalize to application Task (id, contextId, message text)
    -> middleware builds a mock UserContext (+ live emit_thought hook)
    -> call the developer's ExecutorCallback(task, ctx) -> list[Part]
    -> pack the Typed Parts into ONE Artifact on the Task response, COMPLETED.

Live streaming is preserved: the callback (or the handler_to_executor bridge)
may call ctx.emit_thought(text) during execution, which is mapped to an A2A
WORKING status message so a coordinator's trace UI shows thinking as it happens.

Critical ordering (unchanged): the final answer rides in the Artifact and NO
status message is sent after it — google-adk's AgentTool overwrites last_content
with the last event it sees, so a trailing message would clobber the answer.
The `text`-dataType Part maps to an a2a text part, which is exactly what
RemoteA2aAgent reads back as the answer; thinking_process/source_reference/file
Parts map to data parts and stay out of that text channel.
"""

from a2a.helpers import (
    get_message_text,
    new_task_from_user_message,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from ...application.dtos import Task
from ...application.ports.inbound.executor_callback import ExecutorCallback
from .dtos import ArtifactDTO
from .middleware import build_user_context


class ExecutorAgentExecutor(AgentExecutor):
    def __init__(self, executor: ExecutorCallback) -> None:
        self._executor = executor

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        a2a_task = context.current_task
        if not a2a_task:
            a2a_task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(a2a_task)

        task_updater = TaskUpdater(
            event_queue=event_queue, task_id=a2a_task.id, context_id=a2a_task.context_id
        )
        await task_updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Processing request..."),
        )

        # Live "thinking" hook: each call surfaces as a WORKING status message.
        async def emit_thought(text: str) -> None:
            await task_updater.update_status(
                state=TaskState.TASK_STATE_WORKING,
                message=new_text_message(text),
            )

        task = Task(
            id=a2a_task.id,
            context_id=a2a_task.context_id,
            message=get_message_text(context.message) or "",
        )
        user_context = build_user_context(emit_thought=emit_thought)

        try:
            parts = await self._executor(task, user_context)
        except Exception as e:
            await task_updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message(f"Agent error: {e}"),
            )
            return

        a2a_parts = ArtifactDTO.from_domain(parts).to_a2a_parts()
        # Final answer(s) as a single Artifact, NO trailing status message.
        await task_updater.add_artifact(parts=a2a_parts)
        await task_updater.update_status(state=TaskState.TASK_STATE_COMPLETED)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
