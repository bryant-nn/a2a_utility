"""ChatAgentExecutor — A2A inbound adapter for AGENT mode.

Bridges the A2A protocol to the ChatUseCase: it turns an incoming A2A task into a
`query`, streams the use case, and maps each StreamChunk back onto A2A events:

  - THINKING chunk -> a WORKING status update carrying the reasoning text, so the
    coordinator's trace UI can show the sub-agent thinking live.
  - ANSWER chunks  -> accumulated and emitted as the single final artifact.

Critical ordering note: the final
answer is delivered as an *artifact*, and NO trailing status message is sent
after it. google.adk.tools.agent_tool.AgentTool extracts a RemoteA2aAgent's final
answer by overwriting last_content with whatever event it saw last; a message
after the artifact (even a generic "done") would silently replace the real answer.
"""

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

from ...application.ports.inbound.chat_use_case_port import ChatUseCasePort
from ...domain.models.chat_message import StreamPhase


class ChatAgentExecutor(AgentExecutor):
    def __init__(self, chat_use_case: ChatUseCasePort) -> None:
        self._chat_use_case = chat_use_case

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(
            event_queue=event_queue, task_id=task.id, context_id=task.context_id
        )
        await task_updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Processing request..."),
        )

        query = get_message_text(context.message)
        if not query:
            await task_updater.add_artifact(
                parts=[new_text_part(text="No text input is provided!", media_type="text/plain")]
            )
            await task_updater.update_status(state=TaskState.TASK_STATE_COMPLETED)
            return

        answer_parts: list[str] = []
        try:
            async for chunk in self._chat_use_case.handle(query):
                if chunk.phase == StreamPhase.THINKING:
                    await task_updater.update_status(
                        state=TaskState.TASK_STATE_WORKING,
                        message=new_text_message(chunk.text),
                    )
                else:
                    answer_parts.append(chunk.text)
        except Exception as e:
            await task_updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=new_text_message(f"Agent error: {e}"),
            )
            return

        answer = "".join(answer_parts)
        # Final answer as an artifact, with NO trailing status message (see docstring).
        await task_updater.add_artifact(
            parts=[new_text_part(text=answer, media_type="text/plain")]
        )
        await task_updater.update_status(state=TaskState.TASK_STATE_COMPLETED)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")
