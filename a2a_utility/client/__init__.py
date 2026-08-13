"""a2a_utility.client — outbound A2A client wrapping the native a2a.client.

Public surface:
  - call_agent: send a message, return the answer text (concatenated text parts).
  - call_agent_parts: same call, return the full list of typed ExtendedParts.
  - call_agent_result: same call, return a typed A2ATaskResult (id, status, artifacts).
  - A2ACallError / ThoughtEmitter: error type and thinking-process callback contract.
  - DiscoveryClient: cached directory lookups against an a2a_utility DISCOVERY node.

The typed data models (ExtendedPart, A2ATaskResult, …) live in a2a_utility.types.
"""

from .agent_client import (
    A2ACallError,
    ThoughtEmitter,
    call_agent,
    call_agent_parts,
    call_agent_result,
)
from .discovery_client import DiscoveryClient

__all__ = [
    "call_agent",
    "call_agent_parts",
    "call_agent_result",
    "A2ACallError",
    "ThoughtEmitter",
    "DiscoveryClient",
]
