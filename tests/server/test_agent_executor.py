from __future__ import annotations

from a2a.types import TaskState

from a2a_utility.schema import ExtendedPart
from a2a_utility.server import DomainAgentExecutorPort, Progress, PublishArtifact
from a2a_utility.server.adapters.inbound.agent_executor import AgentExecutor


def _status_states(event_queue) -> list[TaskState.ValueType]:
    return [
        e.status.state
        for e in event_queue.events
        if e.__class__.__name__ == "TaskStatusUpdateEvent"
    ]


class _NoOp(DomainAgentExecutorPort):
    async def execute(self, context):
        return
        yield  # pragma: no cover — makes this an async generator


async def test_not_yielding_anything_still_completes_the_task(request_context, event_queue):
    """No imperative start_work()/complete() to forget anymore — a domain
    executor that yields nothing at all still ends up COMPLETED, because
    completion is implicit at generator exhaustion, not something the
    executor opts into. This is the declarative model's whole point, and a
    deliberate behavior change from the old imperative AgentHandlerPort
    (which sent nothing at all if the handler did nothing)."""
    executor = AgentExecutor(_NoOp())
    await executor.execute(request_context, event_queue)
    assert _status_states(event_queue) == [TaskState.TASK_STATE_COMPLETED]


async def test_executor_yielding_progress_and_artifact_completes_normally(request_context, event_queue):
    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            yield Progress("go")
            yield PublishArtifact(parts=[ExtendedPart.from_text("42")])

    executor = AgentExecutor(Executor())
    await executor.execute(request_context, event_queue)

    states = _status_states(event_queue)
    assert states == [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED]


async def test_unhandled_exception_becomes_failed_via_the_same_updater(request_context, event_queue):
    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            raise ValueError("boom")
            yield  # pragma: no cover

    executor = AgentExecutor(Executor())
    await executor.execute(request_context, event_queue)

    states = _status_states(event_queue)
    assert states == [TaskState.TASK_STATE_FAILED]
    failed_event = next(e for e in event_queue.events if e.__class__.__name__ == "TaskStatusUpdateEvent")
    assert "boom" in failed_event.status.message.parts[0].text


async def test_exception_mid_stream_after_content_already_sent(request_context, event_queue):
    """Unlike the old imperative design (where a handler's own updater and
    the exception safety net's throwaway updater were two different
    instances — so a handler that completed and then raised anyway could
    produce a spurious duplicate terminal status), there is now exactly one
    `TaskUpdater` per execute() call, shared by the happy path and the except
    block. A generator can't both reach implicit completion *and* raise
    afterward — raising always happens mid-iteration, before completion is
    ever reached — so this scenario (terminal state already reached, then
    failed() called again) is structurally unreachable now, not just handled
    gracefully."""
    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            yield Progress("go")
            yield PublishArtifact(parts=[ExtendedPart.from_text("partial")])
            raise RuntimeError("crashed mid-stream")

    executor = AgentExecutor(Executor())
    await executor.execute(request_context, event_queue)  # must not raise

    states = _status_states(event_queue)
    assert states == [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_FAILED]


async def test_cancel_without_override_marks_canceled_with_no_message(request_context, event_queue):
    executor = AgentExecutor(_NoOp())
    await executor.cancel(request_context, event_queue)

    assert _status_states(event_queue) == [TaskState.TASK_STATE_CANCELED]
    canceled_event = next(e for e in event_queue.events if e.__class__.__name__ == "TaskStatusUpdateEvent")
    assert not canceled_event.status.HasField("message")


async def test_cancel_override_supplies_a_custom_message(request_context, event_queue):
    """The task is still marked CANCELED regardless of what cancel() returns
    — the override only supplies the message attached to that status, it
    doesn't get to choose a different outcome."""
    calls = []

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            return
            yield  # pragma: no cover

        async def cancel(self, context):
            calls.append(1)
            return "cleaned up gracefully"

    executor = AgentExecutor(Executor())
    await executor.cancel(request_context, event_queue)

    assert calls == [1]
    assert _status_states(event_queue) == [TaskState.TASK_STATE_CANCELED]
    canceled_event = next(e for e in event_queue.events if e.__class__.__name__ == "TaskStatusUpdateEvent")
    assert canceled_event.status.message.parts[0].text == "cleaned up gracefully"


async def test_cancel_override_raising_falls_back_to_the_default(request_context, event_queue):
    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            return
            yield  # pragma: no cover

        async def cancel(self, context):
            raise RuntimeError("cancel() itself is buggy")

    executor = AgentExecutor(Executor())
    await executor.cancel(request_context, event_queue)  # must not raise

    assert _status_states(event_queue) == [TaskState.TASK_STATE_CANCELED]


async def test_message_id_generator_is_used_by_the_exception_safety_net(request_context, event_queue):
    class FixedMessageGenerator:
        def generate(self, context):
            return "fixed-message-id"

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            raise ValueError("boom")
            yield  # pragma: no cover

    executor = AgentExecutor(Executor(), message_id_generator=FixedMessageGenerator())
    await executor.execute(request_context, event_queue)

    failed_event = next(e for e in event_queue.events if e.__class__.__name__ == "TaskStatusUpdateEvent")
    assert failed_event.status.message.message_id == "fixed-message-id"
