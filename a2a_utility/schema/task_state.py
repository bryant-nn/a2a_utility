"""ExtendedTaskState — the task lifecycle states, as a plain Python enum.

Native `a2a.types.TaskState` is a protobuf enum: its members are bare ints
(`TaskState.TASK_STATE_WORKING == 2`), it has no `.value` that reads as
anything, and comparing or logging one gives you a number. Exposing it
directly would also mean any handler wanting to branch on state — or pass a
state to `update_status()` — has to `from a2a.types import TaskState`, which
is exactly the leak this package exists to close.

This mirrors it 1:1 as a `str` enum, so states print and serialize as
themselves (`"working"`), compare as strings, and drop into log lines and
JSON without conversion. `to_proto()`/`from_proto()` are the only crossing
points; `tests/test_sdk_contract.py` asserts the native enum still has every
member mapped here.
"""

from __future__ import annotations

from enum import Enum

from a2a.types import TaskState as _ProtoTaskState

__all__ = ["ExtendedTaskState"]


class ExtendedTaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    INPUT_REQUIRED = "input_required"
    AUTH_REQUIRED = "auth_required"
    UNSPECIFIED = "unspecified"

    def to_proto(self) -> int:
        """The native protobuf enum value for this state."""
        return _TO_PROTO[self]

    @classmethod
    def from_proto(cls, state: int) -> "ExtendedTaskState":
        """The ExtendedTaskState for a native protobuf enum value.

        Unknown values (a state added by a newer SDK than this package knows
        about) map to UNSPECIFIED rather than raising — a task in an
        unrecognized state is still a task worth reporting.
        """
        return _FROM_PROTO.get(state, cls.UNSPECIFIED)

    @property
    def is_terminal(self) -> bool:
        """True for states the protocol treats as final: no further status
        update is allowed after one of these (native `TaskUpdater` raises
        RuntimeError if you try)."""
        return self in _TERMINAL

    @property
    def is_interrupted(self) -> bool:
        """True for states where the task is paused waiting on the caller.
        The framework resumes it with a *new* execute() call, not by resuming
        the old coroutine — see ExtendedRequestContext.is_resuming."""
        return self in _INTERRUPTED


_TO_PROTO: dict[ExtendedTaskState, int] = {
    ExtendedTaskState.UNSPECIFIED: _ProtoTaskState.TASK_STATE_UNSPECIFIED,
    ExtendedTaskState.SUBMITTED: _ProtoTaskState.TASK_STATE_SUBMITTED,
    ExtendedTaskState.WORKING: _ProtoTaskState.TASK_STATE_WORKING,
    ExtendedTaskState.COMPLETED: _ProtoTaskState.TASK_STATE_COMPLETED,
    ExtendedTaskState.FAILED: _ProtoTaskState.TASK_STATE_FAILED,
    ExtendedTaskState.CANCELED: _ProtoTaskState.TASK_STATE_CANCELED,
    ExtendedTaskState.REJECTED: _ProtoTaskState.TASK_STATE_REJECTED,
    ExtendedTaskState.INPUT_REQUIRED: _ProtoTaskState.TASK_STATE_INPUT_REQUIRED,
    ExtendedTaskState.AUTH_REQUIRED: _ProtoTaskState.TASK_STATE_AUTH_REQUIRED,
}

_FROM_PROTO: dict[int, ExtendedTaskState] = {v: k for k, v in _TO_PROTO.items()}

_TERMINAL = frozenset(
    {
        ExtendedTaskState.COMPLETED,
        ExtendedTaskState.FAILED,
        ExtendedTaskState.CANCELED,
        ExtendedTaskState.REJECTED,
    }
)

_INTERRUPTED = frozenset(
    {
        ExtendedTaskState.INPUT_REQUIRED,
        ExtendedTaskState.AUTH_REQUIRED,
    }
)
