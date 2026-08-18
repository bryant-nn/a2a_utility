"""End-to-end: `credentials=` on the client actually puts an
`Authorization` header on the wire.

This used to be verified by checking that a server-side GateKeeper accepted
the credential — but the server side no longer has any auth concept
(GateKeeper, UserContext, and the card's `auth=` declaration were all
removed). What's left to test is squarely a *client* behavior: does
`credentials=`/`StaticToken`/a `CredentialProvider` really attach the header,
and does `_A2AAuthInterceptor`'s fallback (attach a bearer header even when
the target card declares no security scheme at all — which is now the only
case there is, since a card can no longer declare one) actually fire.

Verified by a plain Starlette middleware that records the `Authorization`
header it sees on each request, independent of anything the handler does —
decoupling "was the credential attached correctly" from "what does a server
do with it", which is what this file should be testing.
"""

from __future__ import annotations

from starlette.middleware import Middleware

from a2a_utility.client import StaticToken, call_agent

from harness import BASE_URL, running_app

GOOD_TOKEN = "good-token"


class _CaptureAuthorization:
    """Records the `Authorization` header of every JSON-RPC call (POST /).

    Not every request: `create_client()` fetches the agent card first with a
    plain unauthenticated GET (interceptors only apply to the actual RPC
    call), and capturing that too would make every test see an extra leading
    `None` unrelated to what credentials= actually did.
    """

    def __init__(self, app) -> None:
        self.app = app
        self.seen: list[str | None] = []

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("method") == "POST":
            headers = dict(scope.get("headers") or [])
            value = headers.get(b"authorization")
            self.seen.append(value.decode() if value else None)
        await self.app(scope, receive, send)


def capturing_app(make_app):
    """A plain (ungated) app wrapped with a header-capturing middleware.
    Returns (app, capture) — `capture.seen` fills in as requests arrive."""
    capture_holder: dict[str, _CaptureAuthorization] = {}

    def _capturing_middleware_factory(app):
        instance = _CaptureAuthorization(app)
        capture_holder["instance"] = instance
        return instance

    async def handler(context, event_queue):
        from a2a_utility.server import ExtendedTaskUpdater
        from a2a_utility.schema import ExtendedPart

        tu = ExtendedTaskUpdater(context, event_queue)
        await tu.start_work()
        await tu.add_artifact([ExtendedPart.from_text("ok")])
        await tu.complete()

    app = make_app(handler, middleware=[Middleware(_capturing_middleware_factory)])
    return app, capture_holder


async def test_a_bare_string_credential_attaches_a_bearer_header(make_app):
    """The fallback path in _A2AAuthInterceptor: the target card declares no
    security scheme at all (cards can't declare one anymore), so a caller who
    passed credentials= still gets a bearer header attached rather than
    silently sending nothing."""
    app, capture = capturing_app(make_app)
    async with running_app(app) as http:
        await call_agent(BASE_URL, "hi", http_client=http, credentials=GOOD_TOKEN)

    assert capture["instance"].seen == [f"Bearer {GOOD_TOKEN}"]


async def test_no_credentials_means_no_authorization_header(make_app):
    app, capture = capturing_app(make_app)
    async with running_app(app) as http:
        await call_agent(BASE_URL, "hi", http_client=http)

    assert capture["instance"].seen == [None]


async def test_a_credential_provider_is_consulted_per_call(make_app):
    """Providers exist so a token can be refreshed at call time rather than
    captured once at startup and left to expire."""
    calls: list[str] = []

    class RecordingProvider:
        async def get_token(self, scheme_name: str) -> str:
            calls.append(scheme_name)
            return GOOD_TOKEN

    app, capture = capturing_app(make_app)
    async with running_app(app) as http:
        await call_agent(BASE_URL, "one", http_client=http, credentials=RecordingProvider())
        await call_agent(BASE_URL, "two", http_client=http, credentials=RecordingProvider())

    assert len(calls) == 2
    assert capture["instance"].seen == [f"Bearer {GOOD_TOKEN}"] * 2


async def test_static_token_provider_is_equivalent_to_a_bare_string(make_app):
    app, capture = capturing_app(make_app)
    async with running_app(app) as http:
        await call_agent(BASE_URL, "hi", http_client=http, credentials=StaticToken(GOOD_TOKEN))

    assert capture["instance"].seen == [f"Bearer {GOOD_TOKEN}"]
