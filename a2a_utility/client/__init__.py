"""a2a_utility.client — framework-agnostic outbound A2A client.

Public surface:
  - call_agent: SendStreamingMessage to any A2A agent, returns its final answer.
  - A2ACallError / ThoughtEmitter: call_agent's error type and thinking-process
    callback contract.
  - DiscoveryClient: cached directory lookups against an a2a_utility DISCOVERY
    node (list_agents/resolve/search).
"""

from .agent_client import A2ACallError, ThoughtEmitter, call_agent
from .discovery_client import DiscoveryClient

__all__ = ["call_agent", "A2ACallError", "ThoughtEmitter", "DiscoveryClient"]
