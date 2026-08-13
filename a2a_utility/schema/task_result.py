"""A2ATaskResult — the client-side read model for a completed (or in-flight)
A2A task: what a caller actually received after a round trip.

Deliberately separate from `parts.py`'s `ExtendedPart`/`ExtendedArtifact`
building blocks: those are the vocabulary an agent *produces* a response
with (task_id/status/history are meaningless to a handler mid-execution — the
real task_id/context_id are already owned by the TaskUpdater the server side
built, status is fully expressed by return-vs-raise, and history is an input
concept, not something a single reply authors) — this is the vocabulary a
*caller* reads a full result back with. Keeping them distinct means a handler
never has to fill in dead fields just to satisfy this type's shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .parts import ExtendedArtifact, ExtendedMessage, ExtendedPart


class A2ATaskResult(BaseModel):
    task_id: str
    status: str
    artifacts: list[ExtendedArtifact] = Field(default_factory=list)
    history: list[ExtendedMessage] = Field(default_factory=list)

    def parts(self) -> list[ExtendedPart]:
        return [p for a in self.artifacts for p in a.parts]

    def text(self) -> str:
        return "".join(p.text for p in self.parts() if p.text is not None)
