"""Composition helpers: build_agent_card / create_app / serve / serve_as_a2a.

A domain agent hands create_app a `DomainAgentExecutorPort` instance (see
application/ports/inbound/domain_agent_executor_port.py) together with an
AgentCard, mode=ServerMode.AGENT (the default). create_app builds
adapters/inbound/agent_executor.py's AgentExecutor around that instance and
wires the native a2a request handler + routes. A domain agent never imports
AgentExecutor itself — only this module and application.dtos/
application.ports.inbound.domain_agent_executor_port. serve_as_a2a is the
one-call convenience that also builds the card and runs uvicorn with registry
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
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from .adapters.inbound.agent_executor import AgentExecutor
from .adapters.inbound.discovery_agent_executor import DiscoveryAgentExecutor
from .adapters.outbound.in_memory_registry_adapter import InMemoryRegistryAdapter
from .application.ports.inbound.domain_agent_executor_port import DomainAgentExecutorPort
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
) -> Starlette:
    """DISCOVERY mode: registry-backed discovery, zero LLM/executor.

    Mounts the plain-REST /register + /agents contract (structured registration
    data doesn't fit A2A's Task/Message shape) alongside the real A2A JSON-RPC
    "who can do X?" search route, backed by DiscoveryAgentExecutor.
    """
    proto_card = agent_card.to_agent_card()
    registry = InMemoryRegistryAdapter(ttl_seconds=REGISTRY_TTL_SECONDS)
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
    context_builder = DefaultServerCallContextBuilder()

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
    executor: Optional[DomainAgentExecutorPort] = None,
    registry_url: Optional[str] = None,
    registration_payload: Optional[dict] = None,
    rpc_url: str = "/",
) -> Starlette:
    """Wire an A2A Starlette app, in either mode.

    Takes an `ExtendedAgentCard` and converts it to the native protobuf card
    internally, so a caller never handles `a2a.types.AgentCard` directly.

    mode=AGENT (default): wraps a domain agent's `executor` (required — a
    `DomainAgentExecutorPort` instance, not an AgentExecutor) behind the
    native a2a request handler. Override `DomainAgentExecutorPort.cancel()`
    only to attach a custom message to an externally requested cancellation;
    most agents don't need it (the default marks the task CANCELED with no
    message).

    mode=DISCOVERY: `executor` is not used (a registry node has no LLM/domain
    logic of its own) — see _create_discovery_app for what gets wired instead.

    This deliberately takes no production-injection arguments. What to do
    instead:

      - middleware, extra routes: the return value is an ordinary Starlette
        app, so use Starlette's own API on it —
        `app.add_middleware(...)` / `app.router.routes.append(Route(...))`.
      - a durable task store, push notifications, A2A's REST binding: compose
        the app yourself from the exported `AgentExecutor` and native
        `DefaultRequestHandler`. That path is why `AgentExecutor` is public.

    The task store is always `InMemoryTaskStore`: every task is lost on
    restart and nothing is shared across replicas.

    Raises:
      ValueError: mode=AGENT with no `executor`.
    """
    if mode == ServerMode.DISCOVERY:
        return _create_discovery_app(agent_card=agent_card, rpc_url=rpc_url)

    if executor is None:
        raise ValueError("create_app(mode=AGENT) requires an `executor`")

    proto_card = agent_card.to_agent_card()
    request_handler = DefaultRequestHandler(
        agent_executor=AgentExecutor(executor=executor),
        task_store=InMemoryTaskStore(),
        agent_card=proto_card,
    )

    builder = DefaultServerCallContextBuilder()
    routes: list[BaseRoute] = list(create_agent_card_routes(proto_card))
    routes.extend(create_jsonrpc_routes(request_handler, rpc_url, context_builder=builder))

    return Starlette(
        routes=routes,
        lifespan=_make_lifespan(request_handler, registry_url, registration_payload or {}),
    )


def serve(app: Starlette, *, host: str = "127.0.0.1", port: int) -> None:
    """Blocking: run the app under uvicorn."""
    uvicorn.run(app, host=host, port=port)


def serve_as_a2a(
    *,
    card: ExtendedAgentCard,
    executor: Optional[DomainAgentExecutorPort] = None,
    mode: ServerMode = ServerMode.AGENT,
    registry_url: Optional[str] = None,
) -> None:
    """One-call convenience: from a single ExtendedAgentCard, create the app
    and serve it.

    A domain agent passes `card=ExtendedAgentCard(name=..., description=...,
    port=..., skills=[ExtendedAgentSkill(...)])` (host defaults to 127.0.0.1)
    — all the a2a boilerplate (version, modes, capabilities, JSONRPC
    interface) is filled internally. host/port come from the card.

    mode=AGENT (default): `executor` (required) is an instance of a domain-
    agent-authored `DomainAgentExecutorPort` subclass. If registry_url is
    given, self-registers {name, description, agent_card_url} and heartbeats.

    mode=DISCOVERY: `executor`/`registry_url` are ignored (a registry node
    doesn't self-register or run any executor).

    This is the shortest path and nothing more. Anything that needs to touch
    the app before it runs — middleware, extra routes, a durable task store —
    calls `create_app()` and `serve()` separately; see `create_app`'s
    docstring.
    """
    if mode == ServerMode.DISCOVERY:
        app = create_app(mode=mode, agent_card=card)
        serve(app, host=card.host, port=card.port)
        return

    if executor is None:
        raise ValueError("serve_as_a2a(mode=AGENT) requires an `executor`")

    registration_payload = {
        "name": card.name,
        "description": card.description,
        "agent_card_url": card.agent_card_url,
    }
    app = create_app(
        mode=mode,
        executor=executor,
        agent_card=card,
        registry_url=registry_url,
        registration_payload=registration_payload,
    )
    serve(app, host=card.host, port=card.port)
