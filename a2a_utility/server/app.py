"""Composition helpers: build_agent_card / create_app / serve / serve_as_a2a.

A domain agent hands create_app a plain AgentHandlerPort callable (a function,
or an object with __call__ — see application/ports/inbound/agent_handler_port.py)
together with an AgentCard, mode=ServerMode.AGENT (the default). create_app
builds adapters/inbound/agent_executor.py's AgentExecutor around that handler
and wires the native a2a request handler + routes. A domain agent never
imports AgentExecutor itself — only this module and application.dtos/
application.ports.inbound.agent_handler_port. serve_as_a2a is the one-call
convenience that also builds the card and runs uvicorn with registry
self-registration (heartbeat).

Passing mode=ServerMode.DISCOVERY instead builds a zero-LLM registry node in
the same call: no `handler` is needed (or used, if given) — create_app wires
an InMemoryRegistryAdapter + RegisterAgentCardUseCase/SearchAgentUseCase +
DiscoveryAgentExecutor internally and mounts the plain-REST `/register` +
`/agents` endpoints alongside the real A2A JSON-RPC discovery route. Both
modes go through this single composition root instead of being split across
this file (AGENT) and main.py (DISCOVERY, historically).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

import httpx
import uvicorn
from a2a.server.id_generator import IDGenerator
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
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
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from .adapters.inbound.agent_executor import AgentExecutor
from .adapters.inbound.call_context_builder import A2AUtilityCallContextBuilder
from .adapters.inbound.discovery_agent_executor import DiscoveryAgentExecutor
from .adapters.outbound.in_memory_registry_adapter import InMemoryRegistryAdapter
from .application.ports.inbound.agent_handler_port import AgentHandlerPort
from .application.ports.inbound.on_cancel_port import OnCancelPort
from .application.ports.outbound.registry_port import AgentRegistryPort
from .application.use_cases.register_agent_card_use_case import RegisterAgentCardUseCase
from .application.use_cases.search_agent_use_case import SearchAgentUseCase
from .card import ExtendedAgentCard
from .config import ServerMode
from .domain.models.agent_card import AgentDescriptor

__all__ = [
    "create_app",
    "serve",
    "serve_as_a2a",
    "REGISTRY_HEARTBEAT_SECONDS",
    "REGISTRY_TTL_SECONDS",
]

logger = logging.getLogger(__name__)

REGISTRY_HEARTBEAT_SECONDS = 5.0
REGISTRY_TTL_SECONDS = 15.0


# --------------------------------------------------------------------------- #
# Registry self-registration (heartbeat)                                       #
# --------------------------------------------------------------------------- #
async def _register_once(client: httpx.AsyncClient, registry_url: str, payload: dict) -> None:
    try:
        resp = await client.post(f"{registry_url}/register", json=payload, timeout=3.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("registry heartbeat to %s failed: %s", registry_url, e)


async def _heartbeat_loop(registry_url: str, payload: dict) -> None:
    async with httpx.AsyncClient() as client:
        while True:
            await _register_once(client, registry_url, payload)
            await asyncio.sleep(REGISTRY_HEARTBEAT_SECONDS)


def _make_lifespan(
    request_handler: DefaultRequestHandler,
    registry_url: Optional[str] = None,
    payload: Optional[dict] = None,
):
    """Startup/shutdown for both modes.

    Two responsibilities:

      - The optional registry heartbeat (AGENT mode with `registry_url`).
      - Draining the request handler on shutdown. `DefaultRequestHandler` is
        `DefaultRequestHandlerV2`, whose `aclose()` docstring states it is
        "intended to be wired into an ASGI `lifespan` / `on_shutdown` hook" —
        without it, its `ActiveTaskRegistry` leaves pending asyncio tasks
        behind when the server stops. This applies to DISCOVERY mode too,
        which is why that mode gets a lifespan now rather than a bare
        `Starlette(routes=...)`.
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        task = None
        if registry_url:
            task = asyncio.create_task(_heartbeat_loop(registry_url, payload or {}))
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await request_handler.aclose()

    return lifespan


