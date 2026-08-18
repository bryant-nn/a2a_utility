"""Attaching credentials to outbound A2A calls.

A caller passes `credentials=` and stops thinking about it — the actual
header (bearer vs. an API key under its own header name) is native's
business, driven by whatever security scheme the *target* agent's card
declares.

Built on the native SDK's own auth machinery (`AuthInterceptor` +
`CredentialService`) rather than a parallel implementation, so that mapping
stays the SDK's business when the target does declare a scheme.

With one deliberate addition: native's `AuthInterceptor` attaches nothing
when the target's agent card declares no `security_schemes` — it iterates
the card's requirements, and an empty list means an empty loop. That is a
reasonable protocol reading and a bad failure mode — a caller who passed a
token gets a silent 401 from a server that does require one, because the two
sides disagree about whether the card was filled in. It is also, against an
a2a_utility-served agent, now the *only* case there is: `ExtendedAgentCard`
has no way to declare a security scheme at all (a2a_utility's server has no
built-in auth concept — see `server/README.md`). `_A2AAuthInterceptor`
therefore falls back to a plain `Authorization: Bearer` header whenever
credentials were supplied and the card declared nothing. Explicit
declarations always win when the target does have them; the fallback only
fills a vacuum.

Agent-to-agent forwarding
-------------------------
The common shape in a multi-agent system: something upstream authenticates a
caller and forwards that identity to a downstream agent it calls on the
caller's behalf:

    answer = await call_agent(url, question, credentials=incoming_token)

Note what this means — the downstream agent authorizes *the original
caller*, not whatever's calling it here — which is usually what you want,
and always worth being deliberate about. What counts as "the incoming
token" and how it was verified is entirely up to whatever sits in front of
the caller; a2a_utility only carries it forward.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol, Union, runtime_checkable

from a2a.client import ClientCallContext
from a2a.client.auth import AuthInterceptor, CredentialService
from a2a.client.interceptors import BeforeArgs

__all__ = ["CredentialProvider", "StaticToken", "Credentials", "build_auth_interceptor"]

logger = logging.getLogger(__name__)


@runtime_checkable
class CredentialProvider(Protocol):
    async def get_token(self, scheme_name: str) -> Optional[str]:
        """The credential to present for `scheme_name`, or None to send none.

        Async so an implementation can refresh an expiring token, or fetch a
        service-to-service credential, at call time rather than at
        construction time — a token captured once at startup outlives its
        expiry.
        """
        ...


class StaticToken:
    """A fixed credential, sent for whatever scheme the target declares."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self, scheme_name: str) -> Optional[str]:
        return self._token


Credentials = Union[str, CredentialProvider]
"""A bare string is the common case and is treated as a static token."""


def _as_provider(credentials: Credentials) -> CredentialProvider:
    return StaticToken(credentials) if isinstance(credentials, str) else credentials


class _ProviderCredentialService(CredentialService):
    """Adapts a CredentialProvider to the native CredentialService interface.

    Native's own `InMemoryContextCredentialStore` keys credentials off a
    `sessionId` in the call context, which suits a server juggling many
    users' credentials. A caller making one outbound call already knows which
    credential it means, so this just delegates.
    """

    def __init__(self, provider: CredentialProvider) -> None:
        self._provider = provider

    async def get_credentials(
        self, security_scheme_name: str, context: Optional[ClientCallContext]
    ) -> Optional[str]:
        return await self._provider.get_token(security_scheme_name)


class _A2AAuthInterceptor(AuthInterceptor):
    """Native's interceptor, plus a fallback for cards that declare nothing."""

    def __init__(self, provider: CredentialProvider) -> None:
        super().__init__(_ProviderCredentialService(provider))
        self._provider = provider

    async def before(self, args: BeforeArgs) -> None:
        await super().before(args)

        if _has_authorization(args.context):
            return  # native matched a declared scheme; leave it alone

        card = args.agent_card
        if card.security_schemes or card.security_requirements:
            # The card *does* declare schemes and native still attached
            # nothing — it decided none of them matched what we hold. Second-
            # guessing that would send a credential the target didn't ask for.
            return

        token = await self._provider.get_token("bearer")
        if not token:
            return

        logger.debug(
            "agent card for %r declares no security schemes; sending a bearer "
            "token anyway because credentials were supplied",
            card.name,
        )
        if args.context is None:
            args.context = ClientCallContext()
        if args.context.service_parameters is None:
            args.context.service_parameters = {}
        args.context.service_parameters["Authorization"] = f"Bearer {token}"


def _has_authorization(context: Optional[ClientCallContext]) -> bool:
    if context is None or not context.service_parameters:
        return False
    return any(k.lower() == "authorization" for k in context.service_parameters)


def build_auth_interceptor(credentials: Optional[Credentials]):
    """The interceptor list for `create_client(interceptors=...)`."""
    if credentials is None:
        return []
    return [_A2AAuthInterceptor(_as_provider(credentials))]
