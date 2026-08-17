"""GateKeeper unit tests.

The gate is pure orchestration over two ports, so the whole thing is testable
with two small fakes — no JWT library, no HTTP server, no token issuer.
"""

from __future__ import annotations

import pytest

from a2a_utility.server import (
    AllowAllGateKeeper,
    ClaimNames,
    GateKeeper,
    InvalidToken,
    PermissionDenied,
    UserContext,
)
from a2a_utility.server.domain.models.auth_decision import Allow, AuthRequired, Reject
from a2a_utility.server.application.services.gate_keeper import extract_bearer_token


class FakeVerifier:
    """Accepts tokens registered up front; rejects everything else."""

    def __init__(self, tokens: dict[str, dict] | None = None) -> None:
        self.tokens = tokens or {"good-token": {"sub": "alice", "tenant_id": "acme"}}
        self.calls: list[str] = []

    async def verify(self, token: str) -> dict:
        self.calls.append(token)
        if token not in self.tokens:
            raise InvalidToken("signature verification failed")
        return self.tokens[token]


class FakePermissions:
    def __init__(self, permissions: dict[str, set[str]] | None = None) -> None:
        self.permissions = permissions or {}
        self.calls: list[tuple[str, str | None]] = []

    async def get_permissions(self, subject: str, tenant: str | None = None) -> set[str]:
        self.calls.append((subject, tenant))
        return self.permissions.get(subject, set())


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


# --- step 1: credential extraction ---------------------------------------- #
@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"authorization": "Bearer abc"}, "abc"),
        ({"Authorization": "Bearer abc"}, "abc"),  # header name is case-insensitive
        ({"authorization": "bearer abc"}, "abc"),  # so is the scheme (RFC 7235)
        ({"authorization": "Bearer   abc  "}, "abc"),
        ({"authorization": "Basic abc"}, None),  # wrong scheme
        ({"authorization": "Bearer"}, None),  # no token
        ({"authorization": "Bearer "}, None),  # empty token
        ({}, None),
    ],
)
def test_bearer_extraction(headers, expected):
    assert extract_bearer_token(headers) == expected


async def test_missing_credential_is_auth_required_not_reject():
    """AUTH_REQUIRED tells the caller to authenticate and retry; REJECTED
    tells them not to bother. A caller who simply forgot the header should
    get the former."""
    gate = GateKeeper(FakeVerifier())
    decision = await gate.authorize({})
    assert isinstance(decision, AuthRequired)


# --- step 2: verification -------------------------------------------------- #
async def test_invalid_token_is_auth_required_so_the_caller_can_refresh():
    gate = GateKeeper(FakeVerifier())
    decision = await gate.authorize(bearer("forged"))
    assert isinstance(decision, AuthRequired)
    assert "invalid credential" in decision.reason


async def test_verified_claims_become_the_user_context():
    verifier = FakeVerifier(
        {
            "good-token": {
                "sub": "alice",
                "tenant_id": "acme",
                "roles": ["operator"],
                "scope": "read write",  # OAuth2 space-delimited convention
            }
        }
    )
    decision = await GateKeeper(verifier).authorize(bearer("good-token"))

    assert isinstance(decision, Allow)
    assert decision.user.user_id == "alice"
    assert decision.user.tenant_id == "acme"
    assert decision.user.roles == ["operator"]
    assert decision.user.scopes == ["read", "write"]
    assert decision.user.token == "good-token"  # kept for downstream forwarding
    assert decision.user.is_authenticated


async def test_claim_names_are_configurable_for_issuers_that_differ():
    verifier = FakeVerifier({"t": {"user": "bob", "org": "beta", "groups": ["admin"]}})
    gate = GateKeeper(
        verifier, claim_names=ClaimNames(subject="user", tenant="org", roles="groups")
    )
    decision = await gate.authorize(bearer("t"))

    assert isinstance(decision, Allow)
    assert (decision.user.user_id, decision.user.tenant_id) == ("bob", "beta")
    assert decision.user.roles == ["admin"]


# --- step 3: permissions --------------------------------------------------- #
async def test_permissions_are_resolved_for_the_verified_subject():
    permissions = FakePermissions({"alice": {"agent:weather"}})
    decision = await GateKeeper(FakeVerifier(), permissions).authorize(bearer("good-token"))

    assert isinstance(decision, Allow)
    assert decision.user.permissions == {"agent:weather"}
    assert permissions.calls == [("alice", "acme")]


async def test_permission_service_outage_propagates_rather_than_failing_open_or_closed():
    """Neither silent answer is acceptable: failing open makes the gate
    decorative, failing closed makes an outage look like a permissions bug.
    The exception reaches AgentExecutor, which reports it as a failure."""

    class BrokenPermissions:
        async def get_permissions(self, subject, tenant=None):
            raise RuntimeError("permission service unreachable")

    gate = GateKeeper(FakeVerifier(), BrokenPermissions())
    with pytest.raises(RuntimeError, match="unreachable"):
        await gate.authorize(bearer("good-token"))


# --- step 4: agent-level check --------------------------------------------- #
async def test_missing_required_permission_is_rejected():
    permissions = FakePermissions({"alice": {"agent:other"}})
    gate = GateKeeper(FakeVerifier(), permissions)
    decision = await gate.authorize(bearer("good-token"), required_permission="agent:weather")

    assert isinstance(decision, Reject)
    assert "agent:weather" in decision.reason


async def test_holding_the_required_permission_is_allowed():
    permissions = FakePermissions({"alice": {"agent:weather"}})
    gate = GateKeeper(FakeVerifier(), permissions)
    decision = await gate.authorize(bearer("good-token"), required_permission="agent:weather")

    assert isinstance(decision, Allow)


async def test_no_required_permission_means_authentication_is_enough():
    gate = GateKeeper(FakeVerifier(), FakePermissions())
    assert isinstance(await gate.authorize(bearer("good-token")), Allow)


# --- UserContext permission matching --------------------------------------- #
@pytest.mark.parametrize(
    "granted,asked,expected",
    [
        ({"tool:get_weather"}, "tool:get_weather", True),
        ({"tool:get_weather"}, "tool:send_email", False),
        ({"tool:*"}, "tool:get_weather", True),
        ({"tool:*"}, "agent:weather", False),
        ({"*"}, "anything:at:all", True),
        (set(), "tool:get_weather", False),
        # No implicit hierarchy without a wildcard.
        ({"tool:a"}, "tool:a:b", False),
    ],
)
def test_permission_matching(granted, asked, expected):
    assert UserContext(permissions=granted).has_permission(asked) is expected


def test_require_raises_permission_denied_naming_the_permission_and_user():
    user = UserContext(user_id="alice", permissions={"tool:a"})
    with pytest.raises(PermissionDenied) as exc:
        user.require("tool:b")
    assert exc.value.permission == "tool:b"
    assert "alice" in str(exc.value)


def test_require_passes_silently_when_permitted():
    UserContext(permissions={"tool:a"}).require("tool:a")


# --- the permissive default ------------------------------------------------ #
async def test_allow_all_gate_keeper_allows_but_grants_no_identity():
    """It must not fabricate an identity: an unauthenticated UserContext means
    `context.user.require(...)` still denies, so a handler's tool checks stay
    meaningful even where the gate was left off."""
    decision = await AllowAllGateKeeper().authorize({}, required_permission="agent:anything")

    assert isinstance(decision, Allow)
    assert not decision.user.is_authenticated
    assert not decision.user.has_permission("tool:anything")
