"""JwtTokenVerifier against real RSA keys and real PyJWT.

Signed with a key generated in-process and served through a stubbed JWKS
client, so these are genuine signature/expiry/issuer/audience checks — not a
mock asserting that we called a function.

Skipped when the [auth] extra isn't installed, since that's a supported way
to run this package.
"""

from __future__ import annotations

import time

import pytest

jwt = pytest.importorskip("jwt", reason="requires the [auth] extra (PyJWT)")

from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from a2a_utility.server.adapters.outbound.jwt_token_verifier import JwtTokenVerifier  # noqa: E402
from a2a_utility.server.application.ports.outbound.token_verifier_port import (  # noqa: E402
    InvalidToken,
)

ISSUER = "https://internal-idp"
AUDIENCE = "joke-agent"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def verifier(keypair, monkeypatch):
    """A verifier whose JWKS lookup returns our test public key.

    Only the network fetch is stubbed; PyJWT still does the real signature
    and claim validation.
    """
    _, public = keypair

    def _build(**overrides):
        kwargs = {
            "jwks_url": "https://internal-idp/.well-known/jwks.json",
            "issuer": ISSUER,
            "audience": AUDIENCE,
        }
        kwargs.update(overrides)
        instance = JwtTokenVerifier(**kwargs)

        class _Key:
            key = public

        monkeypatch.setattr(
            instance._jwk_client, "get_signing_key_from_jwt", lambda token: _Key()
        )
        return instance

    return _build


def sign(keypair, **claims) -> str:
    private, _ = keypair
    payload = {
        "sub": "alice",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int(time.time()) + 300,
    }
    payload.update(claims)
    return jwt.encode(payload, private, algorithm="RS256")


async def test_a_correctly_signed_token_verifies(keypair, verifier):
    claims = await verifier().verify(sign(keypair, tenant_id="acme"))
    assert claims["sub"] == "alice"
    assert claims["tenant_id"] == "acme"


async def test_a_token_signed_by_someone_else_is_rejected(keypair, verifier):
    """The check that makes the whole gate more than decorative."""
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"sub": "mallory", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300},
        attacker,
        algorithm="RS256",
    )
    with pytest.raises(InvalidToken):
        await verifier().verify(forged)


async def test_an_expired_token_is_rejected(keypair, verifier):
    with pytest.raises(InvalidToken):
        await verifier().verify(sign(keypair, exp=int(time.time()) - 10))


async def test_a_token_with_no_expiry_is_rejected(keypair, verifier):
    """`require: ["exp"]` — a token that never expires can't be revoked by
    waiting, so we don't accept one."""
    private, _ = keypair
    forever = jwt.encode(
        {"sub": "alice", "iss": ISSUER, "aud": AUDIENCE}, private, algorithm="RS256"
    )
    with pytest.raises(InvalidToken):
        await verifier().verify(forever)


async def test_a_token_from_the_wrong_issuer_is_rejected(keypair, verifier):
    with pytest.raises(InvalidToken):
        await verifier().verify(sign(keypair, iss="https://somewhere-else"))


async def test_a_token_for_another_service_is_rejected(keypair, verifier):
    """The audience check. Without it, a token legitimately issued for any
    other internal service is a valid key to this one."""
    with pytest.raises(InvalidToken):
        await verifier().verify(sign(keypair, aud="some-other-service"))


async def test_audience_is_only_enforced_when_configured(keypair, verifier):
    token = sign(keypair, aud="some-other-service")
    claims = await verifier(audience=None).verify(token)
    assert claims["sub"] == "alice"


async def test_a_token_signed_with_an_unaccepted_algorithm_is_rejected(keypair, verifier):
    """Algorithms are pinned by configuration, never taken from the token's
    own header — trusting `alg` is the classic JWT confusion attack."""
    with pytest.raises(InvalidToken):
        await verifier(algorithms=["ES256"]).verify(sign(keypair))


async def test_leeway_tolerates_clock_skew(keypair, verifier):
    just_expired = sign(keypair, exp=int(time.time()) - 5)
    claims = await verifier(leeway=60).verify(just_expired)
    assert claims["sub"] == "alice"


async def test_a_malformed_token_is_rejected_not_crashed(verifier):
    with pytest.raises(InvalidToken):
        await verifier().verify("not-a-jwt")
