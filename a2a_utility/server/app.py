"""Composition helpers: build_agent_card / create_app / serve / serve_as_a2a.

A domain agent hands create_app a plain AgentHandlerPort callable (a function,
or an object with __call__ — see application/ports/inbound/agent_handler_port.py)
together with an AgentCard, mode=ServerMode.AGENT (the default). create_app
builds adapters/inbound/agent_executor.py's AgentExecutor around that handler,
wires the native a2a request handler + routes, and injects a2a_utility's
ServerCallContextBuilder (for Principal/permission). A domain agent never
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
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .adapters.inbound.agent_executor import AgentExecutor
from .adapters.inbound.call_context_builder import A2AUtilityCallContextBuilder
from .adapters.inbound.discovery_agent_executor import DiscoveryAgentExecutor
from .adapters.outbound.in_memory_registry_adapter import InMemoryRegistryAdapter
from .application.ports.inbound.agent_handler_port import AgentHandlerPort
from .application.ports.inbound.on_cancel_port import OnCancelPort
from .application.ports.outbound.registry_port import AgentRegistryPort
from .application.use_cases.register_agent_card_use_case import RegisterAgentCardUseCase
from .application.use_cases.search_agent_use_case import SearchAgentUseCase
from .config import ServerMode
from .domain.models.agent_card import AgentDescriptor

__all__ = [
    "build_agent_card",
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


def _make_lifespan(registry_url: Optional[str], payload: dict):
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        task = None
        if registry_url:
            task = asyncio.create_task(_heartbeat_loop(registry_url, payload))
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return lifespan


# --------------------------------------------------------------------------- #
# Card + app composition                                                       #
# --------------------------------------------------------------------------- #
def build_agent_card(
    *,
    name: str,
    description: str,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    examples: list[str],
    host: str,
    port: int,
) -> AgentCard:
    skill = AgentSkill(
        id=skill_id,
        name=skill_name,
        description=skill_description,
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        tags=[skill_id],
        examples=examples,
    )
    return AgentCard(
        name=name,
        description=description,
        version="0.1.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=f"http://{host}:{port}",
                protocol_version="1.0",
            )
        ],
        skills=[skill],
    )


def _create_discovery_app(
    *,
    agent_card: AgentCard,
    rpc_url: str = "/",
    registry: Optional[AgentRegistryPort] = None,
) -> Starlette:
    """DISCOVERY mode: registry-backed discovery, zero LLM/executor.

    Mounts the plain-REST /register + /agents contract (structured registration
    data doesn't fit A2A's Task/Message shape) alongside the real A2A JSON-RPC
    "who can do X?" search route, backed by DiscoveryAgentExecutor.
    """
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
        agent_card=agent_card,
    )
    context_builder = A2AUtilityCallContextBuilder()

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
    routes.extend(create_agent_card_routes(agent_card))

    return Starlette(routes=routes)


def create_app(
    *,
    mode: ServerMode = ServerMode.AGENT,
    agent_card: AgentCard,
    handler: Optional[AgentHandlerPort] = None,
    on_cancel: Optional[OnCancelPort] = None,
    registry_url: Optional[str] = None,
    registration_payload: Optional[dict] = None,
    rpc_url: str = "/",
    registry: Optional[AgentRegistryPort] = None,
) -> Starlette:
    """Wire an A2A Starlette app, in either mode.

    mode=AGENT (default): wraps a domain agent's `handler` (required — a plain
    AgentHandlerPort callable, not an AgentExecutor) behind the native a2a
    request handler. Injects a2a_utility's ServerCallContextBuilder so every
    request carries a typed Principal reachable via
    ExtendedRequestContext.principal inside the handler. `on_cancel` is an
    optional second callable (OnCancelPort) reacting to an externally
    requested cancellation; most agents don't need it — the default cancel
    behavior (mark CANCELED, no custom message) is fine.

    mode=DISCOVERY: `handler`/`on_cancel` are not used (a registry node has no
    LLM/domain logic of its own) — see _create_discovery_app for what gets
    wired instead. `registry` lets a caller inject its own AgentRegistryPort
    (e.g. for tests); defaults to a fresh InMemoryRegistryAdapter.
    """
    if mode == ServerMode.DISCOVERY:
        return _create_discovery_app(agent_card=agent_card, rpc_url=rpc_url, registry=registry)

    if handler is None:
        raise ValueError("create_app(mode=AGENT) requires a `handler`")

    request_handler = DefaultRequestHandler(
        agent_executor=AgentExecutor(handler=handler, on_cancel=on_cancel),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes = create_agent_card_routes(agent_card)
    routes.extend(
        create_jsonrpc_routes(
            request_handler,
            rpc_url,
            context_builder=A2AUtilityCallContextBuilder(),
        )
    )

    return Starlette(
        routes=routes,
        lifespan=_make_lifespan(registry_url, registration_payload or {}),
    )


def serve(app: Starlette, *, host: str = "127.0.0.1", port: int) -> None:
    """Blocking: run the app under uvicorn."""
    uvicorn.run(app, host=host, port=port)


def serve_as_a2a(
    *,
    mode: ServerMode = ServerMode.AGENT,
    handler: Optional[AgentHandlerPort] = None,
    on_cancel: Optional[OnCancelPort] = None,
    name: str,
    description: str,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    examples: list[str],
    host: str = "127.0.0.1",
    port: int,
    registry_url: Optional[str] = None,
    registry: Optional[AgentRegistryPort] = None,
) -> None:
    """One-call convenience: build the card, create the app, and serve it.

    mode=AGENT (default): `handler` (required) is a domain-agent-authored
    AgentHandlerPort — a plain async function, or an object with __call__ if
    the agent wants to hold construction state. No a2a_utility class to
    import or subclass. `on_cancel` is optional (see create_app's docstring).
    If registry_url is given, self-registers {name, description,
    agent_card_url} and heartbeats.

    mode=DISCOVERY: `handler`/`on_cancel`/registry_url/registration are
    irrelevant (a registry node doesn't self-register anywhere) and ignored;
    pass `registry` to inject a non-default AgentRegistryPort.
    """
    agent_card = build_agent_card(
        name=name,
        description=description,
        skill_id=skill_id,
        skill_name=skill_name,
        skill_description=skill_description,
        examples=examples,
        host=host,
        port=port,
    )
    if mode == ServerMode.DISCOVERY:
        app = create_app(mode=mode, agent_card=agent_card, registry=registry)
        serve(app, host=host, port=port)
        return

    if handler is None:
        raise ValueError("serve_as_a2a(mode=AGENT) requires a `handler`")

    registration_payload = {
        "name": name,
        "description": description,
        "agent_card_url": f"http://{host}:{port}/.well-known/agent-card.json",
    }
    app = create_app(
        mode=mode,
        handler=handler,
        on_cancel=on_cancel,
        agent_card=agent_card,
        registry_url=registry_url,
        registration_payload=registration_payload,
    )
    serve(app, host=host, port=port)
