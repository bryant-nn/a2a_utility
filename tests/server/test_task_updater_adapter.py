from __future__ import annotations

import pytest
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from a2a_utility.schema import ExtendedMessage, ExtendedPart, ExtendedTaskState, MessageRole
from a2a_utility.server import ExtendedEventQueue, ExtendedTaskUpdater
from a2a_utility.server.adapters.outbound.task_updater_adapter import _require_part


def _states(event_queue) -> list[TaskState.ValueType]:
    return [
        e.status.state
        for e in event_queue.events
        if e.__class__.__name__ == "TaskStatusUpdateEvent"
    ]


def _status_events(event_queue):
    return [e for e in event_queue.events if e.__class__.__name__ == "TaskStatusUpdateEvent"]


def _artifact_events(event_queue):
    return [e for e in event_queue.events if e.__class__.__name__ == "TaskArtifactUpdateEvent"]


def test_is_a_real_native_task_updater_subclass(extended_request_context, extended_event_queue):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    assert isinstance(tu, TaskUpdater)


async def test_start_work_enqueues_task_then_working_status(
    extended_request_context, extended_event_queue, event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await tu.start_work()
    kinds = [e.__class__.__name__ for e in event_queue.events]
    assert kinds == ["Task", "TaskStatusUpdateEvent"]
    assert _states(event_queue) == [TaskState.TASK_STATE_WORKING]


async def test_task_is_only_enqueued_once_across_multiple_calls(
    extended_request_context, extended_event_queue, event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await tu.start_work()
    await tu.complete()
    task_events = [e for e in event_queue.events if e.__class__.__name__ == "Task"]
    assert len(task_events) == 1


@pytest.mark.parametrize(
    "method_name,expected_state",
    [
        ("complete", TaskState.TASK_STATE_COMPLETED),
        ("failed", TaskState.TASK_STATE_FAILED),
        ("reject", TaskState.TASK_STATE_REJECTED),
        ("cancel", TaskState.TASK_STATE_CANCELED),
        ("requires_input", TaskState.TASK_STATE_INPUT_REQUIRED),
        ("requires_auth", TaskState.TASK_STATE_AUTH_REQUIRED),
        ("submit", TaskState.TASK_STATE_SUBMITTED),
        ("start_work", TaskState.TASK_STATE_WORKING),
    ],
)
async def test_every_status_shorthand_maps_to_its_state_and_enqueues_the_task_first(
    extended_request_context, extended_event_queue, event_queue, method_name, expected_state
):
    """The eight shorthands are explicit overrides (so their `message=` can
    take a2a_utility types rather than a native protobuf Message). This pins
    each one to the state it claims, and to the lazy Task-enqueue that
    update_status() injects."""
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await getattr(tu, method_name)()
    assert _states(event_queue) == [expected_state]
    assert event_queue.events[0].__class__.__name__ == "Task"


async def test_update_status_takes_the_a2a_utility_task_state_enum(
    extended_request_context, extended_event_queue, event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await tu.update_status(ExtendedTaskState.WORKING)
    assert _states(event_queue) == [TaskState.TASK_STATE_WORKING]


async def test_terminal_state_guard_is_inherited_from_native(
    extended_request_context, extended_event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await tu.complete()
    with pytest.raises(RuntimeError, match="terminal"):
        await tu.complete()


# --- message= coercion: the point of overriding the shorthands ------------- #
@pytest.mark.parametrize(
    "message",
    [
        "done",
        ExtendedPart.from_text("done"),
        [ExtendedPart.from_text("done")],
    ],
    ids=["str", "part", "list_of_parts"],
)
async def test_message_accepts_plain_python_shapes_without_building_a_message(
    extended_request_context, extended_event_queue, event_queue, message
):
    """A handler attaching a note to a status shouldn't have to construct a
    message object, let alone a native protobuf one."""
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await tu.complete(message)
    status = _status_events(event_queue)[0].status
    assert status.HasField("message")
    assert status.message.parts[0].text == "done"


async def test_message_accepts_an_extended_message_and_fills_in_its_ids(
    extended_request_context, extended_event_queue, event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await tu.complete(ExtendedMessage(parts=[ExtendedPart.from_text("done")]))
    message = _status_events(event_queue)[0].status.message
    assert message.task_id == tu.task_id
    assert message.context_id == tu.context_id
    assert message.message_id  # generated, not left empty


async def test_message_rejects_a_native_protobuf_message(
    extended_request_context, extended_event_queue
):
    """Passing a native Message used to be the *only* way to do this. It is
    now a type error, so the old idiom fails loudly instead of half-working."""
    from a2a.types import Message as ProtoMessage

    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    with pytest.raises(TypeError, match="ExtendedMessage"):
        await tu.complete(ProtoMessage())


async def test_new_agent_message_returns_a_typed_message(
    extended_request_context, extended_event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    message = tu.new_agent_message([ExtendedPart.from_text("done")])
    assert isinstance(message, ExtendedMessage)
    assert message.role is MessageRole.AGENT
    assert message.task_id == tu.task_id
    assert message.text() == "done"


async def test_complete_accepts_a_message_built_by_new_agent_message(
    extended_request_context, extended_event_queue, event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await tu.complete(tu.new_agent_message([ExtendedPart.from_text("done")]))
    assert _status_events(event_queue)[0].status.HasField("message")


# --- artifacts ------------------------------------------------------------- #
async def test_add_artifact_passes_through_native_kwargs(
    extended_request_context, extended_event_queue, event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    await tu.add_artifact(
        [ExtendedPart.from_text("chunk 1")],
        artifact_id="fixed-id",
        name="answer",
        append=False,
        last_chunk=False,
    )
    events = _artifact_events(event_queue)
    assert len(events) == 1
    assert events[0].artifact.artifact_id == "fixed-id"
    assert events[0].artifact.name == "answer"
    assert events[0].append is False
    assert events[0].last_chunk is False


def test_add_artifact_keeps_parts_positional_like_native():
    """Native's signature is add_artifact(parts, artifact_id=None, ...). An
    override that made `parts` keyword-only would narrow the contract."""
    import inspect

    params = list(inspect.signature(ExtendedTaskUpdater.add_artifact).parameters)
    assert params[1] == "parts"


async def test_add_artifact_rejects_non_extended_part(
    extended_request_context, extended_event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    with pytest.raises(TypeError):
        await tu.add_artifact(["not a part"])  # type: ignore[list-item]


def test_require_part_rejects_wrong_type():
    with pytest.raises(TypeError):
        _require_part({"text": "not an ExtendedPart"})


async def test_as_part_emitter_streams_a_working_status(
    extended_request_context, extended_event_queue, event_queue
):
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    emit = tu.as_part_emitter()
    await emit(ExtendedPart.thinking("still working..."))
    assert _states(event_queue) == [TaskState.TASK_STATE_WORKING]
    assert _status_events(event_queue)[0].status.HasField("message")


async def test_custom_artifact_id_generator_is_used(
    extended_request_context, extended_event_queue, event_queue
):
    class FixedGenerator:
        def generate(self, context):
            return "custom-artifact-id"

    tu = ExtendedTaskUpdater(
        extended_request_context, extended_event_queue, artifact_id_generator=FixedGenerator()
    )
    await tu.add_artifact([ExtendedPart.from_text("x")])
    assert _artifact_events(event_queue)[0].artifact.artifact_id == "custom-artifact-id"


async def test_custom_message_id_generator_is_used(
    extended_request_context, extended_event_queue
):
    class FixedGenerator:
        def generate(self, context):
            return "custom-message-id"

    tu = ExtendedTaskUpdater(
        extended_request_context, extended_event_queue, message_id_generator=FixedGenerator()
    )
    assert tu.new_agent_message([ExtendedPart.from_text("x")]).message_id == "custom-message-id"


async def test_event_queue_attr_is_the_typed_instance_we_were_given(
    extended_request_context, extended_event_queue
):
    """Native's __init__ stores the raw queue; we overwrite it with the typed
    one so the task-id validation applies to everything this updater sends."""
    tu = ExtendedTaskUpdater(extended_request_context, extended_event_queue)
    assert tu.event_queue is extended_event_queue
    assert isinstance(tu.event_queue, ExtendedEventQueue)
