"""GateMiddleware — the optional HTTP-level pre-filter.

It refuses credential-less requests before they become tasks. That is a
different *shape* of refusal from the executor gate (an HTTP status, not a
task state), so it needs its own tests at the HTTP layer.
"""

from __future__ import annotations

from starlette.middleware import Middleware

from a2a_utility.server import ExtendedTaskUpdater, GateMiddleware
from a2a_utility.schema import ExtendedPart

from harness import running_app


async def echo(context, event_queue) -> None:
    tu = ExtendedTaskUpdater(context, event_queue)
    await tu.start_work()
    await tu.add_artifact([ExtendedPart.from_text("ok")])
    await tu.complete()


def gated_app(make_app, **middleware_kwargs):
    return make_app(echo, middleware=[Middleware(GateMiddleware, **middleware_kwargs)])


async def test_a_request_with_no_token_is_refused_with_401(make_app):
    async with running_app(gated_app(make_app)) as http:
        response = await http.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "x"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_the_agent_card_stays_reachable_without_a_token(make_app):
    """Exempt by default, and it must stay that way: a client has to read the
    card to learn which credential to send. Requiring one to fetch it makes
    the agent undiscoverable."""
    async with running_app(gated_app(make_app)) as http:
        response = await http.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.json()["name"] == "test_agent"


async def test_a_request_carrying_a_token_passes_through(make_app):
    """It checks presence and shape only — never validity. Signature
    verification lives in exactly one place (TokenVerifierPort), so there is
    no second implementation to drift."""
    async with running_app(gated_app(make_app)) as http:
        http.headers.update({"Authorization": "Bearer anything-at-all"})
        response = await http.get("/.well-known/agent-card.json")

    assert response.status_code == 200


async def test_a_malformed_authorization_header_is_refused(make_app):
    async with running_app(gated_app(make_app)) as http:
        http.headers.update({"Authorization": "Basic dXNlcjpwYXNz"})
        response = await http.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "x"})

    assert response.status_code == 401


async def test_exempt_paths_are_configurable(make_app):
    async def health(request):
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True})

    from starlette.routing import Route

    app = make_app(
        echo,
        middleware=[Middleware(GateMiddleware, exempt_paths=["/health"])],
        extra_routes=[Route("/health", health)],
    )

    async with running_app(app) as http:
        assert (await http.get("/health")).status_code == 200
        # The default exemption is replaced, not extended.
        assert (await http.get("/.well-known/agent-card.json")).status_code == 401
