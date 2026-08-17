"""GateKeeper — the four-step gate every request passes before a handler runs.

    1. take the credential out of the request headers
    2. verify it (signature + issuer/audience), producing claims
    3. resolve the caller's permissions
    4. check the agent-level permission this server requires

Pure orchestration: no a2a, no starlette, no HTTP, no JWT library. Everything
concrete arrives through the two outbound ports, which is what makes the
whole gate unit-testable with two ten-line fakes — see
`tests/server/test_gate_keeper.py`.

Tool-level checks are deliberately *not* here. Which tools a request will
reach isn't knowable before the handler runs, so those happen at the point of
use via `context.user.require("tool:x")`; the resulting `PermissionDenied`
lands in the same REJECTED state this class would have produced.

Where it runs
-------------
`AgentExecutor.execute()` calls this, not a Starlette middleware. Only code
inside execute() can turn a refusal into a *task state* — middleware can
return an HTTP status and nothing more. Rejections therefore reach the caller
as `REJECTED`/`AUTH_REQUIRED` tasks, which is what an A2A client knows how to
interpret. `adapters/inbound/gate_middleware.py` exists as an optional
cheap pre-filter in front of that, not as a replacement for it.
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional

from ...domain.models.auth_decision import Allow, AuthDecision, AuthRequired, Reject
from ...domain.models.user_context import UserContext
from ..ports.outbound.permission_service_port import PermissionServicePort
from ..ports.outbound.token_verifier_port import InvalidToken, TokenVerifierPort

__all__ = ["GateKeeper", "AllowAllGateKeeper", "ClaimNames", "extract_bearer_token"]

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "bearer "


def extract_bearer_token(headers: Mapping[str, str]) -> Optional[str]:
    """The bearer token from an Authorization header, or None.

    Header lookup is case-insensitive on both the name and the `Bearer`
    keyword — RFC 7235 makes the scheme name case-insensitive, and clients do
    vary.
    """
    for name, value in headers.items():
        if name.lower() != "authorization":
            continue
        if value.lower().startswith(_BEARER_PREFIX):
            token = value[len(_BEARER_PREFIX) :].strip()
            return token or None
        return None
    return None


class ClaimNames:
    """Which JWT claims carry which piece of identity.

    Defaults follow common practice (`sub`, `scope`), but internal issuers
    rarely agree on tenant and role claim names, so they're configurable
    rather than assumed.
    """

    def __init__(
        self,
        *,
        subject: str = "sub",
        tenant: str = "tenant_id",
        roles: str = "roles",
        scope: str = "scope",
    ) -> None:
        self.subject = subject
        self.tenant = tenant
        self.roles = roles
        self.scope = scope


class GateKeeper:
    """The default gate: bearer token -> verified claims -> permissions."""

    def __init__(
        self,
        token_verifier: TokenVerifierPort,
        permission_service: Optional[PermissionServicePort] = None,
        *,
        claim_names: Optional[ClaimNames] = None,
    ) -> None:
        """
        Args:
          token_verifier: required. A gate without verification is not a gate.
          permission_service: optional. Omit it when the token itself is the
            authority (permissions carried as claims/scopes) and there is no
            separate service to consult.
          claim_names: how to read identity out of the verified claims.
        """
        self._token_verifier = token_verifier
        self._permission_service = permission_service
        self._claims = claim_names or ClaimNames()

    async def authorize(
        self,
        headers: Mapping[str, str],
        required_permission: Optional[str] = None,
    ) -> AuthDecision:
        # 1. credential
        token = extract_bearer_token(headers)
        if token is None:
            return AuthRequired("no bearer token in the Authorization header")

        # 2. verify
        try:
            claims = await self._token_verifier.verify(token)
        except InvalidToken as e:
            # AuthRequired, not Reject: an expired or malformed token means
            # the caller should get a fresh one and retry, which is exactly
            # what AUTH_REQUIRED tells them to do.
            return AuthRequired(f"invalid credential: {e}")

        user = self._build_user(claims, token)

        # 3. permissions
        if self._permission_service is not None and user.user_id:
            # Not wrapped in a try/except: if the permission service is down
            # we do not know what this caller may do, and guessing in either
            # direction is wrong — fail open and the gate is decorative, fail
            # closed silently and an outage looks like a permissions bug. The
            # exception propagates to AgentExecutor, which ends the task
            # FAILED with the reason, so the outage is visible as an outage.
            #
            # Copied, not assigned: the returned set may be the same object a
            # CachedPermissionService is holding internally. If a handler
            # later mutates user.permissions (e.g. via a local grant), that
            # would otherwise poison the cache for every future request for
            # this subject.
            user.permissions = set(
                await self._permission_service.get_permissions(user.user_id, user.tenant_id)
            )
        elif not self._permission_service:
            # No permission service configured: the token itself is the
            # authority, per this class's own docstring. Scopes are the
            # conventional place an issuer encodes entitlements directly in
            # the token, so without a separate service to consult, scopes
            # double as permissions rather than every caller being rejected.
            user.permissions = set(user.scopes)

        # 4. agent-level check
        if required_permission is not None and not user.has_permission(required_permission):
            logger.info(
                "gate: rejecting %s — missing %r",
                user.user_id or "<anonymous>",
                required_permission,
            )
            return Reject(f"missing required permission {required_permission!r}")

        return Allow(user)

    def _build_user(self, claims: dict, token: str) -> UserContext:
        return UserContext(
            user_id=claims.get(self._claims.subject),
            tenant_id=claims.get(self._claims.tenant),
            roles=_as_list(claims.get(self._claims.roles)),
            scopes=_as_list(claims.get(self._claims.scope)),
            claims=claims,
            token=token,
        )


class AllowAllGateKeeper:
    """Lets everything through, with no identity attached.

    The default when no gate is configured, so that local development and
    tests don't need a token issuer. `create_app` logs a warning when it
    installs this, and `A2A_REQUIRE_AUTH=true` makes its use a startup error
    — the combination is meant to keep it impossible to ship this to
    production by accident while keeping it frictionless everywhere else.
    """

    async def authorize(
        self,
        headers: Mapping[str, str],
        required_permission: Optional[str] = None,
    ) -> AuthDecision:
        return Allow(UserContext())


def _as_list(value) -> list[str]:
    """Normalizes a claim that may be a list, a space-delimited string (the
    OAuth2 convention for `scope`), or absent."""
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(v) for v in value]
