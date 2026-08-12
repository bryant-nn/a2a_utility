"""HandlerLLMAdapter — the pluggable seam that lets ANY framework be an agent.

Each domain agent in this repo already exposes an async handler of the shape:

    async def handle(text: str, emit_thought: ThoughtEmitter) -> str

where calling `emit_thought(text)` streams intermediate reasoning and the return
value is the final answer. calculate_agent uses nanobot, weather_agent uses
LangChain deepagents, web_search_agent uses google-adk, fake_agent returns a
canned reply — none of them are OpenAI. This adapter implements the LLMServicePort
by driving such a handler and translating it into the domain's StreamChunk stream:
every emit_thought(...) becomes a THINKING chunk, the returned string becomes the
final ANSWER chunk.

The handler runs concurrently and feeds a queue so thoughts surface as they
happen (not buffered until the handler returns).
"""

import asyncio
import contextlib
from typing import AsyncIterator, Awaitable, Callable

from ...application.ports.outbound.llm_port import LLMServicePort
from ...domain.models.chat_message import StreamChunk

ThoughtEmitter = Callable[[str], Awaitable[None]]
Handler = Callable[[str, ThoughtEmitter], Awaitable[str]]

_DONE = object()


class HandlerLLMAdapter(LLMServicePort):
    def __init__(self, handler: Handler) -> None:
        self._handler = handler

    async def stream(self, query: str) -> AsyncIterator[StreamChunk]:
        queue: asyncio.Queue = asyncio.Queue()

        async def emit_thought(text: str) -> None:
            await queue.put(StreamChunk.thinking(text))

        async def run() -> None:
            try:
                result = await self._handler(query, emit_thought)
                await queue.put(StreamChunk.answer(result))
            except Exception as exc:  # surfaced to the consumer below
                await queue.put(exc)
            finally:
                await queue.put(_DONE)

        worker = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            # If the consumer stops early (client disconnect), don't leak the task.
            if not worker.done():
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker

    async def aclose(self) -> None:
        # The wrapped handler owns its own resources; nothing to release here.
        return None