# --------------------------------------------------------------------------- #
# App composition (card-building models live in card.py)                       #
# --------------------------------------------------------------------------- #
def _create_discovery_app(
    *,
    agent_card: ExtendedAgentCard,
    rpc_url: str = "/",
    registry: Optional[AgentRegistryPort] = None,
    context_builder: Optional[ServerCallContextBuilder] = None,
) -> Starlette:
    """DISCOVERY mode: registry-backed discovery, zero LLM/executor.

    Mounts the plain-REST /register + /agents contract (structured registration
    data doesn't fit A2A's Task/Message shape) alongside the real A2A JSON-RPC
    "who can do X?" search route, backed by DiscoveryAgentExecutor.
    """
    proto_card = agent_card.to_agent_card()
    registry = registry or InMemoryRegistryAdapter(ttl_seconds=REGISTRY_TTL_SECONDS)
    register_use_case = RegisterAgentCardUseCase(registry)
    search_use_case = SearchAgentUseCase(registry)

    async def register(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            descriptor = AgentDescriptor(
                name=body["name"],
                description=body["description"],
                agent_card_url=body["agent_card_url"],
            )
        except (KeyError, TypeError):
            return JSONResponse(
                {"error": "expected JSON with name, description, agent_card_url"},
                status_code=400,
            )
        await register_use_case.register(descriptor)
        return JSONResponse({"ok": True})

    async def list_agents(request: Request) -> JSONResponse:
        agents = await search_use_case.list_all()
        return JSONResponse({"agents": [a.to_dict() for a in agents]})

    discovery_executor = DiscoveryAgentExecutor(search_use_case)
    discovery_request_handler = DefaultRequestHandler(
        agent_executor=discovery_executor,
        task_store=InMemoryTaskStore(),
        agent_card=proto_card,
    )
    context_builder = context_builder or A2AUtilityCallContextBuilder()

    routes: list[Route] = [
        Route("/register", register, methods=["POST"]),
        Route("/agents", list_agents, methods=["GET"]),
        *create_jsonrpc_routes(discovery_request_handler, rpc_url, context_builder=context_builder),
    ]
    if rpc_url != "/a2a/v1/discovery":
        routes.extend(
            create_jsonrpc_routes(
                discovery_request_handler, "/a2a/v1/discovery", context_builder=context_builder
            )
        )
    routes.extend(create_agent_card_routes(proto_card))

    return Starlette(routes=routes, lifespan=_make_lifespan(discovery_request_handler))


def create_app(
    *,
    mode: ServerMode = ServerMode.AGENT,
    agent_card: ExtendedAgentCard,
    handler: Optional[AgentHandlerPort] = None,
    on_cancel: Optional[OnCancelPort] = None,
    registry_url: Optional[str] = None,
    registration_payload: Optional[dict] = None,
    rpc_url: str = "/",
    registry: Optional[AgentRegistryPort] = None,
    context_builder: Optional[ServerCallContextBuilder] = None,
    message_id_generator: Optional[IDGenerator] = None,
    task_store: Optional[TaskStore] = None,
    push_config_store: Optional[PushNotificationConfigStore] = None,
    push_sender: Optional[PushNotificationSender] = None,
    middleware: Optional[list[Middleware]] = None,
    extra_routes: Optional[list[BaseRoute]] = None,
    include_rest_routes: bool = False,
) -> Starlette:
    """Wire an A2A Starlette app, in either mode.

    Takes an `ExtendedAgentCard` and converts it to the native protobuf card
    internally, so a caller never handles `a2a.types.AgentCard` directly.

    mode=AGENT (default): wraps a domain agent's `handler` (required — a plain
    AgentHandlerPort callable, not an AgentExecutor) behind the native a2a
    request handler.

    Other injectables:
      `on_cancel` reacts to an externally requested cancellation; most agents
      don't need it (the default marks the task CANCELED).
      `task_store` defaults to `InMemoryTaskStore`, which loses every task on
      restart and can't be shared across replicas — pass a durable store for
      anything beyond a single-process deployment.
      `push_config_store`/`push_sender` enable push notifications; both are
      needed for them to work.
      `middleware` and `extra_routes` are merged into the Starlette app, for
      health checks, metrics, or any other cross-cutting concern.
      `include_rest_routes` also mounts A2A's REST binding beside JSON-RPC.
      Off by default: JSON-RPC is what `a2a_utility.client` and most A2A
      clients speak, and every mounted route is more surface to secure.
      `message_id_generator` is used only by `AgentExecutor`'s own exception-
      safety-net path (the throwaway `ExtendedTaskUpdater` it builds to send
      a FAILED status if `handler` raises) — a handler builds its own updater
      and can pass whatever generators it likes there, per-instance.

    mode=DISCOVERY: `handler`/`on_cancel`/`message_id_generator` are not used
    (a registry node has no LLM/domain logic of its own) — see
    _create_discovery_app for what gets wired instead. `registry` lets a
    caller inject its own AgentRegistryPort (e.g. for tests); defaults to a
    fresh InMemoryRegistryAdapter. `context_builder` applies here too, for
    consistency with AGENT mode.

    Raises:
      ValueError: mode=AGENT with no `handler`.
    """
    if mode == ServerMode.DISCOVERY:
        return _create_discovery_app(
            agent_card=agent_card, rpc_url=rpc_url, registry=registry, context_builder=context_builder
        )

    if handler is None:
        raise ValueError("create_app(mode=AGENT) requires a `handler`")

    proto_card = agent_card.to_agent_card()
    request_handler = DefaultRequestHandler(
        agent_executor=AgentExecutor(
            handler=handler,
            on_cancel=on_cancel,
            message_id_generator=message_id_generator,
        ),
        task_store=task_store or InMemoryTaskStore(),
        agent_card=proto_card,
        push_config_store=push_config_store,
        push_sender=push_sender,
    )

    builder = context_builder or A2AUtilityCallContextBuilder()
    routes: list[BaseRoute] = list(create_agent_card_routes(proto_card))
    routes.extend(create_jsonrpc_routes(request_handler, rpc_url, context_builder=builder))
    if include_rest_routes:
        routes.extend(create_rest_routes(request_handler, context_builder=builder))
    if extra_routes:
        routes.extend(extra_routes)

    return Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=_make_lifespan(request_handler, registry_url, registration_payload or {}),
    )


