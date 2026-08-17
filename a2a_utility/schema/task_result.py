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

Covers both of A2A's response workflows. Task mode fills `task_id`/`status`/
`artifacts`; message mode — where the agent publishes a single standalone
`Message` and never creates a Task at all — fills only `message`, leaving
`task_id` and `status` empty. `parts()`/`text()` read from whichever was
used, so a caller that only wants the answer doesn't have to know which
workflow the agent chose.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .parts import ExtendedArtifact, ExtendedMessage, ExtendedPart
from .task_state import ExtendedTaskState


class A2ATaskResult(BaseModel):
    task_id: str
    status: ExtendedTaskState = ExtendedTaskState.UNSPECIFIED
    """The task's final state. UNSPECIFIED in message mode, where there is no
    task to have a state."""
    artifacts: list[ExtendedArtifact] = Field(default_factory=list)
    history: list[ExtendedMessage] = Field(default_factory=list)
    message: Optional[ExtendedMessage] = None
    """The standalone reply, when the agent answered in message mode. None in
    task mode — where the answer lives in `artifacts` instead."""
    status_message: Optional[ExtendedMessage] = None
    """The message attached to the last status update, when there was one.

    This is where an agent explains a non-success ending — why it was
    rejected, what input it still needs, what went wrong. Kept separate from
    `artifacts` (which hold the answer) so that `text()` never mixes an
    explanation into the result, and separate from the raised error so a
    non-raising outcome like AUTH_REQUIRED still carries its reason.
    """

    @property
    def status_text(self) -> str:
        """The last status message's text, or "" if there wasn't one."""
        return self.status_message.text() if self.status_message else ""

    @property
    def is_message_mode(self) -> bool:
        """True when the agent answered without creating a Task."""
        return self.message is not None and not self.task_id

    def parts(self) -> list[ExtendedPart]:
        parts = [p for a in self.artifacts for p in a.parts]
        if self.message is not None:
            parts.extend(self.message.parts)
        return parts

    def text(self) -> str:
        return "".join(p.text for p in self.parts() if p.text is not None)
