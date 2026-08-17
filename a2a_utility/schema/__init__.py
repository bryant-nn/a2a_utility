"""a2a_utility.schema — the shared wire-format data contract.

Sits above both `a2a_utility.client` and `a2a_utility.server` (neither
subpackage depends on the other; both depend on this) so importing one
doesn't drag in the other's extras.

  parts.py        the atomic conversion boundary — ExtendedPart /
                  CustomizedData / ExtendedArtifact / ExtendedMessage /
                  MessageRole — plus the shared streaming callback type
                  (PartEmitter)
  task_state.py   ExtendedTaskState: the task lifecycle, as a str enum
  task.py         ExtendedTask: the server-side read model of a task in flight
  task_result.py  A2ATaskResult: the client-side read model of a round trip

Together these are the reason a domain agent never writes `import a2a`: every
protobuf type the A2A protocol puts on the wire has a typed counterpart here,
converting at exactly one boundary in each direction.
"""

from .parts import (
    CustomizedData,
    ExtendedArtifact,
    ExtendedMessage,
    ExtendedPart,
    MessageRole,
    PartEmitter,
    SourceReferenceResponse,
    VercelThinkingResponse,
    as_thinking_emitter,
)
from .task import ExtendedTask
from .task_result import A2ATaskResult
from .task_state import ExtendedTaskState

__all__ = [
    "ExtendedPart",
    "ExtendedArtifact",
    "ExtendedMessage",
    "MessageRole",
    "ExtendedTask",
    "ExtendedTaskState",
    "A2ATaskResult",
    "CustomizedData",
    "VercelThinkingResponse",
    "SourceReferenceResponse",
    "PartEmitter",
    "as_thinking_emitter",
]
