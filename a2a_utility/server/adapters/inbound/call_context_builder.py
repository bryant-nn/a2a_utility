"""Inbound adapter: assembling the per-request ServerCallContext.

a2a threads per-request state through `context.call_context`, and the
sanctioned extension point is a custom `ServerCallContextBuilder` passed to
`create_jsonrpc_routes(..., context_builder=)`. This is a2a_utility's.

It deliberately does very little — this is a named, stable seam to extend,
not somewhere logic already lives. `DefaultServerCallContextBuilder` already
puts the raw request headers in `state['headers']` (asserted by
`tests/test_sdk_contract.py`), so a deployment wanting to attach anything
else to every request — a trace id, a tenant resolved from the hostname,
identity parsed from a credential — overrides `build()` in a subclass and
passes it via `context_builder=` on `create_app()`/`serve_as_a2a()`, without
forking this package.
"""

from __future__ import annotations

from a2a.server.context import ServerCallContext
from a2a.server.routes.common import DefaultServerCallContextBuilder
from starlette.requests import Request

__all__ = ["A2AUtilityCallContextBuilder"]


class A2AUtilityCallContextBuilder(DefaultServerCallContextBuilder):
    """The default context builder. Adds nothing to native's behavior yet;
    it is the seam to add to."""

    def build(self, request: Request) -> ServerCallContext:
        return super().build(request)
