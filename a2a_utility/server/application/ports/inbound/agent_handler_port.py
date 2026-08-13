"""Inbound port: the contract a domain agent implements.

Deliberately a plain `Callable` type, not a class-based Protocol requiring
inheritance — this is satisfied equally by a bare async function (stateless
agents) or an object with `__call__` (agents that want to hold construction
state, e.g. a persistent client/loop instance), with no a2a_utility base
class in the picture either way. `adapters/inbound/agent_executor.py` takes
one of these via constructor injection (mirroring how
`adapters/inbound/discovery_agent_executor.py` is injected with a
DiscoveryUseCasePort) rather than a domain agent subclassing anything.

Returns a HandlerResult (a typed, discriminated union — HandlerCompleted /
HandlerFailed / HandlerInputRequired / HandlerAuthRequired / HandlerCanceled)
rather than a bare list[ExtendedPart]: the task-ending state is the domain
agent's own explicit decision, not something a2a_utility infers or that a
handler signals via raising an exception.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from .....schema import PartEmitter
from ...dtos import ExtendedRequestContext, HandlerResult

AgentHandlerPort = Callable[[ExtendedRequestContext, PartEmitter], Awaitable[HandlerResult]]
