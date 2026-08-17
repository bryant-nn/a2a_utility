"""GateMiddleware — an optional HTTP-level pre-filter in front of the gate.

**Not** the main gate, and not a substitute for it. Middleware can only
return an HTTP status; it cannot produce a task event, so it can never give
an A2A caller the `REJECTED`/`AUTH_REQUIRED` task state they know how to
interpret. That is why the real decision lives in `AgentExecutor.execute()`.

What this adds is cheapness at the edge: a request with no credential at all
is going to be refused anyway, and refusing it here means the framework never
builds a task, never allocates an ActiveTask, and never touches the task
store for it. Useful when a service is exposed somewhere unauthenticated
traffic actually arrives; unnecessary on an internal-only mesh, which is why
it is opt-in.

It checks only for *presence and shape*, never validity — signature
verification stays in one place (the `TokenVerifierPort`), so there is no
second implementation to drift.

    create_app(..., middleware=[Middleware(GateMiddleware)])
"""

from __future__ import annotations

from typing import Iterable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from ...application.services.gate_keeper import extract_bearer_token

__all__ = ["GateMiddleware"]

_DEFAULT_EXEMPT = ("/.well-known/",)


class GateMiddleware:
    """Rejects requests with no bearer token before they become tasks."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        exempt_paths: Optional[Iterable[str]] = None,
    ) -> None:
        """
        Args:
          exempt_paths: path prefixes that skip the check. The agent card
            endpoint is exempt by default and should stay that way: a client
            has to *read* the card to learn which credential to send, so
            requiring one to fetch it makes the agent undiscoverable.
        """
        self.app = app
        self._exempt = tuple(exempt_paths) if exempt_paths is not None else _DEFAULT_EXEMPT

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._exempt):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        if extract_bearer_token(request.headers) is None:
            response = JSONResponse(
                {"error": "missing bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
