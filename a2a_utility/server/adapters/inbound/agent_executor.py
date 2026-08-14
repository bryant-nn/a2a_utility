"""AgentExecutor — the inbound adapter bridging native a2a execute()/cancel()
calls to an injected AgentHandlerPort (+ optional OnCancelPort).

This *is* a real subclass of the native `a2a.server.agent_execution.
AgentExecutor` ABC (not a reimplementation) — it genuinely satisfies the SDK's
own contract, so `DefaultRequestHandler`/`create_jsonrpc_routes` work with it
unmodified. Domain agents never see this class at all: `serve_as_a2a(handler=
..., ...)`/`create_app(handler=..., ...)` build one internally from whatever
AgentHandlerPort (a plain callable) the domain agent supplied, exactly the
way `adapters/inbound/discovery_agent_executor.py` is constructor-injected
with a DiscoveryUseCasePort instead of being subclassed per mode.

execute() has zero task-state decision logic of its own — it just dispatches
on which HandlerResult variant the handler returned. That's the entire point
of HandlerResult being a typed, discriminated union: the domain agent decides
the task's ending state, not a2a_utility.
"""

from __future__ import annotations

from typing import Optional

from a2a.server.agent_execution import AgentExecutor as _NativeAgentExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue

from ...application.dtos import (
    ExtendedRequestContext,
    HandlerAuthRequired,
    HandlerCanceled,
    HandlerCompleted,
    HandlerFailed,
    HandlerInputRequired,
)
from ...application.ports.inbound.agent_handler_port import AgentHandlerPort
from ...application.ports.inbound.on_cancel_port import OnCancelPort
from ..outbound.event_queue_adapter import ExtendedEventQueue


class AgentExecutor(_NativeAgentExecutor):
    def __init__(self, handler: AgentHandlerPort, on_cancel: Optional[OnCancelPort] = None) -> None:
        self._handler = handler
        self._on_cancel = on_cancel

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        ctx = ExtendedRequestContext(context)
        eq = ExtendedEventQueue(context, event_queue)
        await eq.start_work()
        try:
            result = await self._handler(ctx, eq.emit)
        except Exception as e:
            # Unexpected crash, not a business decision — HandlerFailed is
            # for the latter (a clear, agent-authored message).
            await eq.failed(f"Agent error: {e}")
            return

        # Dispatch on the discriminated HandlerResult the handler returned — the
        # agent decides how the task ends, a2a_utility just maps it onto the
        # matching terminal event. A non-HandlerResult return is a programming
        # error in the handler; surface it as a FAILED task with a clear message
        # (the type guard) instead of silently leaving the task un-terminated.
        if isinstance(result, HandlerCompleted):
            await eq.complete(result.parts)
        elif isinstance(result, HandlerFailed):
            await eq.failed(result.message)
        elif isinstance(result, HandlerInputRequired):
            await eq.requires_input(result.message)
        elif isinstance(result, HandlerAuthRequired):
            await eq.requires_auth(result.message)
        elif isinstance(result, HandlerCanceled):
            await eq.cancel(result.message)
        else:
            await eq.failed(
                f"handler returned {type(result).__name__}, expected a HandlerResult "
                "(HandlerCompleted / HandlerFailed / HandlerInputRequired / "
                "HandlerAuthRequired / HandlerCanceled)"
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Reacts to an externally-requested cancellation (a client's cancel
        RPC). Structurally separate from execute()'s HandlerCanceled path (an
        agent deciding on its own to give up) — the framework calls this
        independently, in addition to cancelling the asyncio task running
        execute() (which lets a handler's own try/finally clean up; no new
        plumbing needed for that half)."""
        ctx = ExtendedRequestContext(context)
        eq = ExtendedEventQueue(context, event_queue)
        message = None
        if self._on_cancel is not None:
            try:
                message = (await self._on_cancel(ctx)).message
            except Exception:
                pass  # best-effort cleanup message; cancellation must still complete
        await eq.cancel(message)
