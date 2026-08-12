"""Domain model for a streamed chat turn.

Kept deliberately free of any a2a-sdk / LLM-SDK types: the application layer
speaks only in these objects. An LLM's output is a sequence of StreamChunks,
each tagged as either the model's private reasoning (THINKING) or the actual
answer text shown to the caller (ANSWER). How a given provider surfaces
reasoning (OpenAI `reasoning_content`, Claude `<thinking>` blocks, a domain
agent's `emit_thought` callback) is an adapter concern, not a domain one.
"""

from dataclasses import dataclass
from enum import Enum


class StreamPhase(str, Enum):
    THINKING = "thinking"
    ANSWER = "answer"


@dataclass(frozen=True)
class StreamChunk:
    phase: StreamPhase
    text: str

    @classmethod
    def thinking(cls, text: str) -> "StreamChunk":
        return cls(phase=StreamPhase.THINKING, text=text)

    @classmethod
    def answer(cls, text: str) -> "StreamChunk":
        return cls(phase=StreamPhase.ANSWER, text=text)
