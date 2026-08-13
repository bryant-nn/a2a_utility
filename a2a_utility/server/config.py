"""Pydantic settings + ServerMode for the A2A server.

Two independent modes share this one package:

- AGENT:      an LLM/domain-agent server that answers chat tasks.
- DISCOVERY:  a zero-LLM node that only answers "who's available?" queries.

`main.py` reads `A2ASettings.server_mode` and conditionally wires only the
components that mode needs — in DISCOVERY mode no LLM adapter is ever built.
"""

from enum import Enum
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerMode(str, Enum):
    DISCOVERY = "DISCOVERY"
    AGENT = "AGENT"


class A2ASettings(BaseSettings):
    """Server-level settings (env prefix: A2A_)."""

    model_config = SettingsConfigDict(env_prefix="A2A_", extra="ignore")

    server_mode: ServerMode = ServerMode.AGENT

    agent_name: str = "generic_agent"
    agent_description: str = "A generic A2A agent."
    agent_version: str = "0.1.0"

    host: str = "127.0.0.1"
    port: int = 9000

    # If set, an AGENT server self-registers here (heartbeat); a DISCOVERY
    # server can seed itself from the same registry.
    registry_url: Optional[str] = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def agent_card_url(self) -> str:
        return f"{self.url}/.well-known/agent-card.json"
