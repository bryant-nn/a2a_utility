"""Domain agent executor's return vocabulary: what `DomainAgentExecutorPort.
execute()` yields instead of driving a task_updater imperatively.

Pure Python, zero framework dependency — these carry `a2a_utility.schema`
types (`ExtendedPart`/`MessageLike`), never `a2a.*`. `adapters/inbound/
agent_executor.py` is the only place that reads these and turns them into
native `TaskUpdater`/`EventQueue` calls; a domain agent constructs and yields
them and never sees what happens after.

Each maps to exactly one task-lifecycle action:

  Progress          -> WORKING status update, carrying a progress message.
                       Yield as many times as you like.
  PublishArtifact    -> add_artifact(). Fields mirror it completely, including
                       append/last_chunk for streaming one artifact in pieces.
  InputRequired      -> pauses the task (INPUT_REQUIRED). Terminal for this
                       call to execute() — nothing yielded after is read.
  AuthRequired       -> pauses the task (AUTH_REQUIRED). Same as above.
  Rejected           -> ends the task REJECTED. Same as above.
  MessageReply       -> message-mode: publishes a standalone reply and never
                       creates a Task at all. Only valid as the *first and
                       only* event yielded — yielding anything before it
                       would have already created the Task this exists to
                       avoid.

Not yielding anything, or returning normally after some Progress/
PublishArtifact events, marks the task COMPLETED. Raising marks it FAILED,
with the exception's message preserved on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from ....schema import ExtendedPart, MessageLike

__all__ = [
    "Progress",
    "PublishArtifact",
    "InputRequired",
    "AuthRequired",
    "Rejected",
    "MessageReply",
    "TaskEvent",
]


@dataclass(frozen=True)
class Progress:
    """WORKING status update carrying a progress message (thinking, an
    intermediate note, live-streamed text — whatever `message` represents).
    Yield as many of these as the work takes."""

    message: MessageLike


@dataclass(frozen=True)
class PublishArtifact:
    """One artifact chunk. Yield more than once with matching `artifact_id`
    and `append=True` to stream a single artifact in pieces; set
    `last_chunk=True` on the final one."""

    parts: list[ExtendedPart]
    artifact_id: Optional[str] = None
    name: Optional[str] = None
    metadata: Optional[dict] = None
    append: Optional[bool] = None
    last_chunk: Optional[bool] = None
    extensions: Optional[list[str]] = None


@dataclass(frozen=True)
class InputRequired:
    """Pauses the task pending more input from the caller. The framework
    resumes with a *new* execute() call — see
    `ExtendedRequestContext.is_resuming`/`.current_task`."""

    message: Optional[MessageLike] = None


@dataclass(frozen=True)
class AuthRequired:
    """Pauses the task pending credentials from the caller. Resumed the same
    way as InputRequired."""

    message: Optional[MessageLike] = None


@dataclass(frozen=True)
class Rejected:
    """Ends the task REJECTED — the agent declined it outright, as opposed to
    FAILED (attempted and broke)."""

    message: Optional[MessageLike] = None


@dataclass(frozen=True)
class MessageReply:
    """message-mode: a standalone reply with no Task ever created. Must be
    the only event yielded — yield this first, or not at all."""

    message: MessageLike


TaskEvent = Union[Progress, PublishArtifact, InputRequired, AuthRequired, Rejected, MessageReply]
