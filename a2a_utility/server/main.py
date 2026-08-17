"""Composition Root — AGENT (demo) and DISCOVERY nodes, env-var driven.

    A2A_SERVER_MODE=AGENT      python -m a2a_utility.server.main
    A2A_SERVER_MODE=DISCOVERY  python -m a2a_utility.server.main

AGENT mode runs a demo AgentHandlerPort emitting typed ExtendedParts (text +
thinking + source_reference). DISCOVERY mode is the zero-LLM registry node
(REST /register + /agents, plus an A2A "who can do X?" search endpoint).

Real domain agents don't use this file — they write their own AgentHandlerPort
callable and call `serve_as_a2a()`/`create_app()`, same as the two
run_*_server() functions below do. It is, however, the file people copy from,
so it holds itself to the same rule every domain agent is held to: **not one
`a2a.*` import**. Everything it needs comes from a2a_utility.
"""

from typing import Optional

from ..schema import ExtendedPart
from .app import create_app, serve
from .application.dtos import ExtendedRequestContext
from .adapters.outbound.event_queue_adapter import ExtendedEventQueue
from .adapters.outbound.task_updater_adapter import ExtendedTaskUpdater
from .card import ExtendedAgentCard, ExtendedAgentSkill
from .config import A2ASettings, ServerMode


async def _demo_handler(context: ExtendedRequestContext, event_queue: ExtendedEventQueue) -> None:
    """Demo AgentHandlerPort — builds its own ExtendedTaskUpdater from the
    ExtendedEventQueue it's handed, same calling convention as writing a
    native AgentExecutor.execute()."""
    task_updater = ExtendedTaskUpdater(context, event_queue)
    await task_updater.start_work()
    await task_updater.as_part_emitter()(ExtendedPart.thinking("Parsing the request..."))
    query = context.get_user_input()
    await task_updater.add_artifact(
        [
            ExtendedPart.thinking("Picked a canned demo reply."),
            ExtendedPart.source_reference([{"source": "demo", "note": "no real data source"}]),
            ExtendedPart.from_text(f"You said: {query!r}. This is a demo executor."),
        ]
    )
    await task_updater.complete()


def _demo_card(settings: A2ASettings, skill: ExtendedAgentSkill) -> ExtendedAgentCard:
    return ExtendedAgentCard(
        name=settings.agent_name,
        description=settings.agent_description,
        version=settings.agent_version,
        host=settings.host,
        port=settings.port,
        skills=[skill],
    )


def run_agent_server(settings: Optional[A2ASettings] = None) -> None:
    """AGENT mode: demo AgentHandlerPort mounted at POST /."""
    settings = settings or A2ASettings()  # type: ignore[call-arg]
    skill = ExtendedAgentSkill(
        id="chat",
        name="General Chat",
        description="Demo executor returning typed Parts as an Artifact.",
        examples=["Tell me a joke", "Summarize this article"],
    )
    app = create_app(
        mode=ServerMode.AGENT,
        handler=_demo_handler,
        agent_card=_demo_card(settings, skill),
        gate_keeper=settings.build_gate_keeper(),
        required_permission=settings.required_permission,
        require_auth=settings.require_auth,
    )
    serve(app, host=settings.host, port=settings.port)


def run_discovery_server(settings: Optional[A2ASettings] = None) -> None:
    """DISCOVERY mode: registry-backed discovery. No LLM/executor is created."""
    settings = settings or A2ASettings()  # type: ignore[call-arg]
    skill = ExtendedAgentSkill(
        id="discovery",
        name="Agent Discovery",
        description="Register agent cards and find agents that can handle a given task.",
        output_modes=["application/json"],
        examples=["who can do math?", "which agent handles weather?"],
    )
    app = create_app(mode=ServerMode.DISCOVERY, agent_card=_demo_card(settings, skill))
    serve(app, host=settings.host, port=settings.port)


def main() -> None:
    settings = A2ASettings()
    if settings.server_mode == ServerMode.DISCOVERY:
        run_discovery_server(settings)
    else:
        run_agent_server(settings)


if __name__ == "__main__":
    main()
