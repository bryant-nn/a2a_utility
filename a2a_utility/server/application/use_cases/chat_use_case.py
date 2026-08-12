"""ChatUseCase: the thinking-aware chat pipeline.

Today it is a thin pass-through over the LLM port, but it is the seam where
cross-cutting policy (guardrails, prompt shaping, thinking redaction, retries)
would live without any inbound/outbound adapter needing to change.
"""

from typing import AsyncIterator

from ...domain.models.chat_message import StreamChunk
from ..ports.inbound.chat_use_case_port import ChatUseCasePort
from ..ports.outbound.llm_port import LLMServicePort


class ChatUseCase(ChatUseCasePort):
    def __init__(self, llm_service: LLMServicePort) -> None:
        self._llm = llm_service

    async def handle(self, query: str) -> AsyncIterator[StreamChunk]:
        async for chunk in self._llm.stream(query):
            yield chunk
