"""Outbound port: turning a raw credential into verified claims.

Step 2 of the gate. Deliberately narrow — one method, one direction — so that
swapping the company's token format (JWT today, something else later) touches
one adapter and nothing else. The GateKeeper never learns what a token *is*.

`InvalidToken` is the only failure mode the gate distinguishes: expired,
malformed, wrong issuer, bad signature all mean the same thing to it (the
caller must re-authenticate), so an adapter should raise it for all of them
rather than leaking its library's exception types upward.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class InvalidToken(Exception):
    """The credential could not be verified — malformed, expired, wrongly
    signed, or issued by someone we don't trust."""


@runtime_checkable
class TokenVerifierPort(Protocol):
    async def verify(self, token: str) -> dict[str, Any]:
        """Verify `token` and return its claims.

        Verification means checking the signature and any issuer/audience
        constraints, not merely decoding. An implementation that only decodes
        would make the whole gate decorative.

        Raises:
            InvalidToken: the credential is not usable.
        """
        ...
