"""Client-side error types.

In their own module so `agent.py` and `agent_client.py` can share them
without either importing the other.
"""

from __future__ import annotations

from ..schema import ExtendedTaskState

__all__ = ["A2ACallError", "CALL_FAILED_STATES"]


class A2ACallError(RuntimeError):
    """The remote agent did not produce an answer.

    Raised for FAILED (the agent tried and could not) and REJECTED (the agent
    declined — an authorization failure lands here). Both mean the caller has
    no result, so both raise rather than returning an empty A2ATaskResult.

    `status` carries which of the two it was, because a caller acts on them
    differently: REJECTED means this identity may not do this — retrying is
    pointless, though a different agent or better credentials might work —
    while FAILED means the agent broke and a retry is reasonable.

    AUTH_REQUIRED and INPUT_REQUIRED deliberately do *not* raise: the task is
    paused awaiting something from the caller, not finished, so they come back
    as a normal A2ATaskResult with the reason in `status_message`.
    """

    def __init__(self, message: str, *, status: ExtendedTaskState, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


CALL_FAILED_STATES = frozenset({ExtendedTaskState.FAILED, ExtendedTaskState.REJECTED})
"""Terminal states that mean the caller got no answer."""
