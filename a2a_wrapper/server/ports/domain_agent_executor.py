from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Optional

from a2a_wrapper.events import StreamEvent
from a2a_wrapper.types import DomainContext


class DomainAgentExecutorPort(ABC):
    """
    Port interface for agent developers.

    Contract:
        - yield TextChunk      → streaming text update (state=working)
        - yield StatusMessage   → structured status push (state=working)
        - yield InputRequired   → pause and request more input from client
        - yield AuthRequired    → pause and request authentication/authorization
        - yield Rejected        → reject the task (e.g., validation or safety block)
        - yield ArtifactResult  → final or intermediate output artifact
        - return                → framework calls complete()
        - raise                 → framework calls failed()
    """

    @abstractmethod
    def execute(
        self,
        context: DomainContext,
    ) -> AsyncIterator[StreamEvent]:
        """Yield StreamEvents to drive the task.

        Args:
            context: the current turn's input and (if resuming) prior state.

        Returns:
            An async generator of StreamEvent. Returning normally completes
            the task; raising fails it.
        """
        ...

    async def cancel(self, context: DomainContext) -> Optional[str]:
        """React to an externally-requested cancel RPC.

        Args:
            context: the task's context at the time of cancellation.

        Returns:
            An optional message to attach to the CANCELED status. The task
            is marked CANCELED regardless of what (or whether) this returns
            — an exception here is also caught, not fatal. Real resource
            cleanup doesn't need this override at all: the asyncio task
            running execute() is cancelled by the framework independently.
        """
        return None
