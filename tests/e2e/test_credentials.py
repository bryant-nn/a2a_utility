"""End-to-end credentials: `credentials=` on the client must satisfy the
gate on the server.

The two halves were built separately and each looks correct alone; this is
the test that they meet. It exercises the real interceptor chain, the real
HTTP headers, and the real gate.
"""

from __future__ import annotations

from a2a_utility.client import StaticToken, call_agent, call_agent_result
from a2a_utility.schema import ExtendedPart, ExtendedTaskState
from a2a_utility.server import BearerAuth, ExtendedTaskUpdater, GateKeeper, InvalidToken

from harness import BASE_URL, build_card, running_app

GOOD_TOKEN = "good-token"


class FakeVerifier:
    async def verify(self, token: str) -> dict:
        if token != GOOD_TOKEN:
            raise InvalidToken("signature verification failed")
        return {"sub": "alice", "tenant_id": "acme"}


class FakePermissions:
    async def get_permissions(self, subject: str, tenant: str | None = None) -> set[str]:
        return {"agent:test"}


async def whoami(context, event_queue) -> None:
    tu = ExtendedTaskUpdater(context, event_queue)
    await tu.start_work()
    await tu.add_artifact([ExtendedPart.from_text(context.user.user_id or "anonymous")])
    await tu.complete()


def secured_app(make_app, *, declare_on_card: bool):
    """An app behind a gate. `declare_on_card` controls whether the agent card
    advertises the bearer scheme."""
    card = build_card()
    if declare_on_card:
        card.auth = BearerAuth()
    return make_app(
        whoami,
        card=card,
        gate_keeper=GateKeeper(FakeVerifier(), FakePermissions()),
        required_permission="agent:test",
    )


async def test_credentials_reach_the_gate_when_the_card_declares_the_scheme(make_app):
    async with running_app(secured_app(make_app, declare_on_card=True)) as http:
        answer = await call_agent(BASE_URL, "hi", http_client=http, credentials=GOOD_TOKEN)

    assert answer == "alice"


async def test_credentials_are_sent_even_when_the_card_declares_nothing(make_app):
    """Native's AuthInterceptor attaches nothing to a card with no declared
    schemes. That silently produces an unauthenticated call from a caller who
    explicitly passed a token, so _A2AAuthInterceptor falls back to a bearer
    header. Without the fallback this test fails with AUTH_REQUIRED."""
    async with running_app(secured_app(make_app, declare_on_card=False)) as http:
        answer = await call_agent(BASE_URL, "hi", http_client=http, credentials=GOOD_TOKEN)

    assert answer == "alice"


async def test_a_bad_token_still_fails_the_gate(make_app):
    """Proving the previous two tests aren't passing because the gate is
    inert."""
    async with running_app(secured_app(make_app, declare_on_card=True)) as http:
        result = await call_agent_result(
            BASE_URL, "hi", http_client=http, credentials="forged"
        )

    assert result.status is ExtendedTaskState.AUTH_REQUIRED


async def test_no_credentials_against_a_gated_agent_is_auth_required(make_app):
    async with running_app(secured_app(make_app, declare_on_card=True)) as http:
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.status is ExtendedTaskState.AUTH_REQUIRED


async def test_a_credential_provider_is_consulted_per_call(make_app):
    """Providers exist so a token can be refreshed at call time rather than
    captured once at startup and left to expire."""
    calls: list[str] = []

    class RecordingProvider:
        async def get_token(self, scheme_name: str) -> str:
            calls.append(scheme_name)
            return GOOD_TOKEN

    async with running_app(secured_app(make_app, declare_on_card=True)) as http:
        await call_agent(BASE_URL, "one", http_client=http, credentials=RecordingProvider())
        await call_agent(BASE_URL, "two", http_client=http, credentials=RecordingProvider())

    assert len(calls) == 2


async def test_static_token_provider_is_equivalent_to_a_bare_string(make_app):
    async with running_app(secured_app(make_app, declare_on_card=True)) as http:
        answer = await call_agent(
            BASE_URL, "hi", http_client=http, credentials=StaticToken(GOOD_TOKEN)
        )

    assert answer == "alice"


async def test_forwarding_the_callers_token_to_a_downstream_agent(make_app):
    """The multi-agent shape: a root agent authenticates a user, then calls a
    domain agent on their behalf by forwarding `context.user.token`. Both
    servers are real, and the downstream one gates independently."""
    downstream = secured_app(make_app, declare_on_card=True)

    async with running_app(downstream) as downstream_http:

        async def root_handler(context, event_queue):
            tu = ExtendedTaskUpdater(context, event_queue)
            await tu.start_work()
            # Forward the end user's own credential, not the root agent's.
            answer = await call_agent(
                BASE_URL,
                "who am i",
                http_client=downstream_http,
                credentials=context.user.token,
            )
            await tu.add_artifact([ExtendedPart.from_text(f"downstream says: {answer}")])
            await tu.complete()

        root = make_app(
            root_handler,
            gate_keeper=GateKeeper(FakeVerifier(), FakePermissions()),
        )
        async with running_app(root) as root_http:
            answer = await call_agent(
                BASE_URL, "hi", http_client=root_http, credentials=GOOD_TOKEN
            )

    assert answer == "downstream says: alice"
