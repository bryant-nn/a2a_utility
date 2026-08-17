"""UserContext — who is calling, and what they're allowed to do.

Pure domain model: zero framework imports, operating only on plain values and
a plain `dict` (which happens to be what native a2a's
`ServerCallContext.state` is, but this module never names that type). That's
what lets `application/` read it and `adapters/inbound/` write it without
either depending on the other — the dependency direction stays adapters ->
application -> domain, never reversed.

Server-local only. A UserContext is never serialized onto the A2A wire: it is
derived from the request's credentials on arrival and discarded when the
request ends. The `token` field is the one thing that travels, and only
deliberately — see `ForwardedToken` on the client side, for the common
multi-agent case where a root agent must pass the caller's identity to the
domain agents it fans out to.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

_USER_CONTEXT_KEY = "user_context"


class PermissionDenied(Exception):
    """The caller is authenticated but not allowed to do this.

    Raised by `UserContext.require()`. `AgentExecutor` catches it separately
    from other exceptions and ends the task REJECTED rather than FAILED —
    "you may not" is a different answer than "it broke", and a caller acts on
    them differently.
    """

    def __init__(self, permission: str, user: Optional["UserContext"] = None) -> None:
        self.permission = permission
        self.user = user
        who = user.user_id if user and user.user_id else "unauthenticated caller"
        super().__init__(f"{who} is not permitted to {permission!r}")


class UserContext(BaseModel):
    """The identity and entitlements attached to one request."""

    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    """Scopes carried by the credential itself (e.g. a JWT's `scope` claim)."""
    permissions: set[str] = Field(default_factory=set)
    """Entitlements resolved from the permission service. Separate from
    `scopes` because they answer different questions: a scope is what the
    token was issued for, a permission is what this user may currently do."""
    claims: dict[str, Any] = Field(default_factory=dict)
    """The verified claims the credential carried, for anything this model
    doesn't name explicitly."""
    token: Optional[str] = None
    """The raw credential, kept so it can be forwarded to downstream agents."""

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

    def has_permission(self, permission: str) -> bool:
        """Whether this user holds `permission`.

        Supports trailing-wildcard grants: holding `"tool:*"` satisfies a
        check for `"tool:get_weather"`, and `"*"` satisfies anything. Matching
        is on the literal string otherwise — no implicit hierarchy, so
        `"tool:a"` never implies `"tool:a:b"` unless a wildcard says so.
        """
        if permission in self.permissions:
            return True
        return any(
            granted.endswith("*") and permission.startswith(granted[:-1])
            for granted in self.permissions
        )

    def require(self, permission: str) -> None:
        """Assert `permission`, or raise PermissionDenied.

        The tool-level half of authorization: an agent-level check runs in the
        GateKeeper before the handler starts, but which tools a request will
        reach isn't knowable until it runs, so a handler calls this at the
        point of use.

        Raises:
            PermissionDenied: ends the task REJECTED if it escapes the handler.
        """
        if not self.has_permission(permission):
            raise PermissionDenied(permission, self)

    def has_role(self, role: str) -> bool:
        return role in self.roles


def read_user_context(state: dict) -> UserContext:
    """The UserContext attached to this request, or an empty (unauthenticated)
    one if the gate never ran."""
    user = state.get(_USER_CONTEXT_KEY)
    return user if isinstance(user, UserContext) else UserContext()


def write_user_context(state: dict, user: UserContext) -> None:
    state[_USER_CONTEXT_KEY] = user
