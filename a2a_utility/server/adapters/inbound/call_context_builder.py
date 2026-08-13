"""Inbound adapter: builds the Principal a request carries.

a2a threads per-request identity through context.call_context (a ServerCallContext
with a typed User + an arbitrary `state` dict). The sanctioned extension point is a
custom ServerCallContextBuilder passed to create_jsonrpc_routes(..., context_builder=).

This builds a typed `Principal` from the incoming request and writes it into
call_context.state via domain/models/principal.py's write_principal() — the
header parsing here is a STUB, a real deployment validates a JWT / session
instead. This is the single place to do that.
"""

from __future__ import annotations

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.routes.common import DefaultServerCallContextBuilder
from starlette.requests import Request

from ...domain.models.principal import Principal, read_principal, write_principal


class A2AUtilityCallContextBuilder(DefaultServerCallContextBuilder):
    """Builds the native ServerCallContext, then attaches a typed Principal.

    Override `build_principal` to plug in real auth (JWT/session validation).
    """

    def build(self, request: Request) -> ServerCallContext:
        ctx = super().build(request)
        write_principal(ctx.state, self.build_principal(request, ctx))
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
    """Escape hatch for code holding a native RequestContext directly (not
    wrapped in ExtendedRequestContext). ExtendedRequestContext.principal reads
    domain/models/principal.py's read_principal() directly instead of this,
    to keep the application layer from depending on the adapters layer."""
    return read_principal(context.call_context.state)
