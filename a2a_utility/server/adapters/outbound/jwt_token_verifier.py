"""JwtTokenVerifier — the default TokenVerifierPort: verify a JWT against JWKS.

Requires PyJWT, which is an optional extra (`pip install a2a-utility[auth]`)
rather than a base dependency: a deployment behind an authenticating gateway,
or one using mTLS, has no use for it, and the base install shouldn't carry a
crypto stack those deployments will never call.

What it verifies, and why each matters:

  - **signature**, against the issuer's published JWKS. Without this the
    token is just a claim the caller made about themselves.
  - **expiry**, so a leaked token stops working.
  - **issuer**, so a token minted by some other (possibly attacker-chosen)
    issuer isn't accepted.
  - **audience**, so a token legitimately issued for a *different* service
    can't be replayed against this one. This is the check most often skipped
    and the one that turns any other internal service's tokens into keys to
    this one.

Signing keys are fetched lazily and cached by PyJWT's own `PyJWKClient`,
which handles kid lookup and refresh on rotation.
"""

from __future__ import annotations

from typing import Any, Optional

from ...application.ports.outbound.token_verifier_port import InvalidToken

__all__ = ["JwtTokenVerifier"]


class JwtTokenVerifier:
    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        algorithms: Optional[list[str]] = None,
        leeway: float = 0.0,
    ) -> None:
        """
        Args:
          jwks_url: the issuer's JWKS endpoint.
          issuer: expected `iss`. Strongly recommended.
          audience: expected `aud` — this service's own identifier. Strongly
            recommended; see the module docstring.
          algorithms: accepted signing algorithms. Defaults to RS256 only.
            Deliberately not "whatever the token says": accepting the token's
            own `alg` is the classic JWT confusion attack.
          leeway: seconds of clock skew tolerated on time-based claims.
        """
        try:
            import jwt
            from jwt import PyJWKClient
        except ImportError as e:  # pragma: no cover - depends on install extras
            raise ImportError(
                "JwtTokenVerifier needs PyJWT. Install it with "
                "'pip install \"a2a-utility[auth]\"', or supply your own "
                "TokenVerifierPort to GateKeeper if your tokens aren't JWTs."
            ) from e

        self._jwt = jwt
        self._jwk_client = PyJWKClient(jwks_url)
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms or ["RS256"]
        self._leeway = leeway

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            return self._jwt.decode(
                token,
                signing_key.key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={
                    "require": ["exp"],
                    "verify_aud": self._audience is not None,
                    "verify_iss": self._issuer is not None,
                },
            )
        except Exception as e:
            # Every PyJWT failure — expired, bad signature, wrong audience,
            # unreachable JWKS — means the same thing to the gate: this
            # credential is not usable. Collapsing them here keeps PyJWT's
            # exception hierarchy out of the application layer. The original
            # is chained for logs.
            raise InvalidToken(str(e) or type(e).__name__) from e
