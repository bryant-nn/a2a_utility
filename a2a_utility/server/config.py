"""Pydantic settings + ServerMode for the A2A server.

Two independent modes share this one package:

- AGENT:      an LLM/domain-agent server that answers chat tasks.
- DISCOVERY:  a zero-LLM node that only answers "who's available?" queries.

`main.py` reads `A2ASettings.server_mode` and conditionally wires only the
components that mode needs — in DISCOVERY mode no LLM adapter is ever built.

Everything here is env-driven with the `A2A_` prefix, so the same image can be
deployed as either mode, with or without auth, without a code change.
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
    registry_heartbeat_seconds: float = 5.0
    registry_ttl_seconds: float = 15.0
    """How long a registered agent stays listed without a heartbeat. Must
    comfortably exceed the heartbeat interval, or healthy agents flicker out
    of the directory between beats."""

    # ---- auth ----
    require_auth: bool = False
    """Refuse to start without a configured gate. Off by default so local
    development needs no token issuer; **set this in every deployed
    environment** — it is the check that stops an unauthenticated server
    reaching production because someone forgot to pass `gate_keeper=`."""

    jwks_url: Optional[str] = None
    jwt_issuer: Optional[str] = None
    jwt_audience: Optional[str] = None
    """This service's own identifier, checked against the token's `aud`.
    Without it, a token issued for any other internal service is accepted
    here too."""
    jwt_algorithms: list[str] = ["RS256"]

    permission_service_url: Optional[str] = None
    permission_cache_ttl_seconds: float = 30.0
    """Also the revocation delay — a withdrawn permission keeps working until
    the cached entry expires. Choose it for that, not for hit rate."""

    required_permission: Optional[str] = None
    """Agent-level permission every caller must hold, e.g.
    `"agent:weather"`. Tool-level checks happen inside the handler via
    `context.user.require(...)`."""

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def agent_card_url(self) -> str:
        return f"{self.url}/.well-known/agent-card.json"

    def build_gate_keeper(self):
        """The GateKeeper these settings describe, or None if no auth is
        configured.

        Returns None when `jwks_url` is unset — meaning "this deployment has
        no token verification", which `create_app` turns into either a warning
        or a startup error depending on `require_auth`.
        """
        if not self.jwks_url:
            return None

        # Imported here, not at module scope: the JWT adapter pulls in PyJWT,
        # which is an optional extra, and a deployment with no auth
        # configured must still be able to import this module.
        from .adapters.outbound.cached_permission_service import CachedPermissionService
        from .adapters.outbound.http_permission_service import HttpPermissionService
        from .adapters.outbound.jwt_token_verifier import JwtTokenVerifier
        from .application.services.gate_keeper import GateKeeper

        permission_service = None
        if self.permission_service_url:
            permission_service = CachedPermissionService(
                HttpPermissionService(self.permission_service_url),
                ttl_seconds=self.permission_cache_ttl_seconds,
            )

        return GateKeeper(
            JwtTokenVerifier(
                jwks_url=self.jwks_url,
                issuer=self.jwt_issuer,
                audience=self.jwt_audience,
                algorithms=self.jwt_algorithms,
            ),
            permission_service,
        )
