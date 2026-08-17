"""Inbound port: the optional contract a domain agent implements to react to
an externally-requested cancellation (a client's cancel RPC arriving mid-task
— a structurally different control-flow direction than anything
AgentHandlerPort's `handle()` drives, so it's a separate, independently
optional injectable rather than folded into that port).

Most domain agents don't need this — a2a_utility's default `AgentExecutor.
cancel()` builds its own `ExtendedTaskUpdater` and marks the task CANCELED
with no custom message if `on_cancel` isn't given (or raised). Provide one
only if an agent wants to record a specific cleanup message — build
`ExtendedTaskUpdater(context, event_queue)` inside it and call
`.cancel(message=...)`. Real resource cleanup (closing connections, etc.)
doesn't need this port at all: the asyncio task running `execute()` is
cancelled by the framework independently, so a handler's own try/finally
already runs — this port is purely for the optional final message on the
wire.

Mirrors native `AgentExecutor.cancel(context, event_queue) -> None` exactly
— no declarative return value (no CancelResult), same as AgentHandlerPort's
own move away from HandlerResult, and takes `ExtendedEventQueue` for the same
reason AgentHandlerPort does (see that module's docstring).
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ...dtos import ExtendedRequestContext
from ....adapters.outbound.event_queue_adapter import ExtendedEventQueue

OnCancelPort = Callable[[ExtendedRequestContext, ExtendedEventQueue], Awaitable[None]]
