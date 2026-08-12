"""Inbound port: what an A2A inbound adapter is allowed to ask of chat."""

from typing import AsyncIterator, Protocol, runtime_checkable

from ....domain.models.chat_message import StreamChunk


@runtime_checkable
class ChatUseCasePort(Protocol):
    def handle(self, query: str) -> AsyncIterator[StreamChunk]:
        """Stream the response to `query` as THINKING/ANSWER chunks."""
        ...
