"""ExtendedAgentClient — a reusable handle on one remote agent.

`call_agent(...)` and friends are one-shot: each call opens an httpx client,
fetches the agent card, sends one message, and tears it all down. That is the
right shape for a script and the wrong shape for a coordinator, which calls
the same handful of agents repeatedly — there, every call pays for a fresh
connection *and* an extra HTTP round trip to re-read a card that hasn't
changed.

This holds those things open:

  - one httpx client, so connections are reused
  - one resolved agent card, fetched on first use and kept
  - one set of interceptors, so credentials are configured once

It also exposes the rest of what the protocol offers, which the one-shot
functions don't: `get_task`, `cancel_task`, and `subscribe` for reattaching
to a task already in flight. Long-running work is precisely the case where a
caller needs them — a task it started, went away from, and now wants to check
on.

    async with ExtendedAgentClient(url, credentials=token) as agent:
        result = await agent.send_result("what's the weather?")
        later = await agent.get_task(result.task_id)

The one-shot functions still exist and are built on this, so there are not
two implementations of the response-parsing logic.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator, Optional

import httpx
from a2a.client import Client, ClientConfig, create_client
from a2a.helpers import new_text_part
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Role,
    SendMessageRequest,
    StreamResponse,
    SubscribeToTaskRequest,
)

from ..schema import (
    A2ATaskResult,
    ExtendedArtifact,
    ExtendedMessage,
    ExtendedPart,
    ExtendedTask,
    ExtendedTaskState,
    PartEmitter,
)
from .credentials import Credentials, build_auth_interceptor
from .errors import A2ACallError, CALL_FAILED_STATES

__all__ = ["ExtendedAgentClient"]


def build_message(text: str) -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            parts=[new_text_part(text)],
        )
    )


class ExtendedAgentClient:
    """A reusable client for one remote A2A agent."""

    def __init__(
        self,
        base_url: str,
        *,
        credentials: Optional[Credentials] = None,
        timeout: Optional[float] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        Args:
          credentials: a bearer token, or a `CredentialProvider` resolved per
            call. Configured once here rather than per call.
          timeout: applied to the httpx client this creates. Ignored when
            `http_client` is given.
          http_client: an httpx client to use. The caller keeps ownership and
            this never closes it.
        """
        self._base_url = base_url
        self._credentials = credentials
        self._timeout = timeout
        self._http_client = http_client
        self._owns_http = http_client is None
        self._client: Optional[Client] = None

    async def __aenter__(self) -> "ExtendedAgentClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    async def _ensure_client(self) -> Client:
        """The native client, built once and reused.

        Building it is what resolves the agent card, so doing it once is the
        difference between one round trip per call and two.
        """
        if self._client is None:
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(timeout=self._timeout)
            try:
                self._client = await create_client(
                    self._base_url,
                    ClientConfig(streaming=True, httpx_client=self._http_client),
                    interceptors=build_auth_interceptor(self._credentials),
                )
            except Exception:
                # create_client() resolves the agent card before returning,
                # so it can fail (unreachable host, bad card) after we've
                # already opened an owned httpx client above. Without this,
                # every failed first call leaks that connection — self._client
                # stays None, so close() never sees it either.
                if self._owns_http:
                    await self._http_client.aclose()
                    self._http_client = None
                raise
        return self._client

    async def close(self) -> None:
        """Release the connection, if this object opened one.

        The native `Client.close()` goes straight through to
        `httpx_client.aclose()`, so it is only safe to call when we own that
        client — otherwise it would close a client the caller still needs.
        """
        if self._client is not None and self._owns_http:
            await self._client.close()
        elif self._http_client is not None and self._owns_http:
            # _ensure_client() was never reached (or failed before storing
            # self._client) but still opened an httpx client — close it
            # directly since there's no native Client wrapping it yet.
            await self._http_client.aclose()
        self._client = None

    # ---- sending ------------------------------------------------------- #
    async def send_result(
        self, text: str, *, emit: Optional[PartEmitter] = None
    ) -> A2ATaskResult:
        """Send `text` and collect the whole exchange into an A2ATaskResult.

        Raises:
          A2ACallError: the task ended FAILED or REJECTED.
        """
        client = await self._ensure_client()
        return await self._collect(client.send_message(build_message(text)), emit=emit)

    async def send(self, text: str, *, emit: Optional[PartEmitter] = None) -> str:
        """Send `text` and return just the answer."""
        return (await self.send_result(text, emit=emit)).text()

    async def send_parts(
        self, text: str, *, emit: Optional[PartEmitter] = None
    ) -> list[ExtendedPart]:
        """Send `text` and return the typed parts of the answer."""
        return (await self.send_result(text, emit=emit)).parts()

    # ---- task management ----------------------------------------------- #
    async def get_task(self, task_id: str, *, history_length: Optional[int] = None) -> ExtendedTask:
        """Fetch a task's current state — for checking on work started earlier."""
        client = await self._ensure_client()
        request = GetTaskRequest(id=task_id)
        if history_length is not None:
            request.history_length = history_length
        return ExtendedTask.from_protobuf(await client.get_task(request))

    async def cancel_task(self, task_id: str) -> ExtendedTask:
        """Ask the agent to abandon a task in flight."""
        client = await self._ensure_client()
        return ExtendedTask.from_protobuf(
            await client.cancel_task(CancelTaskRequest(id=task_id))
        )

    async def subscribe(
        self, task_id: str, *, emit: Optional[PartEmitter] = None
    ) -> A2ATaskResult:
        """Reattach to a task already running and follow it to completion.

        For picking up a long-running task after the original connection went
        away — a reconnecting UI, or a coordinator resuming after a restart.
        """
        client = await self._ensure_client()
        stream = client.subscribe(SubscribeToTaskRequest(id=task_id))
        return await self._collect(stream, emit=emit)

    # ---- response assembly --------------------------------------------- #
    async def _collect(
        self, stream: AsyncIterator[StreamResponse], *, emit: Optional[PartEmitter]
    ) -> A2ATaskResult:
        """Fold a StreamResponse stream into an A2ATaskResult.

        Handles all four branches of the payload oneof. `message` is the
        message-mode reply — an agent answering without creating a Task at all
        — which has no status or artifacts of its own.
        """
        task_id = ""
        status = ExtendedTaskState.UNSPECIFIED
        artifacts: list[ExtendedArtifact] = []
        history: list[ExtendedMessage] = []
        message: Optional[ExtendedMessage] = None
        status_message: Optional[ExtendedMessage] = None

        async for event in stream:
            if event.HasField("status_update"):
                update = event.status_update
                task_id = update.task_id or task_id
                proto_status = update.status
                status = ExtendedTaskState.from_proto(proto_status.state)
                # Reset on every status update, not just when a new message
                # is present — otherwise a terminal status with no message of
                # its own would keep showing a stale WORKING progress note as
                # if it were the final explanation.
                status_message = None
                if proto_status.HasField("message"):
                    status_message = ExtendedMessage.from_protobuf(proto_status.message)
                    # A WORKING status carries live progress; any other status
                    # explains where the task ended up. Only the former streams.
                    if status is ExtendedTaskState.WORKING and emit:
                        for part in status_message.parts:
                            await emit(part)
                if status in CALL_FAILED_STATES:
                    detail = status_message.text() if status_message else "unknown error"
                    raise A2ACallError(
                        f"agent at {self._base_url} {status.value}: {detail}",
                        status=status,
                        detail=detail,
                    )
            elif event.HasField("artifact_update"):
                update = event.artifact_update
                task_id = update.task_id or task_id
                artifacts.append(ExtendedArtifact.from_protobuf(update.artifact))
            elif event.HasField("task"):
                # The non-streaming shape: the whole task comes back as one
                # event instead of a status_update stream, so this is the
                # only place a non-streaming call ever sees the final state —
                # it has to raise here too, or a crashed/rejected handler
                # looks like a silent empty success.
                task = event.task
                task_id = task.id or task_id
                status = ExtendedTaskState.from_proto(task.status.state)
                artifacts.extend(ExtendedArtifact.from_protobuf(a) for a in task.artifacts)
                history = [ExtendedMessage.from_protobuf(m) for m in task.history]
                if task.status.HasField("message"):
                    status_message = ExtendedMessage.from_protobuf(task.status.message)
                if status in CALL_FAILED_STATES:
                    detail = status_message.text() if status_message else "unknown error"
                    raise A2ACallError(
                        f"agent at {self._base_url} {status.value}: {detail}",
                        status=status,
                        detail=detail,
                    )
            elif event.HasField("message"):
                message = ExtendedMessage.from_protobuf(event.message)
                if emit:
                    for part in message.parts:
                        await emit(part)

        return A2ATaskResult(
            task_id=task_id,
            status=status,
            artifacts=artifacts,
            history=history,
            message=message,
            status_message=status_message,
        )
