"""End-to-end auth: a gate refusal must reach the caller as a *task state*.

This is the property that motivated putting the gate in the executor rather
than in a Starlette middleware, and it can only be checked here — a unit test
sees an `AuthDecision`, not what the caller actually receives over the wire.
"""

from __future__ import annotations

import pytest

from a2a_utility.client import A2ACallError, call_agent, call_agent_result
from a2a_utility.schema import ExtendedTaskState
from a2a_utility.server import (
    ExtendedTaskUpdater,
    GateKeeper,
    InvalidToken,
    UserContext,
)

from harness import BASE_URL, running_app

GOOD_TOKEN = "good-token"


class FakeVerifier:
    def __init__(self, claims: dict | None = None) -> None:
        self._claims = claims or {"sub": "alice", "tenant_id": "acme"}

    async def verify(self, token: str) -> dict:
        if token != GOOD_TOKEN:
            raise InvalidToken("signature verification failed")
        return self._claims


class FakePermissions:
    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    async def get_permissions(self, subject: str, tenant: str | None = None) -> set[str]:
        return self._permissions


def gate(permissions: set[str] = frozenset()) -> GateKeeper:
    return GateKeeper(FakeVerifier(), FakePermissions(set(permissions)))


async def echo_handler(context, event_queue) -> None:
    tu = ExtendedTaskUpdater(context, event_queue)
    await tu.start_work()
    await tu.add_artifact([_text(f"hello {context.user.user_id}")])
    await tu.complete()


def _text(value: str):
    from a2a_utility.schema import ExtendedPart

    return ExtendedPart.from_text(value)


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- refusals become task states ------------------------------------------- #
async def test_no_credential_ends_the_task_auth_required(make_app):
    app = make_app(echo_handler, gate_keeper=gate())

    async with running_app(app) as http:
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.status is ExtendedTaskState.AUTH_REQUIRED
    # AUTH_REQUIRED doesn't raise — the task is paused, not finished — so the
    # reason has to be readable off the result.
    assert "no bearer token" in result.status_text


async def test_invalid_credential_ends_the_task_auth_required(make_app):
    """Not REJECTED: an expired or malformed token is worth retrying with a
    fresh one, and AUTH_REQUIRED is what says so."""
    app = make_app(echo_handler, gate_keeper=gate())

    async with running_app(app) as http:
        http.headers.update(auth_header("forged"))
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.status is ExtendedTaskState.AUTH_REQUIRED


async def test_missing_agent_permission_ends_the_task_rejected(make_app):
    """REJECTED, not AUTH_REQUIRED: we know exactly who this is, and the
    answer is no. Retrying with the same identity won't help."""
    app = make_app(
        echo_handler,
        gate_keeper=gate({"agent:other"}),
        required_permission="agent:weather",
    )

    async with running_app(app) as http:
        http.headers.update(auth_header(GOOD_TOKEN))
        with pytest.raises(A2ACallError, match="rejected"):
            await call_agent(BASE_URL, "hi", http_client=http)


async def test_valid_credential_with_permission_reaches_the_handler(make_app):
    app = make_app(
        echo_handler,
        gate_keeper=gate({"agent:weather"}),
        required_permission="agent:weather",
    )

    async with running_app(app) as http:
        http.headers.update(auth_header(GOOD_TOKEN))
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.status is ExtendedTaskState.COMPLETED
    assert result.text() == "hello alice"


async def test_the_handler_never_runs_when_the_gate_refuses(make_app):
    """The point of gating before dispatch: refused work costs nothing beyond
    the refusal itself."""
    ran = False

    async def handler(context, event_queue):
        nonlocal ran
        ran = True
        await ExtendedTaskUpdater(context, event_queue).complete()

    async with running_app(make_app(handler, gate_keeper=gate())) as http:
        await call_agent_result(BASE_URL, "hi", http_client=http)

    assert ran is False


# --- tool-level checks inside the handler ---------------------------------- #
async def test_tool_permission_denied_inside_the_handler_ends_rejected(make_app):
    """Tool-level authorization can't happen at the gate — which tools a
    request reaches isn't known until it runs — so it happens at the point of
    use and lands in the same REJECTED state."""

    async def handler(context, event_queue):
        tu = ExtendedTaskUpdater(context, event_queue)
        await tu.start_work()
        context.user.require("tool:send_email")  # raises PermissionDenied
        await tu.complete()

    app = make_app(handler, gate_keeper=gate({"tool:get_weather"}))

    async with running_app(app) as http:
        http.headers.update(auth_header(GOOD_TOKEN))
        with pytest.raises(A2ACallError) as exc:
            await call_agent_result(BASE_URL, "hi", http_client=http)

    assert exc.value.status is ExtendedTaskState.REJECTED
    assert "tool:send_email" in exc.value.detail


async def test_a_granted_tool_permission_lets_the_handler_finish(make_app):
    async def handler(context, event_queue):
        tu = ExtendedTaskUpdater(context, event_queue)
        await tu.start_work()
        context.user.require("tool:get_weather")
        await tu.add_artifact([_text("sunny")])
        await tu.complete()

    app = make_app(handler, gate_keeper=gate({"tool:*"}))

    async with running_app(app) as http:
        http.headers.update(auth_header(GOOD_TOKEN))
        result = await call_agent_result(BASE_URL, "hi", http_client=http)

    assert result.status is ExtendedTaskState.COMPLETED
    assert result.text() == "sunny"


async def test_handler_sees_the_verified_identity(make_app):
    seen: dict[str, UserContext] = {}

    async def handler(context, event_queue):
        seen["user"] = context.user
        await ExtendedTaskUpdater(context, event_queue).complete()

    async with running_app(make_app(handler, gate_keeper=gate({"a"}))) as http:
        http.headers.update(auth_header(GOOD_TOKEN))
        await call_agent_result(BASE_URL, "hi", http_client=http)

    user = seen["user"]
    assert (user.user_id, user.tenant_id) == ("alice", "acme")
    assert user.permissions == {"a"}
    assert user.token == GOOD_TOKEN  # available to forward downstream


# --- the permissive default ------------------------------------------------ #
async def test_without_a_gate_requests_run_but_stay_unauthenticated(make_app):
    """create_app installs AllowAllGateKeeper and warns. Requests succeed, but
    context.user is unauthenticated — so tool checks still deny."""

    async def handler(context, event_queue):
        tu = ExtendedTaskUpdater(context, event_queue)
        assert not context.user.is_authenticated
        context.user.require("tool:anything")
        await tu.complete()

    async with running_app(make_app(handler)) as http:
        with pytest.raises(A2ACallError) as exc:
            await call_agent_result(BASE_URL, "hi", http_client=http)

    assert exc.value.status is ExtendedTaskState.REJECTED


def test_require_auth_refuses_to_build_an_app_without_a_gate(make_app):
    """The guard that stops an unauthenticated server reaching production."""
    with pytest.raises(ValueError, match="require_auth"):
        make_app(echo_handler, require_auth=True)


def test_require_auth_is_satisfied_by_an_explicit_gate(make_app):
    make_app(echo_handler, require_auth=True, gate_keeper=gate())
