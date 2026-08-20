"""End-to-end harness: a real a2a_utility server driven by the real
a2a_utility client, over ASGI, in-process.

Everything under `tests/server/` and `tests/schema/` is unit-level — the
adapters are exercised against a duck-typed `FakeEventQueue` that models only
the producer side. That deliberately says nothing about how the framework
reacts to what we enqueue, which is where the interesting failures live:
`DefaultRequestHandlerV2` requires a `Task` event before any status event,
drains through `aclose()` on shutdown, and routes message-mode replies down a
different branch of the `StreamResponse` oneof than task-mode ones.

These tests run the whole stack instead: `create_app()` → Starlette →
`httpx.ASGITransport` → the native JSON-RPC client the a2a_utility client
wraps. No sockets, no ports, no subprocesses — but every layer is the real
one, including lifespan startup/shutdown.
"""

from __future__ import annotations

from typing import Callable, Optional

import pytest
from starlette.applications import Starlette

from a2a_utility.server import ExtendedAgentCard, create_app

from harness import build_card


@pytest.fixture
def make_app() -> Callable[..., Starlette]:
    """Builds an app around a DomainAgentExecutorPort instance, with the card
    and rpc_url the rest of the harness assumes."""

    def _make(
        executor=None, *, card: Optional[ExtendedAgentCard] = None, **kwargs
    ) -> Starlette:
        return create_app(
            agent_card=card or build_card(),
            executor=executor,
            rpc_url="/",
            **kwargs,
        )

    return _make
