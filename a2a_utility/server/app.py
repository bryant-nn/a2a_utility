"""Composition helpers: build_agent_card / create_app / serve / serve_as_a2a.

A domain agent writes its own AgentExecutor subclass and hands it to create_app
together with an AgentCard. create_app wires the native a2a request handler +
routes and injects a2a_utility's ServerCallContextBuilder (for Principal/permission).
serve_as_a2a is the one-call convenience that also builds the card and runs uvicorn
with registry self-registration (heartbeat).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

import httpx
import uvicorn
from a2a.server.agent_execution import AgentExecutor
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

from .context import A2AUtilityCallContextBuilder

__all__ = [
    "build_agent_card",
    "create_app",
    "serve",
    "serve_as_a2a",
    "REGISTRY_HEARTBEAT_SECONDS",
]

logger = logging.getLogger(__name__)

REGISTRY_HEARTBEAT_SECONDS = 5.0


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


def create_app(
    *,
    executor: AgentExecutor,
    agent_card: AgentCard,
    registry_url: Optional[str] = None,
    registration_payload: Optional[dict] = None,
    rpc_url: str = "/",
) -> Starlette:
    """Wire a domain agent's AgentExecutor into an A2A Starlette app.

    Injects a2a_utility's ServerCallContextBuilder so every request carries a
    typed Principal reachable via get_principal(context) inside execute().
    """
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
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
    executor: AgentExecutor,
    name: str,
    description: str,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    examples: list[str],
    host: str = "127.0.0.1",
    port: int,
    registry_url: Optional[str] = None,
) -> None:
    """One-call convenience: build the card, create the app, and serve it.

    `executor` is a domain-agent-authored AgentExecutor instance. If registry_url
    is given, self-registers {name, description, agent_card_url} and heartbeats.
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
    app = create_app(
        executor=executor,
        agent_card=agent_card,
        registry_url=registry_url,
        registration_payload=registration_payload,
    )
    serve(app, host=host, port=port)
