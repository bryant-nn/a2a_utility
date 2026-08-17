from __future__ import annotations

import pytest
from a2a.types import TaskState

from a2a_utility.schema import ExtendedPart
from a2a_utility.server import ExtendedEventQueue, ExtendedTaskUpdater
from a2a_utility.server.adapters.inbound.agent_executor import AgentExecutor


def _status_states(event_queue) -> list[TaskState.ValueType]:
    return [
        e.status.state
        for e in event_queue.events
        if e.__class__.__name__ == "TaskStatusUpdateEvent"
    ]


async def test_handler_gets_an_extended_event_queue_matching_native_execute_signature(request_context, event_queue):
    received = {}

    async def handler(context, eq):
        received["eq_type"] = type(eq)

    executor = AgentExecutor(handler=handler)
    await executor.execute(request_context, event_queue)
    assert received["eq_type"] is ExtendedEventQueue


async def test_no_automatic_start_work_handler_must_send_it_itself(request_context, event_queue):
    async def handler(context, eq):
        pass  # deliberately does nothing

    executor = AgentExecutor(handler=handler)
    await executor.execute(request_context, event_queue)
    # No status/artifact events at all — matches native: nothing is auto-sent.
    assert event_queue.events == []


async def test_handler_completing_normally_sends_exactly_what_it_asked_for(request_context, event_queue):
    async def handler(context, eq):
        tu = ExtendedTaskUpdater(context, eq)
        await tu.start_work()
        await tu.add_artifact([ExtendedPart.from_text("42")])
        await tu.complete()

    executor = AgentExecutor(handler=handler)
    await executor.execute(request_context, event_queue)

    states = _status_states(event_queue)
    assert states == [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED]


async def test_handler_returning_without_responding_leaves_task_at_whatever_state_it_was(request_context, event_queue):
    """No 'forgot to respond' safety net anymore — matches native, which
    doesn't enforce this either. A handler that never calls anything just
    leaves the task un-terminated; that's on the handler, not a2a_utility."""
    async def handler(context, eq):
        tu = ExtendedTaskUpdater(context, eq)
        await tu.start_work()
        # ... and then just returns without completing.

    executor = AgentExecutor(handler=handler)
    await executor.execute(request_context, event_queue)

    assert _status_states(event_queue) == [TaskState.TASK_STATE_WORKING]


async def test_unhandled_exception_becomes_failed_via_throwaway_updater(request_context, event_queue):
    async def handler(context, eq):
        raise ValueError("boom")

    executor = AgentExecutor(handler=handler)
    await executor.execute(request_context, event_queue)

    states = _status_states(event_queue)
    assert states == [TaskState.TASK_STATE_FAILED]
    failed_event = next(e for e in event_queue.events if e.__class__.__name__ == "TaskStatusUpdateEvent")
    assert "boom" in failed_event.status.message.parts[0].text


async def test_exception_after_handler_already_completed_still_does_not_raise(request_context, event_queue):
    """Documented residual trade-off (see agent_executor.py's module
    docstring / the plan's "已知殘留取捨"): the except block's throwaway
    ExtendedTaskUpdater is a brand-new instance with its own fresh
    `_terminal_state_reached`, so native's per-instance double-terminal guard
    does NOT know the handler's own instance already completed — it does
    NOT raise, and a second (spurious) FAILED status genuinely gets sent.
    contextlib.suppress(RuntimeError) only helps for the cases where native
    *does* detect it (rare — would require reusing the same instance);
    it is not, and isn't meant to be, a guarantee against every double-send.
    This test locks in that this stays non-fatal, not that it stays clean."""
    async def handler(context, eq):
        tu = ExtendedTaskUpdater(context, eq)
        await tu.start_work()
        await tu.complete()
        raise RuntimeError("crashed during cleanup, after already answering")

    executor = AgentExecutor(handler=handler)
    await executor.execute(request_context, event_queue)  # must not raise

    states = _status_states(event_queue)
    assert states == [TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED]


async def test_cancel_without_on_cancel_marks_canceled(request_context, event_queue):
    executor = AgentExecutor(handler=lambda ctx, eq: None)
    await executor.cancel(request_context, event_queue)
    assert _status_states(event_queue) == [TaskState.TASK_STATE_CANCELED]


async def test_cancel_with_on_cancel_lets_it_fully_own_the_outcome(request_context, event_queue):
    calls = []

    async def on_cancel(context, eq):
        calls.append(1)
        tu = ExtendedTaskUpdater(context, eq)
        await tu.cancel()

    executor = AgentExecutor(handler=lambda ctx, eq: None, on_cancel=on_cancel)
    await executor.cancel(request_context, event_queue)

    assert calls == [1]
    assert _status_states(event_queue) == [TaskState.TASK_STATE_CANCELED]


async def test_on_cancel_exception_falls_back_to_default_cancel(request_context, event_queue):
    async def broken_on_cancel(context, eq):
        raise RuntimeError("on_cancel itself is buggy")

    executor = AgentExecutor(handler=lambda ctx, eq: None, on_cancel=broken_on_cancel)
    await executor.cancel(request_context, event_queue)  # must not raise

    assert _status_states(event_queue) == [TaskState.TASK_STATE_CANCELED]


async def test_message_id_generator_is_used_by_the_exception_safety_net(request_context, event_queue):
    class FixedMessageGenerator:
        def generate(self, context):
            return "fixed-message-id"

    async def handler(context, eq):
        raise ValueError("boom")

    executor = AgentExecutor(handler=handler, message_id_generator=FixedMessageGenerator())
    await executor.execute(request_context, event_queue)

    failed_event = next(e for e in event_queue.events if e.__class__.__name__ == "TaskStatusUpdateEvent")
    assert failed_event.status.message.message_id == "fixed-message-id"
