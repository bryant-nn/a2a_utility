"""Application-layer DTOs: the clean objects an executor callback sees.

adjust.md §step-1: `UserContext` (identity, reserved for a future auth
middleware) and `Task` (the inbound request: id, contextId, the user's
message). Neither depends on a2a-sdk — the inbound adapter maps the wire
Request onto these before calling the developer's callback.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

# An async hook the executor may call to stream a live "thinking" line back to
# the client mid-flight (surfaces as an A2A WORKING status message). Injected by
# the inbound middleware onto UserContext so the callback signature stays
# (Task, UserContext) -> list[Part].
ThoughtEmitter = Callable[[str], Awaitable[None]]


@dataclass
class UserContext:
    """Mock identity/context assembled by the inbound middleware.

    Fields are reserved for a real auth/tenant middleware to fill later; today
    the middleware stubs them. `emit_thought` is the request-scoped live-stream
    hook (None if the transport can't stream).
    """

    user_id: Optional[str] = None
    roles: list[str] = field(default_factory=list)
    tenant_id: Optional[str] = None
    token: Optional[str] = None
    emit_thought: Optional[ThoughtEmitter] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """The inbound request, normalized off the A2A wire message."""

    id: str
    context_id: Optional[str]
    message: str                       # the user's natural-language query text
    metadata: dict[str, Any] = field(default_factory=dict)
