"""Mounting an A2A agent onto an existing FastAPI app.

`create_app()` builds a Starlette app that *is* the whole service, which is
the right shape for a standalone agent. Plenty of internal services aren't
that shape: they already have a FastAPI app with health checks, metrics, and
their own REST routes, and the agent is one capability among several.

This adds the A2A endpoints to that app instead of replacing it, using the
SDK's own `add_a2a_routes_to_fastapi`.

    from fastapi import FastAPI
    from a2a_utility.server import ExtendedAgentCard, add_to_fastapi

    app = FastAPI()

    @app.get("/health")
    async def health(): return {"ok": True}

    add_to_fastapi(app, agent_card=card, handler=handle, gate_keeper=gate)

One thing that does not carry over automatically: `create_app` wires the
request handler's `aclose()` into its Starlette lifespan, and a FastAPI app
has its own. `add_to_fastapi` registers a shutdown hook for it, so the
ActiveTaskRegistry still drains — but if you replace the app's lifespan after
calling this, re-register it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from a2a.server.id_generator import IDGenerator
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.tasks import (
    InMemoryTaskStore,
    PushNotificationConfigStore,
    PushNotificationSender,
    TaskStore,
)

from .adapters.inbound.agent_executor import AgentExecutor
from .adapters.inbound.call_context_builder import A2AUtilityCallContextBuilder
from .app import _resolve_gate_keeper
from .application.ports.inbound.agent_handler_port import AgentHandlerPort
from .application.ports.inbound.gate_keeper_port import GateKeeperPort
from .application.ports.inbound.on_cancel_port import OnCancelPort
from .card import ExtendedAgentCard

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI

__all__ = ["add_to_fastapi"]


def add_to_fastapi(
    app: "FastAPI",
    *,
    agent_card: ExtendedAgentCard,
    handler: AgentHandlerPort,
    on_cancel: Optional[OnCancelPort] = None,
    gate_keeper: Optional[GateKeeperPort] = None,
    required_permission: Optional[str] = None,
    require_auth: bool = False,
    rpc_url: str = "/",
    include_rest_routes: bool = False,
    task_store: Optional[TaskStore] = None,
    push_config_store: Optional[PushNotificationConfigStore] = None,
    push_sender: Optional[PushNotificationSender] = None,
    context_builder: Optional[ServerCallContextBuilder] = None,
    message_id_generator: Optional[IDGenerator] = None,
) -> Any:
    """Add this agent's A2A routes to an existing FastAPI app.

    Most arguments mean what they do on `create_app`; see that docstring.
    Two differences:

      `include_rest_routes` additionally mounts A2A's REST binding alongside
      JSON-RPC, for callers that speak it. Off by default because JSON-RPC is
      what `a2a_utility.client` and most A2A clients use, and every mounted
      route is more surface to secure.

      Registry self-registration is absent entirely — a FastAPI service that
      also serves other things generally has its own service-discovery story,
      and a second heartbeat would fight with it.

    Returns the request handler, so a caller can reach it for anything this
    signature doesn't cover.
    """
    gate_keeper = _resolve_gate_keeper(gate_keeper, require_auth, agent_card.name)
    proto_card = agent_card.to_agent_card()

    request_handler = DefaultRequestHandler(
        agent_executor=AgentExecutor(
            handler=handler,
            on_cancel=on_cancel,
            gate_keeper=gate_keeper,
            required_permission=required_permission,
            message_id_generator=message_id_generator,
        ),
        task_store=task_store or InMemoryTaskStore(),
        agent_card=proto_card,
        push_config_store=push_config_store,
        push_sender=push_sender,
    )

    builder = context_builder or A2AUtilityCallContextBuilder()
    # Passing the route lists (rather than letting the SDK build them) is what
    # its signature asks for, and it re-registers them as FastAPI APIRoutes so
    # the A2A endpoints show up in the app's own /docs alongside its REST ones.
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(proto_card),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url, context_builder=builder),
        rest_routes=create_rest_routes(request_handler, context_builder=builder)
        if include_rest_routes
        else None,
    )

    # FastAPI has its own lifespan, so create_app's shutdown wiring doesn't
    # apply here. Without this the ActiveTaskRegistry leaves pending asyncio
    # tasks when the service stops.
    app.add_event_handler("shutdown", request_handler.aclose)

    return request_handler
