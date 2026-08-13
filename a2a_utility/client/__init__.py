"""a2a_utility.client — framework-agnostic outbound A2A client.

Public surface:
  - call_agent: SendStreamingMessage to any A2A agent, returns its final answer text.
  - call_agent_parts: same call, but returns the full list of Typed Parts.
  - parts_to_text / Part: Typed-Part helpers mirroring the server's Data Contract.
  - A2ACallError / ThoughtEmitter: call_agent's error type and thinking-process
    callback contract.
  - DiscoveryClient: cached directory lookups against an a2a_utility DISCOVERY
    node (list_agents/resolve/search).
"""

from .agent_client import (
    A2ACallError,
    Part,
    ThoughtEmitter,
    call_agent,
    call_agent_parts,
    parts_to_text,
)
from .discovery_client import DiscoveryClient

__all__ = [
    "call_agent",
    "call_agent_parts",
    "parts_to_text",
    "Part",
    "A2ACallError",
    "ThoughtEmitter",
    "DiscoveryClient",
]
