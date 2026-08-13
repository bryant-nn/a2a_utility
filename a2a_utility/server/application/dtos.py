"""Application-layer DTOs — the shapes AgentHandlerPort's signature is
expressed in: ExtendedRequestContext (input) and HandlerResult (output).

ExtendedRequestContext wraps the native a2a RequestContext, exposing the
subset of it a handler actually needs. Its constructor takes the native type
(a pragmatic conversion-boundary trade-off, same one `schema/parts.py`'s
ExtendedPart makes with `a2a.types.Part`) — but `.principal` reads
`domain/models/principal.py`'s `read_principal()` directly rather than going
through the adapters-layer `get_principal()` convenience, so this module
never depends on `adapters/`, keeping the dependency direction
adapters -> application -> domain.

HandlerResult is how a domain agent decides its own task-ending state —
a2a_utility (adapters/inbound/agent_executor.py) has zero decision logic of
its own, it just dispatches on which variant came back. This is a pydantic
discriminated union (Literal tag + Field(discriminator=...)), the same
pattern schema/parts.py's CustomizedData already uses, not a new idiom.
Explicitly not exception-based: the task-ending decision is a first-class,
typed return value, not implicit control flow.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from a2a.server.agent_execution import RequestContext
from a2a.types import Message, Task, TaskState
from pydantic import BaseModel, Field

from ..domain.models.principal import Principal, read_principal
from ...schema import ExtendedPart


class ExtendedRequestContext:
    def __init__(self, context: RequestContext) -> None:
        self._context = context

    @property
    def native(self) -> RequestContext:
        """Escape hatch to the raw native RequestContext, for anything not wrapped here."""
        return self._context

    def get_user_input(self, delimiter: str = "\n") -> str:
        return self._context.get_user_input(delimiter)

    @property
    def task_id(self) -> Optional[str]:
        return self._context.task_id

    @property
    def context_id(self) -> Optional[str]:
        return self._context.context_id

    @property
    def message(self) -> Optional[Message]:
        return self._context.message

    @property
    def current_task(self) -> Optional[Task]:
        return self._context.current_task

    @property
    def metadata(self) -> dict[str, Any]:
        return self._context.metadata

    @property
    def principal(self) -> Principal:
        """Auth/tenant/session extension — opt-in, server-local only (never
        serialized onto the A2A wire). See principal.py's three-tier design."""
        return read_principal(self._context.call_context.state)

    @property
    def is_resuming(self) -> bool:
        """True if this execute() call is the framework re-invoking a task
        previously paused via HandlerInputRequired/HandlerAuthRequired — check
        .current_task (native Task, still available as an escape hatch) for
        the prior state/history to pick up where you left off."""
        task = self._context.current_task
        if task is None:
            return False
        return task.status.state in (
            TaskState.TASK_STATE_INPUT_REQUIRED,
            TaskState.TASK_STATE_AUTH_REQUIRED,
        )


# --------------------------------------------------------------------------- #
# HandlerResult — the task-ending decision a handler returns                  #
# --------------------------------------------------------------------------- #
class HandlerCompleted(BaseModel):
    status: Literal["completed"] = "completed"
    parts: list[ExtendedPart]


class HandlerFailed(BaseModel):
    status: Literal["failed"] = "failed"
    message: str


class HandlerInputRequired(BaseModel):
    status: Literal["input_required"] = "input_required"
    message: str


class HandlerAuthRequired(BaseModel):
    status: Literal["auth_required"] = "auth_required"
    message: str


class HandlerCanceled(BaseModel):
    status: Literal["canceled"] = "canceled"
    message: Optional[str] = None


HandlerResult = Annotated[
    Union[HandlerCompleted, HandlerFailed, HandlerInputRequired, HandlerAuthRequired, HandlerCanceled],
    Field(discriminator="status"),
]


class CancelResult(BaseModel):
    """Returned by an OnCancelPort — the optional cleanup message an agent
    wants recorded when the framework tells it an external cancel request
    arrived (see adapters/inbound/agent_executor.py's AgentExecutor.cancel())."""

    message: Optional[str] = None
