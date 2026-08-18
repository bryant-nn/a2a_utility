"""A2ASettings — env-driven wiring."""

from __future__ import annotations

from a2a_utility.server import A2ASettings, ServerMode


def test_defaults_are_local_development_friendly():
    settings = A2ASettings()
    assert settings.server_mode is ServerMode.AGENT


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("A2A_SERVER_MODE", "DISCOVERY")
    monkeypatch.setenv("A2A_PORT", "8090")

    settings = A2ASettings()
    assert settings.server_mode is ServerMode.DISCOVERY
    assert settings.port == 8090


def test_ttl_default_comfortably_exceeds_the_heartbeat_interval():
    """A TTL at or near the heartbeat interval makes healthy agents flicker
    out of the directory between beats."""
    settings = A2ASettings()
    assert settings.registry_ttl_seconds >= settings.registry_heartbeat_seconds * 2


def test_url_helpers():
    settings = A2ASettings(host="10.0.0.1", port=9000)
    assert settings.url == "http://10.0.0.1:9000"
    assert settings.agent_card_url == "http://10.0.0.1:9000/.well-known/agent-card.json"