def serve(app: Starlette, *, host: str = "127.0.0.1", port: int) -> None:
    """Blocking: run the app under uvicorn."""
    uvicorn.run(app, host=host, port=port)


def serve_as_a2a(
    *,
    card: ExtendedAgentCard,
    handler: Optional[AgentHandlerPort] = None,
    on_cancel: Optional[OnCancelPort] = None,
    mode: ServerMode = ServerMode.AGENT,
    registry_url: Optional[str] = None,
    registry: Optional[AgentRegistryPort] = None,
    context_builder: Optional[ServerCallContextBuilder] = None,
    message_id_generator: Optional[IDGenerator] = None,
    task_store: Optional[TaskStore] = None,
    push_config_store: Optional[PushNotificationConfigStore] = None,
    push_sender: Optional[PushNotificationSender] = None,
    middleware: Optional[list[Middleware]] = None,
    extra_routes: Optional[list[BaseRoute]] = None,
) -> None:
    """One-call convenience: from a single ExtendedAgentCard, create the app
    and serve it.

    A domain agent passes `card=ExtendedAgentCard(name=..., description=...,
    port=..., skills=[ExtendedAgentSkill(...)])` (host defaults to 127.0.0.1)
    — all the a2a boilerplate (version, modes, capabilities, JSONRPC
    interface) is filled internally. host/port come from the card.

    mode=AGENT (default): `handler` (required) is a domain-agent-authored
    AgentHandlerPort — a plain async function, or an object with __call__ if the
    agent wants to hold construction state. Every other keyword is optional and
    forwarded to `create_app` unchanged — `task_store`/`push_*` for durability,
    `middleware`/`extra_routes` for anything else mounted alongside; see
    create_app's docstring for each. If registry_url is given, self-registers
    {name, description, agent_card_url} and heartbeats.

    mode=DISCOVERY: `handler`/`on_cancel`/registry_url/`message_id_generator`
    are ignored (a registry node doesn't self-register or run any handler);
    pass `registry` to inject a non-default AgentRegistryPort,
    `context_builder` still applies.
    """
    if mode == ServerMode.DISCOVERY:
        app = create_app(mode=mode, agent_card=card, registry=registry, context_builder=context_builder)
        serve(app, host=card.host, port=card.port)
        return

    if handler is None:
        raise ValueError("serve_as_a2a(mode=AGENT) requires a `handler`")

    registration_payload = {
        "name": card.name,
        "description": card.description,
        "agent_card_url": card.agent_card_url,
    }
    app = create_app(
        mode=mode,
        handler=handler,
        on_cancel=on_cancel,
        agent_card=card,
        registry_url=registry_url,
        registration_payload=registration_payload,
        context_builder=context_builder,
        message_id_generator=message_id_generator,
        task_store=task_store,
        push_config_store=push_config_store,
        push_sender=push_sender,
        middleware=middleware,
        extra_routes=extra_routes,
    )
    serve(app, host=card.host, port=card.port)
