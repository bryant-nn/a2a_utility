"""Agent-card builder models — the Pydantic wrappers a domain agent fills in.

`a2a.types.AgentCard` / `AgentSkill` / `AgentInterface` / `AgentCapabilities`
are protobuf messages and cannot be subclassed, so these WRAP them: a domain
agent fills only the meaningful values (name / description / host / port + at
least one skill) and the A2A boilerplate (version, input/output modes,
capabilities, the JSONRPC interface) is defaulted internally but stays
overridable. `.to_agent_card()` produces the genuine a2a message the SDK
expects.

The card is also where an agent **declares how it wants to be authenticated**.
That declaration is not decoration: a native A2A client's `AuthInterceptor`
reads `security_schemes`/`security_requirements` off the fetched card to
decide which credential to attach, and attaches nothing at all if the card
declares nothing. So an agent whose server-side GateKeeper demands a bearer
token but whose card is silent will reject every well-behaved client. Use the
`auth=` shorthand (see `BearerAuth`/`ApiKeyAuth`) to keep the two in step.

Kept in its own module (not app.py) so the card-building data models live
together and app.py stays pure composition/serving.
"""

from __future__ import annotations

from typing import Optional, Union

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from pydantic import BaseModel, Field

__all__ = [
    "ExtendedAgentSkill",
    "ExtendedAgentCard",
    "ExtendedAgentProvider",
    "BearerAuth",
    "ApiKeyAuth",
    "AuthScheme",
]


# --------------------------------------------------------------------------- #
# Auth declarations                                                            #
# --------------------------------------------------------------------------- #
class BearerAuth(BaseModel):
    """Declares "send me an `Authorization: Bearer <token>` header".

    The right choice for JWT-based internal auth: it is what the GateKeeper's
    default token extraction reads, and native clients map both explicit
    bearer schemes and OAuth2/OIDC ones onto the same header.
    """

    scheme_name: str = "bearer"
    description: Optional[str] = None
    bearer_format: Optional[str] = "JWT"
    scopes: list[str] = Field(default_factory=list)
    """Scopes a caller's token must carry. Advertised on the card so callers
    know what to ask for; enforcing them is the GateKeeper's job."""

    def to_scheme(self) -> SecurityScheme:
        http = HTTPAuthSecurityScheme(scheme="bearer")
        if self.bearer_format:
            http.bearer_format = self.bearer_format
        if self.description:
            http.description = self.description
        return SecurityScheme(http_auth_security_scheme=http)


class ApiKeyAuth(BaseModel):
    """Declares "send me an API key in this header"."""

    scheme_name: str = "api_key"
    header_name: str = "X-API-Key"
    description: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)

    def to_scheme(self) -> SecurityScheme:
        api_key = APIKeySecurityScheme(name=self.header_name, location="header")
        if self.description:
            api_key.description = self.description
        return SecurityScheme(api_key_security_scheme=api_key)


AuthScheme = Union[BearerAuth, ApiKeyAuth]


# --------------------------------------------------------------------------- #
# Card models                                                                  #
# --------------------------------------------------------------------------- #
class ExtendedAgentProvider(BaseModel):
    """Who runs this agent — surfaced in directories and consoles."""

    organization: str
    url: Optional[str] = None

    def to_proto(self) -> AgentProvider:
        provider = AgentProvider(organization=self.organization)
        if self.url:
            provider.url = self.url
        return provider


class ExtendedAgentSkill(BaseModel):
    """Wraps a2a's protobuf AgentSkill.

    A domain agent fills id/name/description/examples; input/output modes and
    tags (default `[id]`) are defaulted internally.
    """

    id: str
    name: str
    description: str
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    tags: Optional[list[str]] = None  # defaults to [id]

    def to_proto(self) -> AgentSkill:
        return AgentSkill(
            id=self.id,
            name=self.name,
            description=self.description,
            input_modes=self.input_modes,
            output_modes=self.output_modes,
            tags=self.tags if self.tags is not None else [self.id],
            examples=self.examples,
        )


class ExtendedAgentCard(BaseModel):
    """Wraps a2a's protobuf AgentCard.

    A domain agent fills name / description / host / port and at least one
    skill; version, input/output modes, capabilities, and the JSONRPC
    interface are defaulted internally (still overridable).
    """

    name: str
    description: str
    port: int
    # a2a AgentCard.skills is a repeated field — a card advertises one or more
    # skills; at least one is required.
    skills: list[ExtendedAgentSkill] = Field(min_length=1)
    host: str = "127.0.0.1"
    version: str = "0.1.0"
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    streaming: bool = True
    protocol_binding: str = "JSONRPC"
    protocol_version: str = "1.0"

    # ---- discovery metadata ----
    provider: Optional[ExtendedAgentProvider] = None
    documentation_url: Optional[str] = None
    icon_url: Optional[str] = None

    # ---- auth ----
    auth: Optional[AuthScheme] = None
    """Shorthand that fills `security_schemes` + `security_requirements`
    together, so the card can't declare a scheme it doesn't require or
    require one it never described. For anything more elaborate (multiple
    alternative schemes, OAuth2 flows), set the two fields below directly and
    leave this None."""

    security_schemes: dict[str, SecurityScheme] = Field(default_factory=dict)
    security_requirements: list[SecurityRequirement] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}  # SecurityScheme is a protobuf message

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def agent_card_url(self) -> str:
        return f"{self.url}/.well-known/agent-card.json"

    def _resolved_security(self) -> tuple[dict[str, SecurityScheme], list[SecurityRequirement]]:
        """Merges the `auth=` shorthand into the explicit fields.

        Explicit entries win: a caller who set both is expressing something
        the shorthand can't, so the shorthand yields rather than overwriting.
        """
        schemes = dict(self.security_schemes)
        requirements = list(self.security_requirements)
        if self.auth is not None and self.auth.scheme_name not in schemes:
            schemes[self.auth.scheme_name] = self.auth.to_scheme()
            # `schemes` on a requirement is a map of scheme name -> the scopes
            # that scheme must grant, not a plain list of names. An empty
            # StringList means "this scheme, no particular scopes".
            requirement = SecurityRequirement()
            requirement.schemes[self.auth.scheme_name].CopyFrom(
                StringList(list=self.auth.scopes)
            )
            requirements.append(requirement)
        return schemes, requirements

    def to_agent_card(self) -> AgentCard:
        schemes, requirements = self._resolved_security()

        card = AgentCard(
            name=self.name,
            description=self.description,
            version=self.version,
            default_input_modes=self.default_input_modes,
            default_output_modes=self.default_output_modes,
            capabilities=AgentCapabilities(streaming=self.streaming),
            supported_interfaces=[
                AgentInterface(
                    protocol_binding=self.protocol_binding,
                    url=self.url,
                    protocol_version=self.protocol_version,
                )
            ],
            skills=[s.to_proto() for s in self.skills],
            security_requirements=requirements,
        )
        for scheme_name, scheme in schemes.items():
            card.security_schemes[scheme_name].CopyFrom(scheme)
        if self.provider is not None:
            card.provider.CopyFrom(self.provider.to_proto())
        if self.documentation_url:
            card.documentation_url = self.documentation_url
        if self.icon_url:
            card.icon_url = self.icon_url
        return card
