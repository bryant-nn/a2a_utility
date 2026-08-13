"""Composition Root — conditional wiring AND mode-based route mounting.

Two ways to start a node:

1. CLI (env-var driven):

    A2A_SERVER_MODE=AGENT      python -m a2a_utility.server.main
    A2A_SERVER_MODE=DISCOVERY  python -m a2a_utility.server.main

2. Function call (embed a node in another process):

    from a2a_utility.server.config import A2ASettings, ServerMode
    from a2a_utility.server.main import run_discovery_server
    run_discovery_server(A2ASettings(server_mode=ServerMode.DISCOVERY, port=8090))

Common endpoint (both modes):
    GET  /.well-known/agent.json        -> this node's Agent Card
    GET  /.well-known/agent-card.json   -> same card (a2a-sdk default name)

AGENT mode (Executor-Callback model; no discovery routes, no registry):
    POST /a2a/v1/chat -> ExecutorAgentExecutor -> the demo executor callback,
    which returns Typed Parts (text + thinking_process + source_reference +
    file) packed into one Artifact. This is the adjust.md §step-4 demo.

DISCOVERY mode (zero LLM; no chat routes):
    POST /register                        -> RegisterAgentCardUseCase (heartbeat, REST)
    GET  /agents                          -> SearchAgentUseCase.list_all (TTL, REST)
    POST /  and  POST /a2a/v1/discovery   -> DiscoveryAgentExecutor (A2A JSON-RPC)
"""

import contextlib
from typing import Optional

import uvicorn
from google.protobuf.json_format import MessageToDict
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

from .adapters.inbound.chat_agent_executor import ExecutorAgentExecutor
from .adapters.inbound.discovery_agent_executor import DiscoveryAgentExecutor
from .adapters.outbound.in_memory_registry_adapter import InMemoryRegistryAdapter
from .application.dtos import Task, UserContext
from .application.use_cases.register_agent_card_use_case import RegisterAgentCardUseCase
from .application.use_cases.search_agent_use_case import SearchAgentUseCase
from .config import A2ASettings, ServerMode
from .domain.models.agent_card import AgentDescriptor
from .domain.models.part import Part

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
            AgentInterface(
                protocol_binding="JSONRPC",
                url=settings.url,
                protocol_version="1.0",
            )
        ],
        skills=[skill],
    )


def _agent_card_route(agent_card: AgentCard) -> Route:
    """Common endpoint: GET /.well-known/agent.json -> this node's card."""
    card_dict = MessageToDict(agent_card, preserving_proto_field_name=True)

    async def get_agent_card(request: Request) -> JSONResponse:
        return JSONResponse(card_dict)

    return Route("/.well-known/agent.json", get_agent_card, methods=["GET"])


def _serve(app: Starlette, settings: A2ASettings) -> None:
    uvicorn.run(app, host=settings.host, port=settings.port)


async def _demo_executor(task: Task, ctx: UserContext) -> list[Part]:
    """adjust.md §step-4 demo: return an Artifact of Typed Parts.

    Streams two live thoughts, then returns one Part of each dataType so the
    /a2a/v1/chat response demonstrates the full Data Contract.
    """
    if ctx.emit_thought is not None:
        await ctx.emit_thought("Parsing the request...")
        await ctx.emit_thought("Composing a typed-parts answer...")

    return [
        Part.thinking_process({"type": "reasoning", "text": "Picked a canned demo reply."}),
        Part.source_reference({"source": "demo", "note": "no real data source"}),
        Part.text(f"You said: {task.message!r}. This is a demo executor callback."),
        Part.file({"filename": "demo.txt", "media_type": "text/plain", "content": "hello"}),
    ]


def run_agent_server(settings: Optional[A2ASettings] = None) -> None:
    """AGENT mode: ExecutorAgentExecutor wrapping the demo executor callback.

    Mounts ONLY the chat endpoint (POST /a2a/v1/chat) + card routes.
    """
    settings = settings or A2ASettings()  # type: ignore[call-arg]
    executor = ExecutorAgentExecutor(_demo_executor)

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

    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes = [_agent_card_route(agent_card)]
    routes.extend(create_agent_card_routes(agent_card))  # /.well-known/agent-card.json
    routes.extend(create_jsonrpc_routes(request_handler, "/a2a/v1/chat"))

    app = Starlette(routes=routes)
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
        *create_jsonrpc_routes(discovery_request_handler, "/"),
        *create_jsonrpc_routes(discovery_request_handler, "/a2a/v1/discovery"),
    ]
    routes.extend(create_agent_card_routes(agent_card))  # /.well-known/agent-card.json

    app = Starlette(routes=routes)
    _serve(app, settings)


def main() -> None:
    """CLI entry (`python -m a2a_utility.server.main`) — env-var driven."""
    settings = A2ASettings()
    if settings.server_mode == ServerMode.DISCOVERY:
        run_discovery_server(settings)
    else:
        run_agent_server(settings)


if __name__ == "__main__":
    main()
