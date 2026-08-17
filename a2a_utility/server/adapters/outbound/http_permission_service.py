"""HttpPermissionService — asks an internal Permission Service over HTTP.

Assumes the shape most internal permission services land on:

    GET {base_url}/permissions?subject=<id>[&tenant=<id>]
    -> 200 {"permissions": ["agent:weather", "tool:*"]}

`response_key` and `path` are settable for services that use a different
noun, but anything more different than that is better served by writing a
small adapter of your own — the port is one method.

Almost always worth wrapping in `CachedPermissionService`: the gate calls
this on every request, so uncached it puts a network round trip on the
critical path of every A2A call. `create_app` does that wrapping for you when
you configure the permission service through `A2ASettings`.
"""

from __future__ import annotations

from typing import Optional

import httpx

__all__ = ["HttpPermissionService", "PermissionServiceUnavailable"]


class PermissionServiceUnavailable(Exception):
    """The permission service could not be reached or answered with an error.

    Raised rather than returning an empty set, which would be indistinguishable
    from "this user is entitled to nothing" and would quietly turn an outage
    into a wave of rejections.
    """


class HttpPermissionService:
    def __init__(
        self,
        base_url: str,
        *,
        path: str = "/permissions",
        response_key: str = "permissions",
        timeout: float = 3.0,
        http_client: Optional[httpx.AsyncClient] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        """
        Args:
          http_client: reuse a client across requests. Worth passing: this is
            on the hot path, and a fresh connection per lookup is measurable.
            When given, the caller keeps ownership and this never closes it.
          headers: sent with every lookup — for the service-to-service
            credential this server uses to authenticate itself to the
            permission service.
        """
        self._base_url = base_url.rstrip("/")
        self._path = path if path.startswith("/") else f"/{path}"
        self._response_key = response_key
        self._timeout = timeout
        self._headers = headers or {}
        self._http_client = http_client
        self._owns_client = http_client is None

    async def get_permissions(
        self, subject: str, tenant: Optional[str] = None
    ) -> set[str]:
        params = {"subject": subject}
        if tenant:
            params["tenant"] = tenant

        client = self._http_client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(
                f"{self._base_url}{self._path}",
                params=params,
                headers=self._headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as e:
            raise PermissionServiceUnavailable(
                f"permission lookup for {subject!r} failed: {e}"
            ) from e
        except ValueError as e:
            raise PermissionServiceUnavailable(
                f"permission service returned a non-JSON body for {subject!r}"
            ) from e
        finally:
            if self._owns_client:
                await client.aclose()

        permissions = payload.get(self._response_key)
        if permissions is None:
            raise PermissionServiceUnavailable(
                f"permission service response has no {self._response_key!r} key"
            )
        return set(permissions)
