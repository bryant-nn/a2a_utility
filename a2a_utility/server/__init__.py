"""a2a_utility.server — a hexagonal / DDD A2A server package (Executor-Callback model).

Public surface:
  - serve_as_a2a: run an AGENT server from an executor callback (Task, UserContext) -> list[Part].
  - handler_to_executor: bridge a legacy (text, emit_thought) -> str handler onto that callback.
  - run_agent_server / run_discovery_server: start a standalone node by function call
    (CLI equivalent: `python -m a2a_utility.server.main`).
  - ServerMode / A2ASettings: mode selection + pydantic settings (env prefix A2A_).
  - Data Contract: Part, DataType, Task, UserContext, ExecutorCallback, ThoughtEmitter.
"""

from .application.dtos import Task, ThoughtEmitter, UserContext
from .application.handler_bridge import handler_to_executor
from .application.ports.inbound.executor_callback import ExecutorCallback
from .config import A2ASettings, ServerMode
from .domain.models.part import DataType, Part
from .main import run_agent_server, run_discovery_server
from .server import serve_as_a2a

__all__ = [
    "serve_as_a2a",
    "handler_to_executor",
    "run_agent_server",
    "run_discovery_server",
    "ServerMode",
    "A2ASettings",
    "Part",
    "DataType",
    "Task",
    "UserContext",
    "ExecutorCallback",
    "ThoughtEmitter",
]
