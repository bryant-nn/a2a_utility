"""Plumbing for the end-to-end tests: a real app, driven over ASGI.

Kept out of `conftest.py` because these are values and helpers the test
modules import by name, not fixtures pytest injects.
"""

from __future__ import annotations

import contextlib
from typing import AsyncIterator

import httpx
from starlette.applications import Starlette

from a2a_utility.server import ExtendedAgentCard, ExtendedAgentSkill

BASE_URL = "http://a2a-utility.test"
"""Host the tests address the app at. ASGITransport ignores the host — it
dispatches on path alone — but the agent card advertises this URL as its
interface, and the native client POSTs to whatever the card advertises, so
the two have to agree."""


def build_card(name: str = "test_agent", port: int = 9999) -> ExtendedAgentCard:
    return ExtendedAgentCard(
        name=name,
        description="An agent used by a2a_utility's end-to-end tests.",
        host="a2a-utility.test",
        port=port,
        skills=[
            ExtendedAgentSkill(
                id="echo",
                name="Echo",
                description="Echoes what it was told.",
                examples=["say hello"],
            )
        ],
    )


@contextlib.asynccontextmanager
async def running_app(app: Starlette) -> AsyncIterator[httpx.AsyncClient]:
    """Runs `app`'s lifespan and yields an httpx client wired to it over ASGI.

    The lifespan matters: it is what starts the registry heartbeat and what
    calls `DefaultRequestHandlerV2.aclose()` on the way out, so a test that
    skipped it would not be testing the app we actually ship. ASGITransport
    does not drive the lifespan scope itself, hence the explicit
    `lifespan_context`.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL, timeout=10.0) as client:
        async with app.router.lifespan_context(app):
            yield client
