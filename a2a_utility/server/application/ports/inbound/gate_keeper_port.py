"""Inbound port: the gate `AgentExecutor` consults before running a handler.

A Protocol rather than a base class, for the same reason `AgentHandlerPort`
is a plain Callable: satisfying it should not require inheriting from
a2a_utility. A team with an authorization model that doesn't decompose into
"verify token, fetch permissions, check one string" can implement this
directly and pass it to `create_app(gate_keeper=...)` without touching the
default `GateKeeper` at all.
"""

from __future__ import annotations

from typing import Mapping, Optional, Protocol, runtime_checkable

from ....domain.models.auth_decision import AuthDecision


@runtime_checkable
class GateKeeperPort(Protocol):
    async def authorize(
        self,
        headers: Mapping[str, str],
        required_permission: Optional[str] = None,
    ) -> AuthDecision:
        """Decide whether this request may run.

        Args:
          headers: the request's HTTP headers, lowercased keys. Everything the
            gate needs comes from here — it is deliberately not given the
            message body, so authorization can never depend on what the caller
            is asking for, only on who they are.
          required_permission: the agent-level permission to check, if the
            server was configured with one.

        Returns an AuthDecision rather than raising, because all three
        outcomes are normal and each maps to a different task state.
        """
        ...
