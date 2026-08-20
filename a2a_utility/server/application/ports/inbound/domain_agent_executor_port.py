"""Inbound port: the contract a domain agent implements.

Unlike the rest of this package's ports (`DiscoveryUseCasePort` is a
`Protocol`, no inheritance required), this one is a real ABC that a domain
agent subclasses — a deliberate departure, not an oversight. The previous
shape (`AgentHandlerPort`, a plain callable driving `ExtendedTaskUpdater`
imperatively) argued against forcing inheritance; this replaces it entirely
because the declarative shape needs somewhere to hang a default `cancel()`
implementation, and because that's the calling convention this was built to
match. See `docs/DESIGN.md` for the fuller reasoning.

`execute()` is an async generator: yield `domain.models.task_events.
TaskEvent` values to drive the task, `return` to complete it, `raise` to fail
it. `adapters/inbound/agent_executor.py` is the only code that reads what's
yielded — nothing here touches a2a.* or even ExtendedTaskUpdater/
ExtendedEventQueue (neither exists anymore; the adapter drives native
`TaskUpdater`/`EventQueue` directly).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from ...dtos import ExtendedRequestContext
from ....domain.models.task_events import TaskEvent
from .....schema import MessageLike

__all__ = ["DomainAgentExecutorPort"]


class DomainAgentExecutorPort(ABC):
    @abstractmethod
    def execute(self, context: ExtendedRequestContext) -> AsyncIterator[TaskEvent]:
        """Yield TaskEvents to drive the task; return to complete it, raise
        to fail it with the exception's message preserved."""
        ...

    async def cancel(self, context: ExtendedRequestContext) -> Optional[MessageLike]:
        """Reacts to an externally-requested cancellation (a client's cancel
        RPC). The task is marked CANCELED regardless of what this returns —
        the return value only supplies an optional message attached to that
        status. Most agents don't need to override this: the asyncio task
        running `execute()` is cancelled by the framework independently, so
        a handler's own try/finally already runs for real cleanup."""
        return None
