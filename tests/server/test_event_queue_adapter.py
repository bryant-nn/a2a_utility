from __future__ import annotations

import pytest
from a2a.helpers import new_text_part
from a2a.types import Message, Role, Task, TaskStatus, TaskState

from a2a_utility.schema import ExtendedMessage, ExtendedPart, MessageRole
from a2a_utility.server import ExtendedEventQueue


class _TrackingQueue:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, event: object) -> None:
        self.events.append(event)


def _message(task_id: str = "task-1") -> Message:
    return Message(message_id="m1", role=Role.ROLE_USER, task_id=task_id, parts=[new_text_part("hi")])


async def test_enqueue_event_accepts_the_full_native_event_union_when_ids_match():
    native = _TrackingQueue()
    eq = ExtendedEventQueue(native, expected_task_id="task-1")
    await eq.enqueue_event(_message("task-1"))
    await eq.enqueue_event(
        Task(id="task-1", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
    )
    assert len(native.events) == 2


async def test_enqueue_event_raises_on_task_id_mismatch_instead_of_letting_native_crash_uncatchably():
    native = _TrackingQueue()
    eq = ExtendedEventQueue(native, expected_task_id="task-1")
    with pytest.raises(ValueError, match="task_id"):
        await eq.enqueue_event(_message("wrong-task-id"))
    assert native.events == []  # never reached the native queue


async def test_task_event_is_validated_on_its_id_field_not_task_id():
    """A Task names itself with `id`; the other three event types use
    `task_id`. Both have to route into the same check."""
    native = _TrackingQueue()
    eq = ExtendedEventQueue(native, expected_task_id="task-1")
    with pytest.raises(ValueError, match="task_id"):
        await eq.enqueue_event(
            Task(id="other-task", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
        )
    assert native.events == []


async def test_enqueue_event_skips_validation_when_no_expected_task_id_given():
    native = _TrackingQueue()
    eq = ExtendedEventQueue(native)  # expected_task_id=None
    await eq.enqueue_event(_message("anything"))
    assert len(native.events) == 1


async def test_standalone_message_without_a_task_id_is_allowed_through():
    """Native's message-mode workflow publishes a Message with no task_id
    (proto3 default ""). Validating that against this request's task id would
    reject the exact workflow enqueue_event exists to make reachable."""
    native = _TrackingQueue()
    eq = ExtendedEventQueue(native, expected_task_id="task-1")
    await eq.enqueue_event(Message(message_id="m1", role=Role.ROLE_AGENT, parts=[new_text_part("hi")]))
    assert len(native.events) == 1


async def test_enqueue_message_converts_and_publishes_without_a_task_id():
    """The typed, no-a2a.* entry point for message-mode — the only public way
    to reach it before enqueue_message existed was
    enqueue_event(ExtendedMessage(...).to_protobuf()), which handed a native
    protobuf object to the handler."""
    native = _TrackingQueue()
    eq = ExtendedEventQueue(native, expected_task_id="task-1")
    reply = ExtendedMessage(role=MessageRole.AGENT, parts=[ExtendedPart.from_text("hi")])

    await eq.enqueue_message(reply)

    assert len(native.events) == 1
    published = native.events[0]
    assert isinstance(published, Message)  # converted to native at the boundary
    assert published.parts[0].text == "hi"


async def test_enqueue_message_still_validates_task_id_when_one_is_set():
    native = _TrackingQueue()
    eq = ExtendedEventQueue(native, expected_task_id="task-1")
    reply = ExtendedMessage(
        role=MessageRole.AGENT, parts=[ExtendedPart.from_text("hi")], task_id="wrong-task-id"
    )

    with pytest.raises(ValueError, match="task_id"):
        await eq.enqueue_message(reply)
    assert native.events == []


def test_consumer_side_methods_are_not_exposed_to_domain_agents():
    """Native's EventQueue ABC is explicit that only `enqueue_event` is the
    producer-side surface; dequeue/tap/close/task_done live on the concrete
    implementations and are framework-managed. A handler calling close()
    would tear down the stream the framework is still consuming."""
    eq = ExtendedEventQueue(_TrackingQueue(), expected_task_id="task-1")
    for framework_managed in ("dequeue_event", "tap", "close", "task_done", "is_closed"):
        assert not hasattr(eq, framework_managed), (
            f"{framework_managed} must not be reachable from a domain agent"
        )
