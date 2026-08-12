"""Outbound port: the LLM (or any text-in/text-out worker) the app talks to.

`stream` MUST distinguish the model's private reasoning from its answer by
tagging each StreamChunk's phase. Implementations:
  - HandlerLLMAdapter: wraps a domain agent's own async handler.
  - OpenAILLMAdapter:   streams an OpenAI-compatible endpoint, splitting
                        `reasoning_content` (THINKING) from `content` (ANSWER).
"""

from typing import AsyncIterator, Protocol, runtime_checkable

from ....domain.models.chat_message import StreamChunk


@runtime_checkable
class LLMServicePort(Protocol):
    def stream(self, query: str) -> AsyncIterator[StreamChunk]:
        """Yield THINKING then ANSWER chunks for `query`."""
        ...

    async def aclose(self) -> None:
        """Release any held resources (network clients, etc.)."""
        ...
