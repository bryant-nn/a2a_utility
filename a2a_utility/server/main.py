"""Composition Root — AGENT (demo) and DISCOVERY nodes, env-var driven.

    A2A_SERVER_MODE=AGENT      python -m a2a_utility.server.main
    A2A_SERVER_MODE=DISCOVERY  python -m a2a_utility.server.main

AGENT mode runs a demo AgentExecutor emitting typed ExtendedParts (text +
thinking + source_reference). DISCOVERY mode is the zero-LLM registry node
(REST /register + /agents, plus an A2A "who can do X?" search endpoint).
Real domain agents don't use this file — they write their own AgentExecutor
and call a2a_utility.server.serve_as_a2a() / create_app().
"""

import contextlib
from typing import Optional

import uvicorn
from google.protobuf.json_format import MessageToDict
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .adapters.inbound.discovery_agent_executor import DiscoveryAgentExecutor
from .adapters.outbound.in_memory_registry_adapter import InMemoryRegistryAdapter
from .application.use_cases.register_agent_card_use_case import RegisterAgentCardUseCase
from .application.use_cases.search_agent_use_case import SearchAgentUseCase
from .app import create_app
from .config import A2ASettings, ServerMode
from .context import A2AUtilityCallContextBuilder
from .domain.models.agent_card import AgentDescriptor
from ..types import ExtendedPart
from .updater import TypedTaskUpdater

REGISTRY_TTL_SECONDS = 15.0


def _build_agent_card(settings: A2ASettings, skill: AgentSkill) -> AgentCard:
    return AgentCard(
        name=settings.agent_name,
        description=settings.agent_description,
        version=settings.agent_version,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=settings.url, protocol_version="1.0")
        ],
        skills=[skill],
    )


def _agent_card_route(agent_card: AgentCard) -> Route:
    card_dict = MessageToDict(agent_card, preserving_proto_field_name=True)

    async def get_agent_card(request: Request) -> JSONResponse:
        return JSONResponse(card_dict)

    return Route("/.well-known/agent.json", get_agent_card, methods=["GET"])


def _serve(app: Starlette, settings: A2ASettings) -> None:
    uvicorn.run(app, host=settings.host, port=settings.port)


class _DemoExecutor(AgentExecutor):
    """Demo AgentExecutor returning typed ExtendedParts (adjust.md-style)."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        u = TypedTaskUpdater.of(context, event_queue)
        await u.start_work()
        await u.emit_thinking("Parsing the request...")
        query = context.get_user_input()
        await u.complete([
            ExtendedPart.thinking("Picked a canned demo reply."),
            ExtendedPart.source_reference([{"source": "demo", "note": "no real data source"}]),
            ExtendedPart.from_text(f"You said: {query!r}. This is a demo executor."),
        ])

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")


def run_agent_server(settings: Optional[A2ASettings] = None) -> None:
    """AGENT mode: demo AgentExecutor mounted at POST /a2a/v1/chat."""
    settings = settings or A2ASettings()  # type: ignore[call-arg]
    skill = AgentSkill(
        id="chat",
        name="General Chat",
        description="Demo executor returning typed Parts as an Artifact.",
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        tags=["chat"],
        examples=["Tell me a joke", "Summarize this article"],
    )
    agent_card = _build_agent_card(settings, skill)
    # JSON-RPC mounted at "/" to match the card's advertised interface url, so the
    # native a2a client (which resolves the card then POSTs to that url) can reach it.
    app = create_app(executor=_DemoExecutor(), agent_card=agent_card)
    app.routes.insert(0, _agent_card_route(agent_card))
    _serve(app, settings)


def run_discovery_server(settings: Optional[A2ASettings] = None) -> None:
    """DISCOVERY mode: registry-backed discovery. No LLM/executor is created."""
    settings = settings or A2ASettings()  # type: ignore[call-arg]
    registry = InMemoryRegistryAdapter(ttl_seconds=REGISTRY_TTL_SECONDS)
    register_use_case = RegisterAgentCardUseCase(registry)
    search_use_case = SearchAgentUseCase(registry)

    skill = AgentSkill(
        id="discovery",
        name="Agent Discovery",
        description="Register agent cards and find agents that can handle a given task.",
        input_modes=["text/plain"],
        output_modes=["application/json"],
        tags=["discovery"],
        examples=["who can do math?", "which agent handles weather?"],
    )
    agent_card = _build_agent_card(settings, skill)

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

    routes = [
        _agent_card_route(agent_card),
        Route("/register", register, methods=["POST"]),
        Route("/agents", list_agents, methods=["GET"]),
        *create_jsonrpc_routes(
            discovery_request_handler, "/", context_builder=A2AUtilityCallContextBuilder()
        ),
        *create_jsonrpc_routes(
            discovery_request_handler, "/a2a/v1/discovery",
            context_builder=A2AUtilityCallContextBuilder(),
        ),
    ]
    routes.extend(create_agent_card_routes(agent_card))

    app = Starlette(routes=routes)
    _serve(app, settings)


def main() -> None:
    settings = A2ASettings()
    if settings.server_mode == ServerMode.DISCOVERY:
        run_discovery_server(settings)
    else:
        run_agent_server(settings)


if __name__ == "__main__":
    main()
