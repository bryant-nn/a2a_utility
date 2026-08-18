"""Inbound adapter: assembling the per-request ServerCallContext.

a2a threads per-request state through `context.call_context`, built by a
`ServerCallContextBuilder` passed to `create_jsonrpc_routes(...,
context_builder=)`. This is a2a_utility's, and `create_app()` always uses
it — it is not an injection point on that function.

It deliberately does very little; it exists as a named place for this
concern rather than an anonymous native default buried in the composition
root. `DefaultServerCallContextBuilder` already puts the raw request headers
in `state['headers']` (asserted by `tests/test_sdk_contract.py`).

A deployment that needs to attach something else to every request — a trace
id, a tenant resolved from the hostname, identity parsed from a credential —
subclasses this and composes its own app from the exported `AgentExecutor`
and native `DefaultRequestHandler`, passing the subclass to
`create_jsonrpc_routes()` directly. `create_app()` is the short path, not the
configurable one.
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
