"""One-shot A2A calls — `call_agent` and friends.

The convenient shape for a script, a notebook, or any caller that talks to an
agent once: no object to construct, no lifecycle to manage.

    answer = await call_agent("http://127.0.0.1:9030", "what's the weather?")

Each call opens a connection, resolves the agent card, sends one message, and
closes. That is two HTTP round trips of overhead per call, which is fine
once and wasteful in a loop — a coordinator calling the same agents
repeatedly should hold an `ExtendedAgentClient` instead, which keeps the
connection and the resolved card. These functions are thin wrappers over that
class, so both paths share one implementation of the response handling and
one set of semantics.

Three shapes of the same call:
  - call_agent_result(...) -> A2ATaskResult   (task id, status, typed artifacts)
  - call_agent_parts(...)  -> list[ExtendedPart]
  - call_agent(...)        -> str             (the answer text)
"""

from __future__ import annotations

from typing import Optional

import httpx

from ..schema import A2ATaskResult, ExtendedPart, PartEmitter
from .agent import ExtendedAgentClient
from .credentials import Credentials
from .errors import A2ACallError

__all__ = ["A2ACallError", "call_agent", "call_agent_parts", "call_agent_result"]


async def call_agent_result(
    base_url: str,
    text: str,
    *,
    emit: Optional[PartEmitter] = None,
    timeout: Optional[float] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    credentials: Optional[Credentials] = None,
) -> A2ATaskResult:
    """Send `text` to the A2A agent at base_url; return a typed A2ATaskResult.

    Args:
      emit: called once per streamed part as it arrives.
      timeout: applies to the whole exchange, not per event. Ignored when
        `http_client` is given — set the timeout on that client instead.
      http_client: an httpx client to reuse instead of opening one per call.
        The caller keeps ownership; it is never closed here.
      credentials: a bearer token, or a `CredentialProvider` for anything that
        must be resolved per call. To call a downstream agent on the current
        caller's behalf, pass `context.user.token`.

    Raises:
      A2ACallError: the task ended FAILED or REJECTED. A *paused* task
        (AUTH_REQUIRED / INPUT_REQUIRED) returns normally, with the reason in
        `status_message` — it is waiting on the caller, not finished.
    """
    agent = ExtendedAgentClient(
        base_url, credentials=credentials, timeout=timeout, http_client=http_client
    )
    try:
        return await agent.send_result(text, emit=emit)
    finally:
        await agent.close()


async def call_agent_parts(
    base_url: str,
    text: str,
    *,
    emit: Optional[PartEmitter] = None,
    timeout: Optional[float] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    credentials: Optional[Credentials] = None,
) -> list[ExtendedPart]:
    """Same call as call_agent_result, returning just the typed parts."""
    result = await call_agent_result(
        base_url,
        text,
        emit=emit,
        timeout=timeout,
        http_client=http_client,
        credentials=credentials,
    )
    return result.parts()


async def call_agent(
    base_url: str,
    text: str,
    *,
    emit: Optional[PartEmitter] = None,
    timeout: Optional[float] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    credentials: Optional[Credentials] = None,
) -> str:
    """Same call as call_agent_result, returning only the answer text."""
    result = await call_agent_result(
        base_url,
        text,
        emit=emit,
        timeout=timeout,
        http_client=http_client,
        credentials=credentials,
    )
    return result.text()
