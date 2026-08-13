"""serve_as_a2a — the convenience entry domain agents use (AGENT mode).

Hexagonal composition root for an executor-callback agent server:

    executor ->  ExecutorAgentExecutor (inbound A2A adapter)
             ->  DefaultRequestHandler + Starlette routes (a2a-sdk transport)

The developer supplies an `executor: (Task, UserContext) -> list[Part]`
callback (or wraps a legacy handler via handler_to_executor). If `registry_url`
is given, the server self-registers (POST /register) on startup and re-registers
every REGISTRY_HEARTBEAT_SECONDS so a coordinator can discover it dynamically.
"""

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

from .adapters.inbound.chat_agent_executor import ExecutorAgentExecutor
from .application.ports.inbound.executor_callback import ExecutorCallback

__all__ = ["serve_as_a2a", "build_app", "build_agent_card", "REGISTRY_HEARTBEAT_SECONDS"]

logger = logging.getLogger(__name__)

REGISTRY_HEARTBEAT_SECONDS = 5.0


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


def build_app(
    *,
    agent_card: AgentCard,
    executor: ExecutorCallback,
    registry_url: Optional[str] = None,
    registration_payload: Optional[dict] = None,
) -> Starlette:
    """Wire the hexagonal layers for an executor-callback AGENT server."""
    agent_executor = ExecutorAgentExecutor(executor)

    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes = create_agent_card_routes(agent_card)
    routes.extend(create_jsonrpc_routes(request_handler, "/"))

    return Starlette(
        routes=routes,
        lifespan=_make_lifespan(registry_url, registration_payload or {}),
    )


def serve_as_a2a(
    *,
    name: str,
    description: str,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    examples: list[str],
    executor: ExecutorCallback,
    host: str = "127.0.0.1",
    port: int,
    registry_url: Optional[str] = None,
) -> None:
    """Blocking call: run an A2A server exposing `executor` as this agent's only skill.

    `executor` is an async `(Task, UserContext) -> list[Part]` callback. To reuse
    an existing `(text, emit_thought) -> str` handler, wrap it:

        from a2a_utility.server import serve_as_a2a, handler_to_executor
        serve_as_a2a(..., executor=handler_to_executor(handle))

    If registry_url is given, this also registers {name, description,
    agent_card_url} with that registry on startup and keeps re-registering
    (heartbeat) for as long as the process runs.
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

    registration_payload = {
        "name": name,
        "description": description,
        "agent_card_url": f"http://{host}:{port}/.well-known/agent-card.json",
    }

    app = build_app(
        agent_card=agent_card,
        executor=executor,
        registry_url=registry_url,
        registration_payload=registration_payload,
    )

    uvicorn.run(app, host=host, port=port)
