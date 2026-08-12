"""OpenAILLMAdapter — streams an OpenAI-compatible chat endpoint.

Implements LLMServicePort by calling POST /chat/completions with stream=true and
parsing the SSE deltas. Reasoning models expose their chain-of-thought in a
`reasoning_content` delta field (DeepSeek-R1, and OpenAI-compatible gateways);
that is emitted as THINKING, while `content` is emitted as ANSWER. Providers
that don't send reasoning simply never produce THINKING chunks — no special case.

Uses httpx directly (already a project dependency) to avoid pulling in the
`openai` SDK. How reasoning is surfaced is fully encapsulated here; the
application layer only ever sees StreamChunks.
"""

import json
from typing import AsyncIterator

import httpx

from ...application.ports.outbound.llm_port import LLMServicePort
from ...domain.models.chat_message import StreamChunk


class OpenAILLMAdapter(LLMServicePort):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str = "You are a helpful assistant.",
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def stream(self, query: str) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": self._model,
            "stream": True,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": query},
            ],
        }
        async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    yield StreamChunk.thinking(reasoning)

                content = delta.get("content")
                if content:
                    yield StreamChunk.answer(content)

    async def aclose(self) -> None:
        await self._client.aclose()
