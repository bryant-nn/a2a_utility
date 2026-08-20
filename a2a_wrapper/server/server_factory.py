from __future__ import annotations

import contextlib

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from a2a_wrapper.server.base_executor import BaseA2AWrapperExecutor
from a2a_wrapper.server.card import ExtendedAgentCard
from a2a_wrapper.server.ports import DomainAgentExecutorPort


def create_a2a_server(
    agent_card: ExtendedAgentCard,
    domain_executor: DomainAgentExecutorPort,
    rpc_url: str = '/',
) -> Starlette:
    """Build a ready-to-run A2A Starlette server.

    Args:
        agent_card: the ExtendedAgentCard to serve at the well-known path.
        domain_executor: the agent developer's DomainAgentExecutorPort.
        rpc_url: path to mount the JSON-RPC endpoint at.

    Returns:
        A Starlette app with the agent-card + JSON-RPC routes and a
        lifespan that drains the request handler on shutdown.
    """
    proto_card = agent_card.to_agent_card()
    wrapper = BaseA2AWrapperExecutor(domain_executor)

    handler = DefaultRequestHandler(
        agent_executor=wrapper,
        task_store=InMemoryTaskStore(),
        agent_card=proto_card,
    )

    routes = [
        *create_agent_card_routes(proto_card),
        *create_jsonrpc_routes(handler, rpc_url),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        # DefaultRequestHandlerV2.aclose() must run on shutdown or it
        # leaves pending asyncio tasks behind.
        try:
            yield
        finally:
            await handler.aclose()

    return Starlette(routes=routes, lifespan=lifespan)
