"""Inbound adapter: assembling the per-request ServerCallContext.

a2a threads per-request state through `context.call_context`, and the
sanctioned extension point is a custom `ServerCallContextBuilder` passed to
`create_jsonrpc_routes(..., context_builder=)`. This is a2a_utility's.

It deliberately does very little. An earlier version parsed headers into a
`Principal` here, which put identity construction in the wrong place: a
context builder can only return a context or raise, and raising surfaces as a
JSON-RPC internal error — so authentication failures came out as opaque 500s
instead of something an A2A caller could act on. Identity now belongs to the
`GateKeeper`, which runs inside `AgentExecutor.execute()` where a refusal can
become a `REJECTED`/`AUTH_REQUIRED` *task state*.

What remains is making sure the raw headers reach the gate.
`DefaultServerCallContextBuilder` already puts them in `state['headers']`
(asserted by `tests/test_sdk_contract.py`), so this subclass exists mainly as
a named, stable extension point: override `build()` to attach anything else
your deployment wants on every request — a trace id, a tenant resolved from
the hostname — without forking this package.
"""

from __future__ import annotations

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.routes.common import DefaultServerCallContextBuilder
from starlette.requests import Request

from ...domain.models.user_context import UserContext, read_user_context

__all__ = ["A2AUtilityCallContextBuilder", "get_user_context"]


class A2AUtilityCallContextBuilder(DefaultServerCallContextBuilder):
    """The default context builder. Adds nothing to native's behavior yet;
    it is the seam to add to."""

    def build(self, request: Request) -> ServerCallContext:
        return super().build(request)


def get_user_context(context: RequestContext) -> UserContext:
    """The caller identity, for code holding a *native* RequestContext.

    Handlers should use `ExtendedRequestContext.user` instead — this exists
    for code sitting outside the handler boundary, such as a custom
    `AgentExecutor` or a native-typed middleware.
    """
    return read_user_context(context.call_context.state)
