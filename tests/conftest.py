"""Shared test fixtures.

`FakeEventQueue` is a minimal duck-typed stand-in for native
`a2a.server.events.EventQueue`, which is an ABC whose only method is
`enqueue_event()` — so that's all this needs, collecting enqueued events for
assertions.

These are unit-level fixtures: they exercise our adapters in isolation and
deliberately do not model the framework's consumer side. Anything that
depends on how the real framework reacts to what we enqueue — the ordering
rule that a `Task` must precede any status event, shutdown draining,
message-mode round trips — belongs in `tests/e2e/`, which drives a real
Starlette app over ASGI.
"""

from __future__ import annotations

import pytest
from a2a.helpers import new_text_part
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Role, SendMessageRequest

from a2a_utility.server import ExtendedRequestContext


class FakeEventQueue:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, event: object) -> None:
        self.events.append(event)


def make_request_context(text: str = "hello") -> RequestContext:
    request = SendMessageRequest(
        message=Message(message_id="m1", role=Role.ROLE_USER, parts=[new_text_part(text)])
    )
    return RequestContext(call_context=ServerCallContext(), request=request)


@pytest.fixture
def event_queue() -> FakeEventQueue:
    return FakeEventQueue()


@pytest.fixture
def request_context() -> RequestContext:
    """The native context, as the framework hands it to AgentExecutor."""
    return make_request_context()


@pytest.fixture
def extended_request_context(request_context: RequestContext) -> ExtendedRequestContext:
    """The typed context, as a DomainAgentExecutorPort.execute() receives it."""
    return ExtendedRequestContext(request_context)
