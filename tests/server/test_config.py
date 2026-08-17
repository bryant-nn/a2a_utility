"""A2ASettings — env-driven wiring.

`build_gate_keeper()` is the path most deployments will actually use (rather
than constructing a GateKeeper by hand), so it needs to be exercised.
"""

from __future__ import annotations

import pytest

from a2a_utility.server import A2ASettings, ServerMode
from a2a_utility.server.application.services.gate_keeper import GateKeeper


def test_defaults_are_local_development_friendly():
    settings = A2ASettings()
    assert settings.server_mode is ServerMode.AGENT
    assert settings.require_auth is False
    assert settings.build_gate_keeper() is None  # no auth configured


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("A2A_SERVER_MODE", "DISCOVERY")
    monkeypatch.setenv("A2A_PORT", "8090")
    monkeypatch.setenv("A2A_REQUIRE_AUTH", "true")

    settings = A2ASettings()
    assert settings.server_mode is ServerMode.DISCOVERY
    assert settings.port == 8090
    assert settings.require_auth is True


def test_ttl_default_comfortably_exceeds_the_heartbeat_interval():
    """A TTL at or near the heartbeat interval makes healthy agents flicker
    out of the directory between beats."""
    settings = A2ASettings()
    assert settings.registry_ttl_seconds >= settings.registry_heartbeat_seconds * 2


def test_build_gate_keeper_returns_none_without_a_jwks_url():
    """Meaning "this deployment has no token verification" — which create_app
    turns into a warning, or a startup error under require_auth."""
    assert A2ASettings(permission_service_url="http://permissions").build_gate_keeper() is None


def test_build_gate_keeper_wires_a_jwt_verifier():
    pytest.importorskip("jwt", reason="requires the [auth] extra (PyJWT)")

    settings = A2ASettings(
        jwks_url="https://internal-idp/.well-known/jwks.json",
        jwt_issuer="https://internal-idp",
        jwt_audience="joke-agent",
    )
    gate = settings.build_gate_keeper()

    assert isinstance(gate, GateKeeper)
    assert gate._permission_service is None  # none configured


def test_build_gate_keeper_wraps_the_permission_service_in_a_cache():
    """Uncached, the gate would put a network round trip on every A2A call."""
    pytest.importorskip("jwt", reason="requires the [auth] extra (PyJWT)")
    from a2a_utility.server import CachedPermissionService

    settings = A2ASettings(
        jwks_url="https://internal-idp/.well-known/jwks.json",
        permission_service_url="http://permissions",
        permission_cache_ttl_seconds=45.0,
    )
    gate = settings.build_gate_keeper()

    assert isinstance(gate._permission_service, CachedPermissionService)
    assert gate._permission_service._ttl == 45.0


def test_url_helpers():
    settings = A2ASettings(host="10.0.0.1", port=9000)
    assert settings.url == "http://10.0.0.1:9000"
    assert settings.agent_card_url == "http://10.0.0.1:9000/.well-known/agent-card.json"
