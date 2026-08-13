"""Inbound port: the optional contract a domain agent implements to react to
an externally-requested cancellation (a client's cancel RPC arriving mid-task
— a structurally different control-flow direction than anything
AgentHandlerPort's `handle()` returns, so it's a separate, independently
optional injectable rather than folded into that port).

Most domain agents don't need this — a2a_utility's default AgentExecutor.
cancel() marks the task CANCELED with no custom message. Provide one only if
an agent wants to record a specific cleanup message. Real resource cleanup
(closing connections, etc.) doesn't need this port at all: the asyncio task
running the handler is cancelled by the framework independently, so a
handler's own try/finally already runs — this port is purely for the
optional final message on the wire.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ...dtos import CancelResult, ExtendedRequestContext

OnCancelPort = Callable[[ExtendedRequestContext], Awaitable[CancelResult]]
