from __future__ import annotations

import contextlib
import logging
import uuid
from typing import Optional

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Task, TaskState, TaskStatus
from typing_extensions import override

from a2a_wrapper.events import (
    ArtifactResult,
    AuthRequired,
    InputRequired,
    Rejected,
    StatusMessage,
    TextChunk,
)
from a2a_wrapper.server.ports import DomainAgentExecutorPort
from a2a_wrapper.types import DomainContext, ExtendedPart


logger = logging.getLogger(__name__)


class BaseA2AWrapperExecutor(AgentExecutor):
    """
    Anti-corruption layer between A2A SDK and domain executors.

    Responsibilities:
        - Protobuf ↔ ExtendedPart conversion
        - Full task lifecycle management (submit → complete/failed)
        - Domain executor never touches A2A SDK types
    """

    def __init__(self, domain_executor: DomainAgentExecutorPort) -> None:
        self._domain_executor = domain_executor

    @staticmethod
    def _initial_task(context: RequestContext, task_id: str, context_id: str) -> Task:
        """Build the Task that must exist before any status/artifact event.

        Args:
            context: the native request context for this call.
            task_id: fallback id if the request carries none.
            context_id: fallback conversation id if the request carries none.

        Returns:
            `context.current_task` if this is a resume, else a Task built
            from `context.message`, else a minimal fallback Task (the shape
            a cancel RPC hits before any Task was ever sent).
        """
        if context.current_task is not None:
            return context.current_task
        if context.message is not None:
            return new_task_from_user_message(context.message)
        return Task(id=task_id, context_id=context_id, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Drive one task from the domain executor's StreamEvent generator.

        Args:
            context: the native request context (native AgentExecutor contract).
            event_queue: the native event queue (native AgentExecutor contract).
        """
        task_id = context.task_id or uuid.uuid4().hex
        context_id = context.context_id or uuid.uuid4().hex
        task = self._initial_task(context, task_id, context_id)
        is_resuming = context.current_task is not None

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        domain_ctx = self._build_domain_context(context, task.id, task.context_id)

        if not is_resuming:
            # A resumed task already exists (paused at INPUT_REQUIRED/
            # AUTH_REQUIRED) — resubmitting it through SUBMITTED->WORKING
            # here breaks the framework's routing for that call.
            await event_queue.enqueue_event(task)
            await updater.submit()
            await updater.start_work()

        has_artifact = False

        try:
            async for event in self._domain_executor.execute(domain_ctx):
                if isinstance(event, TextChunk):
                    await updater.update_status(
                        state=TaskState.TASK_STATE_WORKING,
                        message=updater.new_agent_message(
                            parts=[ExtendedPart(text=event.text).to_protobuf()],
                        ),
                    )

                elif isinstance(event, StatusMessage):
                    proto_parts = [p.to_protobuf() for p in event.parts]
                    await updater.update_status(
                        state=TaskState.TASK_STATE_WORKING,
                        message=updater.new_agent_message(parts=proto_parts),
                    )

                elif isinstance(event, ArtifactResult):
                    proto_parts = [p.to_protobuf() for p in event.parts]
                    await updater.add_artifact(
                        parts=proto_parts,
                        name=event.name,
                        metadata=event.metadata or None,
                        artifact_id=event.artifact_id,
                        append=event.append,
                        last_chunk=event.last_chunk,
                    )
                    has_artifact = True

                elif isinstance(event, InputRequired):
                    proto_parts = [p.to_protobuf() for p in event.parts]
                    await updater.update_status(
                        state=TaskState.TASK_STATE_INPUT_REQUIRED,
                        message=updater.new_agent_message(parts=proto_parts),
                    )
                    return

                elif isinstance(event, AuthRequired):
                    proto_parts = [p.to_protobuf() for p in event.parts]
                    await updater.update_status(
                        state=TaskState.TASK_STATE_AUTH_REQUIRED,
                        message=updater.new_agent_message(parts=proto_parts),
                    )
                    return

                elif isinstance(event, Rejected):
                    proto_parts = [p.to_protobuf() for p in event.parts]
                    await updater.update_status(
                        state=TaskState.TASK_STATE_REJECTED,
                        message=updater.new_agent_message(parts=proto_parts),
                    )
                    return

                else:
                    logger.warning('Unknown StreamEvent type: %s', type(event))

            if not has_artifact:
                logger.warning('task_id=%s completed with no artifacts', task_id)

            await updater.complete()

        except Exception as exc:
            logger.exception('DomainAgentExecutor failed: %s', exc)
            with contextlib.suppress(RuntimeError):  # already-terminal guard
                await updater.failed(
                    message=updater.new_agent_message(
                        parts=[ExtendedPart(text=f'Agent error: {exc}').to_protobuf()],
                    ),
                )

    @override
    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """React to an externally-requested cancel RPC.

        Args:
            context: the native request context (native AgentExecutor contract).
            event_queue: the native event queue (native AgentExecutor contract).
        """
        task_id = context.task_id or uuid.uuid4().hex
        context_id = context.context_id or uuid.uuid4().hex
        task = self._initial_task(context, task_id, context_id)
        domain_ctx = self._build_domain_context(context, task.id, task.context_id)

        custom_message: Optional[str] = None
        with contextlib.suppress(Exception):  # domain cancel() failing must not block CANCELED
            custom_message = await self._domain_executor.cancel(domain_ctx)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        with contextlib.suppress(RuntimeError):
            await event_queue.enqueue_event(task)  # duplicate is logged and ignored, not fatal
            message = (
                updater.new_agent_message(parts=[ExtendedPart(text=custom_message).to_protobuf()])
                if custom_message
                else None
            )
            await updater.cancel(message=message)

    @staticmethod
    def _build_domain_context(
        context: RequestContext,
        task_id: str,
        context_id: str,
    ) -> DomainContext:
        """Convert the native request context into a DomainContext.

        Args:
            context: the native request context for this call.
            task_id: this task's id.
            context_id: this task's conversation id.

        Returns:
            A DomainContext with `is_resuming`/`prior_parts` set when
            `context.current_task` is paused at INPUT_REQUIRED/AUTH_REQUIRED.
        """
        parts: list[ExtendedPart] = []

        if context.message and context.message.parts:
            parts = [ExtendedPart.from_protobuf(p) for p in context.message.parts]

        if not parts:
            user_text = context.get_user_input() or ''
            if user_text:
                parts = [ExtendedPart(text=user_text)]

        is_resuming = False
        prior_parts: list[ExtendedPart] = []
        current = context.current_task
        if current is not None and current.status.state in (
            TaskState.TASK_STATE_INPUT_REQUIRED,
            TaskState.TASK_STATE_AUTH_REQUIRED,
        ):
            is_resuming = True
            if current.status.HasField('message'):
                prior_parts = [ExtendedPart.from_protobuf(p) for p in current.status.message.parts]

        return DomainContext(
            task_id=task_id,
            context_id=context_id,
            message_id=(context.message.message_id if context.message else None),
            parts=parts,
            metadata=(
                dict(context.message.metadata)
                if context.message and context.message.metadata
                else None
            ),
            is_resuming=is_resuming,
            prior_parts=prior_parts,
        )
