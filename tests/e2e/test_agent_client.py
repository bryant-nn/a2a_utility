"""ExtendedAgentClient — reuse, and the task-management calls the one-shot
functions don't expose."""

from __future__ import annotations

import pytest

from a2a_utility.client import A2ACallError, ExtendedAgentClient
from a2a_utility.schema import ExtendedPart, ExtendedTaskState
from a2a_utility.server import DomainAgentExecutorPort, Progress, PublishArtifact

from harness import BASE_URL, running_app


class Echo(DomainAgentExecutorPort):
    async def execute(self, context):
        yield PublishArtifact(parts=[ExtendedPart.from_text(f"echo: {context.get_user_input()}")])


async def test_one_client_serves_many_calls(make_app):
    """The reason this class exists: the one-shot functions re-open the
    connection and re-fetch the agent card every time."""
    async with running_app(make_app(Echo())) as http:
        async with ExtendedAgentClient(BASE_URL, http_client=http) as agent:
            first = await agent.send("one")
            second = await agent.send("two")
            third = await agent.send("three")

    assert [first, second, third] == ["echo: one", "echo: two", "echo: three"]


async def test_the_agent_card_is_resolved_once_not_per_call(make_app):
    """Counted as real HTTP requests, because it's the extra round trip per
    call that motivates holding the client open at all."""
    card_fetches = 0

    async def count_card_fetches(request) -> None:
        nonlocal card_fetches
        if ".well-known" in request.url.path:
            card_fetches += 1

    async with running_app(make_app(Echo())) as http:
        http.event_hooks["request"] = [count_card_fetches]
        async with ExtendedAgentClient(BASE_URL, http_client=http) as agent:
            await agent.send("one")
            await agent.send("two")
            await agent.send("three")

    assert card_fetches == 1


async def test_get_task_reads_back_a_finished_task(make_app):
    async with running_app(make_app(Echo())) as http:
        async with ExtendedAgentClient(BASE_URL, http_client=http) as agent:
            result = await agent.send_result("hi")
            task = await agent.get_task(result.task_id)

    assert task.id == result.task_id
    assert task.state is ExtendedTaskState.COMPLETED
    assert task.text() == "echo: hi"


async def test_send_parts_returns_typed_parts(make_app):
    class Mixed(DomainAgentExecutorPort):
        async def execute(self, context):
            yield PublishArtifact(
                parts=[
                    ExtendedPart.thinking("considering"),
                    ExtendedPart.from_text("answer"),
                ]
            )

    async with running_app(make_app(Mixed())) as http:
        async with ExtendedAgentClient(BASE_URL, http_client=http) as agent:
            parts = await agent.send_parts("hi")

    assert [p.text for p in parts if p.text] == ["answer"]
    assert any(p.data and p.data.data_type == "thinking_response" for p in parts)


async def test_streaming_parts_reach_the_emit_callback(make_app):
    class Streaming(DomainAgentExecutorPort):
        async def execute(self, context):
            yield Progress(ExtendedPart.thinking("step one"))
            yield Progress(ExtendedPart.thinking("step two"))
            yield PublishArtifact(parts=[ExtendedPart.from_text("done")])

    seen: list[str] = []

    async def collect(part: ExtendedPart) -> None:
        if part.data:
            seen.append(part.data.data_content.text)

    async with running_app(make_app(Streaming())) as http:
        async with ExtendedAgentClient(BASE_URL, http_client=http) as agent:
            answer = await agent.send("hi", emit=collect)

    assert seen == ["step one", "step two"]
    assert answer == "done"


async def test_a_failing_agent_raises_with_the_state_that_caused_it(make_app):
    class Broken(DomainAgentExecutorPort):
        async def execute(self, context):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover

    async with running_app(make_app(Broken())) as http:
        async with ExtendedAgentClient(BASE_URL, http_client=http) as agent:
            with pytest.raises(A2ACallError) as exc:
                await agent.send("hi")

    assert exc.value.status is ExtendedTaskState.FAILED
    assert "kaboom" in exc.value.detail


async def test_a_caller_supplied_http_client_survives_the_agent_client_closing(make_app):
    """Ownership rule: closing an ExtendedAgentClient must not close an httpx
    client it was handed, or the caller's next call breaks."""
    async with running_app(make_app(Echo())) as http:
        async with ExtendedAgentClient(BASE_URL, http_client=http) as agent:
            await agent.send("one")

        # Same httpx client, brand new agent client — works only if the first
        # close left it open.
        async with ExtendedAgentClient(BASE_URL, http_client=http) as agent:
            assert await agent.send("two") == "echo: two"
