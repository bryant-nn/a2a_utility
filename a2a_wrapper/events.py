# a2a_wrapper/events.py
from dataclasses import dataclass, field
from typing import Any

from a2a_wrapper.types import ExtendedPart


class StreamEvent:
    """Base class for all stream events yielded by domain executors."""

    pass


@dataclass
class TextChunk(StreamEvent):
    """A simple text chunk, usually for streaming text responses."""

    text: str


@dataclass
class StatusMessage(StreamEvent):
    """A status update message containing rich parts (can be thinking process, progress, etc.)."""

    parts: list[ExtendedPart]


@dataclass
class InputRequired(StreamEvent):
    """Signal that more input is needed from the client."""

    parts: list[ExtendedPart]


@dataclass
class AuthRequired(StreamEvent):
    """Signal that authentication/authorization is required to proceed."""

    parts: list[ExtendedPart]


@dataclass
class Rejected(StreamEvent):
    """Signal that the task has been rejected (e.g., validation failed, safety block)."""

    parts: list[ExtendedPart]


@dataclass
class ArtifactResult(StreamEvent):
    """A final or intermediate artifact produced by the executor."""

    parts: list[ExtendedPart]
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    artifact_id: str | None = None
    append: bool = False
    last_chunk: bool = True
