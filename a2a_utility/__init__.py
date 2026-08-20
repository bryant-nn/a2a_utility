"""a2a_utility — the internal A2A library.

Everything an agent developer needs is reachable from here; nothing requires
importing `a2a.*` directly. The native SDK is a dependency of this package,
not of the code you write against it.

    from a2a_utility import serve_as_a2a, ExtendedAgentCard, ExtendedPart

Three subpackages, with a one-way dependency direction:

    schema/     the typed data contract, shared by both sides
    server/     stand up an A2A endpoint (AGENT node / DISCOVERY node)
    client/     call an A2A endpoint

`server` and `client` don't depend on each other — only on `schema` — so
`pip install a2a-utility[server]` doesn't drag in the client's dependencies
and vice versa. That isolation is why the names below are resolved lazily
through `__getattr__` instead of imported eagerly: touching
`a2a_utility.serve_as_a2a` imports the server subpackage at that moment, and
a client-only process that never touches a server name never pays for (or
requires) the server extras. Eager re-exports here would break exactly the
split the three-subpackage layout exists to preserve.

`schema` names are always safe to import — both extras include it.

See README.md for usage and docs/DESIGN.md for the design rationale.
"""

from typing import Any

from .schema import (
    A2ATaskResult,
    CustomizedData,
    ExtendedArtifact,
    ExtendedMessage,
    ExtendedPart,
    ExtendedTask,
    ExtendedTaskState,
    MessageLike,
    MessageRole,
    PartEmitter,
    SourceReferenceResponse,
    VercelThinkingResponse,
    as_thinking_emitter,
)

__version__ = "0.1.0"

# name -> submodule it lives in. Resolved on first attribute access.
_LAZY: dict[str, str] = {
    # --- server ---
    "A2ASettings": "server",
    "AuthRequired": "server",
    "DomainAgentExecutorPort": "server",
    "ExtendedAgentCard": "server",
    "ExtendedAgentProvider": "server",
    "ExtendedAgentSkill": "server",
    "ExtendedRequestContext": "server",
    "InputRequired": "server",
    "MessageReply": "server",
    "Progress": "server",
    "PublishArtifact": "server",
    "Rejected": "server",
    "ServerMode": "server",
    "TaskEvent": "server",
    "create_app": "server",
    "run_agent_server": "server",
    "run_discovery_server": "server",
    "serve": "server",
    "serve_as_a2a": "server",
    # --- client ---
    "A2ACallError": "client",
    "CredentialProvider": "client",
    "Credentials": "client",
    "DiscoveryClient": "client",
    "ExtendedAgentClient": "client",
    "StaticToken": "client",
    "call_agent": "client",
    "call_agent_parts": "client",
    "call_agent_result": "client",
}

__all__ = [
    "__version__",
    # schema (eager — no extra-specific dependencies)
    "A2ATaskResult",
    "CustomizedData",
    "ExtendedArtifact",
    "ExtendedMessage",
    "ExtendedPart",
    "ExtendedTask",
    "ExtendedTaskState",
    "MessageLike",
    "MessageRole",
    "PartEmitter",
    "SourceReferenceResponse",
    "VercelThinkingResponse",
    "as_thinking_emitter",
    *sorted(_LAZY),
]


def __getattr__(name: str) -> Any:
    """Resolves a server/client name by importing its subpackage on demand."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache, so this runs once per name
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
