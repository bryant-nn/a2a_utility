"""Composition Root — conditional wiring AND mode-based route mounting.

plan.md §3 (Mode-Based Route Mounting): each SERVER_MODE exposes only its own
endpoints, wired dynamically from settings.server_mode.

Two ways to start a node:

1. CLI (env-var driven), for standalone processes / containers:

    A2A_SERVER_MODE=AGENT      python -m a2a_utility.server.main
    A2A_SERVER_MODE=DISCOVERY  python -m a2a_utility.server.main

2. Function call, for embedding a node inside another process (tests, a
   script that wants an in-process discovery node, a single entrypoint that
   starts several nodes on different threads/tasks, etc.) — no env vars or
   subprocess required:

    from a2a_utility.server.config import A2ASettings, ServerMode
    from a2a_utility.server.main import run_discovery_server

    run_discovery_server(A2ASettings(server_mode=ServerMode.DISCOVERY, port=8090))

   run_agent_server() is the AGENT-mode counterpart. Both are blocking calls
   (they call uvicorn.run internally) — same as the CLI path, just callable
   directly instead of only reachable via `python -m`.

Common endpoint (both modes):
    GET  /.well-known/agent.json        -> this node's Agent Card
    GET  /.well-known/agent-card.json   -> same card (a2a-sdk default name)

AGENT mode (LLM chat; no discovery routes, no registry):
    POST /a2a/v1/chat                   -> ChatAgentExecutor -> ChatUseCase
    (OpenAILLMAdapter is the outbound LLM; closed via Starlette lifespan.)

DISCOVERY mode (zero LLM; no chat routes, OpenAILLMAdapter NEVER constructed):
    POST /register                        -> RegisterAgentCardUseCase (heartbeat; plain REST, not A2A —
                                              registration is structured data, not a chat-shaped query,
                                              so it doesn't map onto an A2A Task/Message)
    GET  /agents                          -> SearchAgentUseCase.list_all (TTL-alive; plain REST, not A2A —
                                              kept byte-compatible with the old registry/server.py contract)
    POST /  and  POST /a2a/v1/discovery   -> real A2A JSON-RPC (SendMessage/SendStreamingMessage), routed
                                              through DefaultRequestHandler -> DiscoveryAgentExecutor ->
                                              SearchAgentUseCase.search (empty query text = list_all).
                                              Mounted at both paths: "/" matches the agent card's advertised
                                              url (so a real A2A client resolving the card can actually call
                                              it), "/a2a/v1/discovery" matches plan.md's named endpoint.

The /register + /agents contract is byte-compatible with registry/server.py, so
a DISCOVERY node is a drop-in registry: the four domain agents heartbeat their
cards to it and the coordinator's list_agents() reads /agents from it unchanged.
Run it on port 8090 to replace registry/server.py outright:

    A2A_SERVER_MODE=DISCOVERY A2A_PORT=8090 python -m a2a_server.main

The domain agents themselves still use a2a_utility.server.serve_as_a2a()
(AGENT-style, pluggable handler) — this file is the standalone AGENT/DISCOVERY
node entry.
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

from .adapters.inbound.chat_agent_executor import ChatAgentExecutor
from .adapters.inbound.discovery_agent_executor import DiscoveryAgentExecutor
from .adapters.outbound.in_memory_registry_adapter import InMemoryRegistryAdapter
from .adapters.outbound.openai_llm_adapter import OpenAILLMAdapter
from .application.use_cases.chat_use_case import ChatUseCase
from .application.use_cases.register_agent_card_use_case import RegisterAgentCardUseCase
from .application.use_cases.search_agent_use_case import SearchAgentUseCase
from .config import A2ASettings, LLMSettings, ServerMode
from .domain.models.agent_card import AgentDescriptor

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

    # a2a.types.AgentCard is a protobuf message (A2A 1.0), not a pydantic model,
    # so serialize it with the protobuf JSON helper.
    card_dict = MessageToDict(agent_card, preserving_proto_field_name=True)

    async def get_agent_card(request: Request) -> JSONResponse:
        return JSONResponse(card_dict)

    return Route("/.well-known/agent.json", get_agent_card, methods=["GET"])


def _serve(app: Starlette, settings: A2ASettings) -> None:
    uvicorn.run(app, host=settings.host, port=settings.port)


def run_agent_server(settings: Optional[A2ASettings] = None) -> None:
    """AGENT mode: OpenAILLMAdapter -> ChatUseCase -> ChatAgentExecutor.

    Mounts ONLY the chat endpoint (POST /a2a/v1/chat) + card routes. No registry
    or discovery routes are installed.

    `settings` defaults to `A2ASettings()` (env-var driven) so this still works
    as the target of `python -m a2a_utility.server.main`; pass an explicit
    instance to embed a node in another process without touching env vars.
    """
    settings = settings or A2ASettings()  # type: ignore[call-arg]
    llm_settings = LLMSettings()  # type: ignore[call-arg]
    llm_adapter = OpenAILLMAdapter(
        base_url=llm_settings.base_url,
        api_key=llm_settings.api_key,
        model=llm_settings.model,
        system_prompt=llm_settings.system_prompt,
    )
    chat_use_case = ChatUseCase(llm_service=llm_adapter)
    executor = ChatAgentExecutor(chat_use_case=chat_use_case)

    skill = AgentSkill(
        id="chat",
        name="General Chat",
        description="Forward user queries to an LLM and stream the response.",
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        tags=["chat", "llm"],
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
    # A2A JSON-RPC handler mounted at the chat path (plan.md §3: POST /a2a/v1/chat)
    routes.extend(create_jsonrpc_routes(request_handler, "/a2a/v1/chat"))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        try:
            yield
        finally:
            await llm_adapter.aclose()

    app = Starlette(routes=routes, lifespan=lifespan)
    _serve(app, settings)


def run_discovery_server(settings: Optional[A2ASettings] = None) -> None:
    """DISCOVERY mode: registry-backed discovery. No LLM adapter is created.

    /register and /agents stay plain REST (see module docstring for why —
    registration is structured data, not a chat-shaped query, so it doesn't
    map onto an A2A Task/Message). The actual "who can do X?" query goes
    through a real A2A JSON-RPC endpoint backed by DiscoveryAgentExecutor,
    exactly the same shape AGENT mode uses for chat — no more hand-rolled
    REST closure standing in for what should be an A2A conversation.

    `settings` defaults to `A2ASettings()` (env-var driven); pass an explicit
    instance (e.g. `A2ASettings(server_mode=ServerMode.DISCOVERY, port=8090)`)
    to start a discovery node in-process — a script, a test fixture, or
    another server's own startup code — without shelling out to `python -m`.
    """
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
        # "/" matches agent_card's advertised url so a real A2A client that
        # resolved the card can actually call it; "/a2a/v1/discovery" is the
        # named endpoint from plan.md. Both are the same JSON-RPC dispatcher.
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
