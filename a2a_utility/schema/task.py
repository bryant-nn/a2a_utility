"""ExtendedTask — the read model for a task the server already knows about.

This is what `ExtendedRequestContext.current_task` hands a handler: the task
as it stands *right now*, before this execute() call has done anything. It is
deliberately read-only — a handler changes a task by calling methods on its
`ExtendedTaskUpdater`, never by mutating a task object.

Its main use is resumption. After `requires_input()`/`requires_auth()`, the
framework re-invokes the agent with a **new** execute() call carrying the same
task id (it does not resume the old coroutine), so the only way to pick up
where you left off is to read the prior state and history off this object.
See `ExtendedRequestContext.is_resuming`.

Distinct from `A2ATaskResult` (`task_result.py`), which is the *client*-side
view of a finished round trip. Same underlying protobuf, different question:
this one answers "what has happened to this task so far?" from inside the
agent; that one answers "what did I get back?" from outside it.
"""

from __future__ import annotations

from typing import Optional

from a2a.types import Task as _ProtoTask
from pydantic import BaseModel, Field

from .parts import ExtendedArtifact, ExtendedMessage
from .task_state import ExtendedTaskState

__all__ = ["ExtendedTask"]


class ExtendedTask(BaseModel):
    id: str
    context_id: str
    state: ExtendedTaskState = ExtendedTaskState.UNSPECIFIED
    status_message: Optional[ExtendedMessage] = None
    """The message attached to the task's current status, if the last status
    update carried one."""
    artifacts: list[ExtendedArtifact] = Field(default_factory=list)
    history: list[ExtendedMessage] = Field(default_factory=list)

    @classmethod
    def from_protobuf(cls, proto: _ProtoTask) -> "ExtendedTask":
        status_message = None
        if proto.status.HasField("message"):
            status_message = ExtendedMessage.from_protobuf(proto.status.message)

        return cls(
            id=proto.id,
            context_id=proto.context_id,
            state=ExtendedTaskState.from_proto(proto.status.state),
            status_message=status_message,
            artifacts=[ExtendedArtifact.from_protobuf(a) for a in proto.artifacts],
            history=[ExtendedMessage.from_protobuf(m) for m in proto.history],
        )

    def parts(self):
        """Every part across every artifact — the task's output so far."""
        return [p for a in self.artifacts for p in a.parts]

    def text(self) -> str:
        """The concatenated text of the task's artifacts."""
        return "".join(p.text for p in self.parts() if p.text is not None)
