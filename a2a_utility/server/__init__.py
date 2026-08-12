"""a2a_utility.server — a hexagonal / DDD A2A server package.

Public surface:
  - serve_as_a2a: run a pluggable-handler AGENT server (used by domain agents).
  - run_agent_server / run_discovery_server: start a standalone AGENT or
    DISCOVERY node by function call (see main.py's docstring for the CLI
    equivalent, `python -m a2a_utility.server.main`).
  - ServerMode:   DISCOVERY vs AGENT (see config.py / main.py).
  - A2ASettings:  pydantic settings for either mode (env prefix A2A_).
  - Handler / ThoughtEmitter: the handler contract domain agents implement.
"""

from .config import A2ASettings, ServerMode
from .main import run_agent_server, run_discovery_server
from .server import Handler, ThoughtEmitter, serve_as_a2a

__all__ = [
    "serve_as_a2a",
    "run_agent_server",
    "run_discovery_server",
    "ServerMode",
    "A2ASettings",
    "Handler",
    "ThoughtEmitter",
]
