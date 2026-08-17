"""End-to-end: a2a_utility client -> real Starlette app -> a2a_utility server.

Every assertion here depends on how the *framework* reacts to what our
adapters enqueue, which is exactly what the unit tests cannot see.
"""

from __future__ import annotations

import pytest

from a2a_utility.client import A2ACallError, call_agent, call_agent_result
from a2a_utility.schema import ExtendedPart, ExtendedTaskState

from harness import BASE_URL, build_card, running_app


async def test_normal_task_lifecycle_round_trips_every_part_type(make_app):
    """The happy path, through the real JSON-RPC + SSE stack: a handler
    streams thinking, publishes a mixed artifact, and completes."""

    async def handler(context, event_queue):
        from a2a_utility.server import ExtendedTaskUpdater

        tu = ExtendedTaskUpdater(context, event_queue)
        await tu.start_work()
        await tu.as_part_emitter()(ExtendedPart.thinking("looking it up"))
        await tu.add_artifact(
            [
                ExtendedPart.source_reference([{"source": "test"}]),
                ExtendedPart.from_text(f"echo: {context.get_user_input()}"),
            ]
        )
        await tu.complete()

    streamed: list[ExtendedPart] = []

    async def collect(part: ExtendedPart) -> None:
        streamed.append(part)

    async with running_app(make_app(handler)) as http:
        result = await call_agent_result(BASE_URL, "hello", emit=collect, http_client=http)

    assert result.status == ExtendedTaskState.COMPLETED
    assert result.text() == "echo: hello"
    assert any(p.data and p.data.data_type == "source_reference_response" for p in result.parts())
    assert [p.data.data_content.text for p in streamed if p.data] == ["looking it up"]


async def test_task_is_enqueued_before_the_first_status_event(make_app):
    """The framework raises InvalidAgentResponseError if a status event
    arrives before the Task exists. ExtendedTaskUpdater._ensure_task() is
    what prevents that, and this is the only test that can prove it: the
    unit tests assert we enqueue a Task first, but only the real
    EventConsumer enforces that it matters."""

    async def handler(context, event_queue):
        from a2a_utility.server import ExtendedTaskUpdater

        # No explicit Task enqueue anywhere — straight to a status update.
        await ExtendedTaskUpdater(context, event_queue).complete("done")

    async with running_app(make_app(handler)) as http:
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.status == ExtendedTaskState.COMPLETED


async def test_handler_exception_surfaces_as_a_failed_task_with_the_reason(make_app):
    """Native marks the task FAILED by itself but sends no message.
    AgentExecutor's safety net is what carries the reason back to the caller,
    which is the whole justification for keeping it."""

    async def handler(context, event_queue):
        raise ValueError("something specific broke")

    async with running_app(make_app(handler)) as http:
        with pytest.raises(A2ACallError, match="something specific broke"):
            await call_agent(BASE_URL, "hi", http_client=http)


async def test_message_mode_reply_reaches_the_caller(make_app):
    """The agent answers with a standalone Message and never creates a Task.
    This path was silently dropped end to end: enqueue_event rejected the
    Message for having no task_id, and the client had no branch for
    StreamResponse.message even if it had gone out."""

    async def handler(context, event_queue):
        from a2a_utility.schema import ExtendedMessage, MessageRole

        reply = ExtendedMessage(
            role=MessageRole.AGENT,
            parts=[ExtendedPart.from_text("immediate answer")],
            message_id="reply-1",
        )
        # enqueue_message, not enqueue_event(reply.to_protobuf()) — the
        # handler never touches a native a2a.types object.
        await event_queue.enqueue_message(reply)

    async with running_app(make_app(handler)) as http:
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.is_message_mode
    assert result.text() == "immediate answer"
    assert not result.task_id


async def test_agent_card_is_served_at_the_well_known_path(make_app):
    async with running_app(make_app(lambda c, q: None)) as http:
        response = await http.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "test_agent"
    assert card["skills"][0]["id"] == "echo"


async def test_declared_auth_scheme_appears_on_the_served_card(make_app):
    """A native client's AuthInterceptor reads these off the card to decide
    what credential to attach — if they don't serialize, no client ever
    authenticates."""
    from a2a_utility.server import BearerAuth

    card = build_card()
    card.auth = BearerAuth()

    async with running_app(make_app(lambda c, q: None, card=card)) as http:
        served = (await http.get("/.well-known/agent-card.json")).json()

    assert "bearer" in served["securitySchemes"]
    assert list(served["securityRequirements"][0]["schemes"]) == ["bearer"]


async def test_shutdown_drains_the_request_handler(make_app):
    """DefaultRequestHandlerV2.aclose() is documented as needing to be wired
    into the ASGI lifespan; without it the ActiveTaskRegistry leaves pending
    asyncio tasks behind. Exiting running_app() runs the real shutdown, so a
    missing aclose() shows up as a lingering task."""
    import asyncio

    async def handler(context, event_queue):
        from a2a_utility.server import ExtendedTaskUpdater

        await ExtendedTaskUpdater(context, event_queue).complete("done")

    before = len(asyncio.all_tasks())
    async with running_app(make_app(handler)) as http:
        await call_agent(BASE_URL, "hi", http_client=http)

    # Give cancelled tasks a tick to actually finish unwinding.
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) <= before + 1
