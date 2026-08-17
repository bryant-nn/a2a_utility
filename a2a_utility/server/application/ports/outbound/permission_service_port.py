"""Outbound port: what a verified subject is allowed to do.

Step 3 of the gate. Separate from token verification because the two answer
different questions and change on different clocks: a token says who you are
and is fixed for its lifetime, while permissions say what you may do and can
change between two requests carrying the same token.

That difference is also why caching belongs at this port and not at the
verifier — see `adapters/outbound/cached_permission_service.py`, which wraps
any implementation of this with a TTL.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class PermissionServicePort(Protocol):
    async def get_permissions(
        self, subject: str, tenant: Optional[str] = None
    ) -> set[str]:
        """The permissions granted to `subject`, optionally scoped to `tenant`.

        Returning an empty set means "authenticated, but entitled to nothing"
        — a valid answer that the gate turns into a Reject. Raising means the
        permission service itself is unavailable, which is a different
        situation and one the gate is deliberately not allowed to paper over;
        see GateKeeper.authorize.
        """
        ...
