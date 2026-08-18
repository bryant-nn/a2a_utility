"""Tripwire tests for the a2a-sdk surface a2a_utility is built on.

a2a_utility wraps the SDK closely enough that an SDK upgrade can silently
change behavior rather than break an import — 1.1.0 is the precedent: it
repointed `DefaultRequestHandler` at the new ActiveTask-based
`DefaultRequestHandlerV2` (renaming the old one `LegacyRequestHandler`) and
turned `EventQueue` into an ABC whose only public method is `enqueue_event`.
Neither change breaks `from a2a... import ...`; both change what our adapters
mean.

These tests assert the exact SDK properties our adapters rely on, so bumping
the `a2a-sdk` pin fails here first — with a pointer to what to re-read — and
not in production. Each assertion names the a2a_utility module that depends
on it.
"""

from __future__ import annotations

import inspect

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import Event, EventQueue
from a2a.server.request_handlers import (
    DefaultRequestHandler,
    DefaultRequestHandlerV2,
    LegacyRequestHandler,
)
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCard,
    Message,
    Part,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)


def test_default_request_handler_is_the_v2_active_task_implementation():
    """server/app.py builds `DefaultRequestHandler`. V2 differs from
    LegacyRequestHandler in ways our adapters depend on: it marks the task
    FAILED itself when execute() raises (ActiveTask._run_producer), it
    requires a Task event before any TaskStatusUpdateEvent
    (EventConsumer._handle_task_modification_event), and it exposes aclose()
    for shutdown. If this flips back, re-read those three."""
    assert DefaultRequestHandler is DefaultRequestHandlerV2
    assert DefaultRequestHandler is not LegacyRequestHandler


def test_v2_handler_exposes_aclose_for_lifespan_shutdown():
    """server/app.py's lifespan awaits this on shutdown to drain the
    ActiveTaskRegistry."""
    assert inspect.iscoroutinefunction(DefaultRequestHandlerV2.aclose)


def test_event_queue_is_an_abc_whose_only_abstract_method_is_enqueue_event():
    """adapters/outbound/event_queue_adapter.py deliberately exposes only
    `enqueue_event` to domain agents, mirroring this. dequeue/tap/close live
    on the concrete subclasses and are framework-managed — see the EventQueue
    docstring."""
    assert inspect.isabstract(EventQueue)
    assert EventQueue.__abstractmethods__ == frozenset({"enqueue_event"})


def test_event_union_still_covers_the_four_types_we_validate():
    """adapters/outbound/event_queue_adapter.py's task-id check branches on
    Task (which carries `id`) vs everything else (which carries `task_id`)."""
    assert set(Event.__args__) == {
        Message,
        Task,
        TaskStatusUpdateEvent,
        TaskArtifactUpdateEvent,
    }


def test_task_updater_still_has_every_method_we_override_or_inherit():
    """adapters/outbound/task_updater_adapter.py subclasses TaskUpdater and
    overrides these. A method disappearing here means our override silently
    stops being called."""
    expected = {
        "update_status",
        "add_artifact",
        "new_agent_message",
        "complete",
        "failed",
        "cancel",
        "reject",
        "submit",
        "start_work",
        "requires_input",
        "requires_auth",
    }
    assert expected <= set(dir(TaskUpdater))


def test_task_updater_add_artifact_takes_parts_positionally():
    """Our override must keep `parts` positional to stay LSP-compatible."""
    params = list(inspect.signature(TaskUpdater.add_artifact).parameters)
    assert params[1] == "parts"


def test_agent_executor_abc_signature_is_still_context_event_queue():
    """adapters/inbound/agent_executor.py subclasses this, and
    AgentHandlerPort mirrors the signature parameter for parameter."""
    for method in (AgentExecutor.execute, AgentExecutor.cancel):
        params = list(inspect.signature(method).parameters)
        assert params == ["self", "context", "event_queue"]


def test_request_context_still_exposes_what_ExtendedRequestContext_wraps():
    """application/dtos.py's ExtendedRequestContext delegates to these."""
    expected = {
        "get_user_input",
        "message",
        "current_task",
        "task_id",
        "context_id",
        "metadata",
        "call_context",
    }
    assert expected <= set(dir(RequestContext))


def test_stream_response_payload_oneof_has_all_four_branches():
    """client/agent_client.py dispatches on every branch of this oneof —
    a new branch appearing means the client is silently dropping events."""
    assert {f.name for f in StreamResponse.DESCRIPTOR.fields} == {
        "task",
        "message",
        "status_update",
        "artifact_update",
    }


def test_part_content_oneof_is_still_text_raw_url_data():
    """schema/parts.py's ExtendedPart mirrors this oneof and enforces it in a
    model_validator (protobuf itself silently keeps the last-assigned one)."""
    oneof = Part.DESCRIPTOR.oneofs_by_name["content"]
    assert {f.name for f in oneof.fields} == {"text", "raw", "url", "data"}


def test_part_still_has_the_out_of_oneof_fields_we_carry():
    assert {"metadata", "filename", "media_type"} <= {
        f.name for f in Part.DESCRIPTOR.fields
    }


def test_agent_card_has_the_security_fields_the_card_model_fills():
    """server/card.py fills these so native clients' AuthInterceptor can find
    a scheme to attach credentials for."""
    assert {"security_schemes", "security_requirements"} <= {
        f.name for f in AgentCard.DESCRIPTOR.fields
    }


def test_task_state_still_has_every_state_ExtendedTaskState_maps():
    """schema/task_state.py maps 1:1 onto these."""
    expected = {
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_AUTH_REQUIRED",
    }
    assert expected <= set(TaskState.keys())


def test_default_context_builder_still_puts_raw_headers_in_state():
    """adapters/inbound/call_context_builder.py relies on `state['headers']`
    being populated by the superclass — a custom context builder can read
    them from there."""
    source = inspect.getsource(DefaultServerCallContextBuilder.build)
    assert "state['headers']" in source or 'state["headers"]' in source
