"""Inbound port: the contract a domain agent implements.

Deliberately a plain `Callable` type, not a class-based Protocol requiring
inheritance — this is satisfied equally by a bare async function (stateless
agents) or an object with `__call__` (agents that want to hold construction
state, e.g. a persistent client/loop instance), with no a2a_utility base
class in the picture either way. `adapters/inbound/agent_executor.py` takes
one of these via constructor injection (mirroring how
`adapters/inbound/discovery_agent_executor.py` is injected with a
DiscoveryUseCasePort) rather than a domain agent subclassing anything.

Returns None, and takes `ExtendedEventQueue` as its second argument — this
signature is the typed mirror of native `AgentExecutor.execute(context,
event_queue) -> None` exactly: native hands the implementer the raw
`EventQueue`, not a pre-built `TaskUpdater` — building one is the
implementer's own choice. A domain agent that wants that convenience builds
`ExtendedTaskUpdater(context, event_queue)` itself, as its own first line,
the same way a native implementation would build a `TaskUpdater` itself. See
`adapters/outbound/task_updater_adapter.py`'s module docstring for the full
reasoning (including why this isn't a HandlerResult-style discriminated
union, and why the handoff isn't a pre-built `ExtendedTaskUpdater`).

The second parameter's type (`ExtendedEventQueue`, from `adapters/outbound`)
is the one deliberate exception to this port living purely on domain-layer
types — the whole point of this port is handing the domain agent the
concrete typed object it drives, so importing that adapters-layer type here
is intentional, not an oversight.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ...dtos import ExtendedRequestContext
from ....adapters.outbound.event_queue_adapter import ExtendedEventQueue

AgentHandlerPort = Callable[[ExtendedRequestContext, ExtendedEventQueue], Awaitable[None]]
