"""Principal — the identity/permission value object attached to a request.

Pure domain model: zero framework imports, operates only on a plain `dict`
(the same dict object native a2a's `ServerCallContext.state` happens to be),
never on any a2a.* type. That's what lets both `application/dtos.py` (reads,
via `ExtendedRequestContext.principal`) and `adapters/inbound/
call_context_builder.py` (writes, via `A2AUtilityCallContextBuilder`) depend
on this module without either depending on the other — the dependency
direction stays adapters -> application -> domain, never reversed.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

_PRINCIPAL_KEY = "principal"


class Principal(BaseModel):
    user_id: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    tenant_id: Optional[str] = None
    token: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


def read_principal(state: dict) -> Principal:
    """Reads the Principal a context builder attached to this request's state."""
    principal = state.get(_PRINCIPAL_KEY)
    return principal if isinstance(principal, Principal) else Principal()


def write_principal(state: dict, principal: Principal) -> None:
    """Attaches a Principal to a request's state dict."""
    state[_PRINCIPAL_KEY] = principal
