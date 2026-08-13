"""a2a_utility.schema — the shared wire-format data contract.

Sits above both `a2a_utility.client` and `a2a_utility.server` (neither
subpackage depends on the other; both depend on this) so importing one
doesn't drag in the other's extras. `parts.py` holds the atomic conversion
boundary (`ExtendedPart`/`CustomizedData`) plus the shared streaming callback
type (`PartEmitter`); `task_result.py` holds the client-side read model
(`A2ATaskResult`) built out of those parts.
"""

from .parts import (
    CustomizedData,
    ExtendedArtifact,
    ExtendedMessage,
    ExtendedPart,
    PartEmitter,
    SourceReferenceResponse,
    VercelThinkingResponse,
    as_thinking_emitter,
)
from .task_result import A2ATaskResult

__all__ = [
    "ExtendedPart",
    "ExtendedArtifact",
    "ExtendedMessage",
    "A2ATaskResult",
    "CustomizedData",
    "VercelThinkingResponse",
    "SourceReferenceResponse",
    "PartEmitter",
    "as_thinking_emitter",
]
