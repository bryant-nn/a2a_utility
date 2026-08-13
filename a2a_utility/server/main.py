"""Composition Root — AGENT (demo) and DISCOVERY nodes, env-var driven.

    A2A_SERVER_MODE=AGENT      python -m a2a_utility.server.main
    A2A_SERVER_MODE=DISCOVERY  python -m a2a_utility.server.main

AGENT mode runs a demo AgentHandlerPort emitting typed ExtendedParts (text +
thinking + source_reference). DISCOVERY mode is the zero-LLM registry node
(REST /register + /agents, plus an A2A "who can do X?" search endpoint).
Real domain agents don't use this file — they write their own AgentHandlerPort
callable and call a2a_utility.server.serve_as_a2a() / create_app(), same as
the two run_*_server() functions below do (both modes now go through the
single create_app(mode=...) composition root in app.py; this module is just
the env-var-driven CLI entry point + the AGENT-mode demo handler).
"""

from typing import Optional

from a2a.types import AgentSkill

from .app import build_agent_card, create_app, serve
from .application.dtos import ExtendedRequestContext, HandlerCompleted, HandlerResult
from .config import A2ASettings, ServerMode
from ..schema import ExtendedPart, PartEmitter


async def _demo_handler(context: ExtendedRequestContext, emit: PartEmitter) -> HandlerResult:
    """Demo AgentHandlerPort returning a typed HandlerResult (adjust.md-style)."""
    await emit(ExtendedPart.thinking("Parsing the request..."))
    query = context.get_user_input()
    return HandlerCompleted(parts=[
        ExtendedPart.thinking("Picked a canned demo reply."),
        ExtendedPart.source_reference([{"source": "demo", "note": "no real data source"}]),
        ExtendedPart.from_text(f"You said: {query!r}. This is a demo executor."),
    ])


def run_agent_server(settings: Optional[A2ASettings] = None) -> None:
    """AGENT mode: demo AgentHandlerPort mounted at POST /."""
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
    agent_card = build_agent_card(
        name=settings.agent_name,
        description=settings.agent_description,
        skill_id=skill.id,
        skill_name=skill.name,
        skill_description=skill.description,
        examples=list(skill.examples),
        host=settings.host,
        port=settings.port,
    )
    app = create_app(mode=ServerMode.AGENT, handler=_demo_handler, agent_card=agent_card)
    serve(app, host=settings.host, port=settings.port)


def run_discovery_server(settings: Optional[A2ASettings] = None) -> None:
    """DISCOVERY mode: registry-backed discovery. No LLM/executor is created."""
    settings = settings or A2ASettings()  # type: ignore[call-arg]
    skill = AgentSkill(
        id="discovery",
        name="Agent Discovery",
        description="Register agent cards and find agents that can handle a given task.",
        input_modes=["text/plain"],
        output_modes=["application/json"],
        tags=["discovery"],
        examples=["who can do math?", "which agent handles weather?"],
    )
    agent_card = build_agent_card(
        name=settings.agent_name,
        description=settings.agent_description,
        skill_id=skill.id,
        skill_name=skill.name,
        skill_description=skill.description,
        examples=list(skill.examples),
        host=settings.host,
        port=settings.port,
    )
    app = create_app(mode=ServerMode.DISCOVERY, agent_card=agent_card)
    serve(app, host=settings.host, port=settings.port)


def main() -> None:
    settings = A2ASettings()
    if settings.server_mode == ServerMode.DISCOVERY:
        run_discovery_server(settings)
    else:
        run_agent_server(settings)


if __name__ == "__main__":
    main()
