"""a2a_utility.client — outbound A2A client wrapping the native a2a.client.

Public surface:
  - ExtendedAgentClient: a reusable handle on one agent — keeps the connection
    and the resolved agent card, and adds get_task / cancel_task / subscribe.
    Use this from anything that calls the same agent more than once.
  - call_agent: send a message once, return the answer text.
  - call_agent_parts: same call, return the full list of typed ExtendedParts.
  - call_agent_result: same call, return a typed A2ATaskResult (id, status,
    artifacts, and the final status message).
  - A2ACallError: raised when the remote task ended FAILED or REJECTED; carries
    `.status` so a caller can tell "not permitted" from "broke".
  - Credentials / CredentialProvider / StaticToken: what to authenticate with.
    Pass `credentials=<token>` to forward an identity to a downstream agent
    on the caller's behalf.
  - PartEmitter: the live-streaming callback contract — pass one as `emit=`
    to a call_agent* function or ExtendedAgentClient.send*.
  - DiscoveryClient: cached directory lookups against an a2a_utility DISCOVERY node.

The typed data models (ExtendedPart, A2ATaskResult, …) live in a2a_utility.schema.
"""

from .agent import ExtendedAgentClient
from .agent_client import (
    call_agent,
    call_agent_parts,
    call_agent_result,
)
from .credentials import CredentialProvider, Credentials, StaticToken
from .discovery_client import DiscoveryClient
from .errors import A2ACallError
from ..schema import PartEmitter

__all__ = [
    "ExtendedAgentClient",
    "call_agent",
    "call_agent_parts",
    "call_agent_result",
    "A2ACallError",
    "Credentials",
    "CredentialProvider",
    "StaticToken",
    "PartEmitter",
    "DiscoveryClient",
]
