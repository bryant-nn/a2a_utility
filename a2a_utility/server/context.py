"""Permission / identity for the RequestContext.

a2a threads per-request identity through context.call_context (a ServerCallContext
with a typed User + an arbitrary `state` dict). The sanctioned extension point is a
custom ServerCallContextBuilder passed to create_jsonrpc_routes(..., context_builder=).

This builds a typed `Principal` from the incoming request and stashes it in
call_context.state; domain agents read it inside execute() via get_principal(context).
The header parsing here is a STUB — a real deployment validates a JWT / session
instead. This is the single place to do that.
"""

from __future__ import annotations

from typing import Optional

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.routes.common import DefaultServerCallContextBuilder
from pydantic import BaseModel, Field
from starlette.requests import Request

_PRINCIPAL_KEY = "principal"


class Principal(BaseModel):
    user_id: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    tenant_id: Optional[str] = None
    token: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


class A2AUtilityCallContextBuilder(DefaultServerCallContextBuilder):
    """Builds the native ServerCallContext, then attaches a typed Principal.

    Override `build_principal` to plug in real auth (JWT/session validation).
    """

    def build(self, request: Request) -> ServerCallContext:
        ctx = super().build(request)
        ctx.state[_PRINCIPAL_KEY] = self.build_principal(request, ctx)
        return ctx

    def build_principal(self, request: Request, ctx: ServerCallContext) -> Principal:
        headers = {k.lower(): v for k, v in dict(ctx.state.get("headers", {})).items()}
        auth = headers.get("authorization", "")
        token = auth[len("Bearer "):].strip() if auth.lower().startswith("bearer ") else None
        roles = [r for r in headers.get("x-user-roles", "").split(",") if r]
        return Principal(
            user_id=headers.get("x-user-id"),
            roles=roles,
            tenant_id=ctx.tenant or headers.get("x-tenant-id"),
            token=token,
        )


def get_principal(context: RequestContext) -> Principal:
    """Read the Principal that the context builder attached to this request."""
    principal = context.call_context.state.get(_PRINCIPAL_KEY)
    return principal if isinstance(principal, Principal) else Principal()
