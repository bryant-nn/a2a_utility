"""End-to-end: a2a_utility client -> real Starlette app -> a2a_utility server.

Every assertion here depends on how the *framework* reacts to what our
adapters enqueue, which is exactly what the unit tests cannot see.
"""

from __future__ import annotations

import pytest

from a2a_utility.client import A2ACallError, call_agent, call_agent_result
from a2a_utility.schema import ExtendedPart, ExtendedTaskState
from a2a_utility.server import (
    AuthRequired,
    DomainAgentExecutorPort,
    InputRequired,
    MessageReply,
    Progress,
    PublishArtifact,
    Rejected,
)

from harness import BASE_URL, running_app


async def test_normal_task_lifecycle_round_trips_every_part_type(make_app):
    """The happy path, through the real JSON-RPC + SSE stack: an executor
    yields a Progress event, publishes a mixed artifact, and returns
    (marking the task COMPLETED)."""

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            yield Progress(ExtendedPart.thinking("looking it up"))
            yield PublishArtifact(
                parts=[
                    ExtendedPart.source_reference([{"source": "test"}]),
                    ExtendedPart.from_text(f"echo: {context.get_user_input()}"),
                ]
            )

    streamed: list[ExtendedPart] = []

    async def collect(part: ExtendedPart) -> None:
        streamed.append(part)

    async with running_app(make_app(Executor())) as http:
        result = await call_agent_result(BASE_URL, "hello", emit=collect, http_client=http)

    assert result.status == ExtendedTaskState.COMPLETED
    assert result.text() == "echo: hello"
    assert any(p.data and p.data.data_type == "source_reference_response" for p in result.parts())
    assert [p.data.data_content.text for p in streamed if p.data] == ["looking it up"]


async def test_task_is_enqueued_before_the_first_event(make_app):
    """The framework raises InvalidAgentResponseError if a status/artifact
    event arrives before the Task exists. AgentExecutor's lazy `ensure_task()`
    is what prevents that for every TaskEvent branch, and this is the only
    test that can prove it against the real EventConsumer — not just that we
    enqueue a Task first, but that the framework accepts what we send."""

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            # A single artifact, nothing else — no separate "start work"
            # concept exists anymore, so this is the minimal event stream.
            yield PublishArtifact(parts=[ExtendedPart.from_text("done")])

    async with running_app(make_app(Executor())) as http:
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.status == ExtendedTaskState.COMPLETED


async def test_handler_exception_surfaces_as_a_failed_task_with_the_reason(make_app):
    """Native marks the task FAILED by itself but sends no message.
    AgentExecutor's safety net is what carries the reason back to the caller,
    which is the whole justification for keeping it."""

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            raise ValueError("something specific broke")
            yield  # pragma: no cover — makes this an async generator

    async with running_app(make_app(Executor())) as http:
        with pytest.raises(A2ACallError, match="something specific broke"):
            await call_agent(BASE_URL, "hi", http_client=http)


async def test_message_mode_reply_reaches_the_caller(make_app):
    """The agent answers with a standalone Message and never creates a Task.
    Yielding MessageReply first (and only) is what keeps the Task from ever
    being built — a domain agent never touches enqueue_event or any native
    a2a.types object to make this happen."""

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            yield MessageReply("immediate answer")

    async with running_app(make_app(Executor())) as http:
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.is_message_mode
    assert result.text() == "immediate answer"
    assert not result.task_id


async def test_input_required_resume_continues_the_same_task(make_app):
    """A paused task only resumes if the caller resends the same task_id —
    this is the client-side half of `context.is_resuming` ever being True.
    Exercises `call_agent_result(..., task_id=...)` end to end: first call
    pauses with INPUT_REQUIRED, second call (same task_id) completes it."""

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            if not context.is_resuming:
                yield InputRequired("what city?")
                return
            city = context.get_user_input()
            yield PublishArtifact(parts=[ExtendedPart.from_text(f"weather in {city}: sunny")])

    async with running_app(make_app(Executor())) as http:
        first = await call_agent_result(BASE_URL, "book a flight", http_client=http)
        assert first.status == ExtendedTaskState.INPUT_REQUIRED
        assert first.task_id

        second = await call_agent_result(
            BASE_URL, "boston", http_client=http, task_id=first.task_id
        )

    assert second.status == ExtendedTaskState.COMPLETED
    assert second.task_id == first.task_id
    assert second.text() == "weather in boston: sunny"


async def test_omitting_task_id_starts_a_fresh_task_even_after_a_pause(make_app):
    """Without task_id=, every call is a brand new task to the server — the
    absence of the resume mechanism, not just its presence, is worth
    pinning down."""

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            if not context.is_resuming:
                yield InputRequired("what city?")
                return
            yield PublishArtifact(parts=[ExtendedPart.from_text("should not reach here")])

    async with running_app(make_app(Executor())) as http:
        first = await call_agent_result(BASE_URL, "book a flight", http_client=http)
        second = await call_agent_result(BASE_URL, "boston", http_client=http)

    assert first.status == ExtendedTaskState.INPUT_REQUIRED
    assert second.status == ExtendedTaskState.INPUT_REQUIRED
    assert second.task_id != first.task_id


async def test_auth_required_pauses_the_task(make_app):
    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            yield AuthRequired("please authenticate")

    async with running_app(make_app(Executor())) as http:
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.status == ExtendedTaskState.AUTH_REQUIRED


async def test_rejected_ends_the_task(make_app):
    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            yield Rejected("not allowed")
            yield PublishArtifact(parts=[ExtendedPart.from_text("unreachable")])

    async with running_app(make_app(Executor())) as http:
        with pytest.raises(A2ACallError, match="not allowed"):
            await call_agent(BASE_URL, "hi", http_client=http)


class _NeverCalled(DomainAgentExecutorPort):
    async def execute(self, context):
        raise AssertionError("execute() should not run for a card-only request")
        yield  # pragma: no cover


async def test_agent_card_is_served_at_the_well_known_path(make_app):
    async with running_app(make_app(_NeverCalled())) as http:
        response = await http.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "test_agent"
    assert card["skills"][0]["id"] == "echo"


async def test_shutdown_drains_the_request_handler(make_app):
    """DefaultRequestHandlerV2.aclose() is documented as needing to be wired
    into the ASGI lifespan; without it the ActiveTaskRegistry leaves pending
    asyncio tasks behind. Exiting running_app() runs the real shutdown, so a
    missing aclose() shows up as a lingering task."""
    import asyncio

    class Executor(DomainAgentExecutorPort):
        async def execute(self, context):
            yield PublishArtifact(parts=[ExtendedPart.from_text("done")])

    before = len(asyncio.all_tasks())
    async with running_app(make_app(Executor())) as http:
        await call_agent(BASE_URL, "hi", http_client=http)

    # Give cancelled tasks a tick to actually finish unwinding.
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) <= before + 1
