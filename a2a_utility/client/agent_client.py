"""Outbound A2A client — wraps the native a2a.client.

Any coordinator/script calls a domain agent through call_agent* without touching
the A2A wire format. Uses the SDK's own client (`create_client(base_url)` →
`Client.send_message(SendMessageRequest)` yielding `StreamResponse` protos), and
maps the returned Artifact parts through the shared typed contract
(`ExtendedPart.from_protobuf`) so the client speaks the same data format as the
server.

Three entry points:
  - call_agent_result(...) -> A2ATaskResult   (task_id, status, typed artifacts)
  - call_agent_parts(...)  -> list[ExtendedPart]
  - call_agent(...)        -> str              (concatenated text parts = the answer)

WORKING status messages are forwarded live to `emit` (a PartEmitter — the same
callback type a server-side handler receives) as they stream: every part in
the status message, not just text, so a caller can react to a live source
reference/file the same way it reacts to live thinking text.
"""

from __future__ import annotations

import uuid
from typing import Optional

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import new_text_part
from a2a.types import Message, Role, SendMessageRequest, TaskState

from ..schema import A2ATaskResult, ExtendedArtifact, ExtendedMessage, ExtendedPart, PartEmitter


class A2ACallError(RuntimeError):
    pass


def _build_request(text: str) -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            parts=[new_text_part(text)],
        )
    )


async def call_agent_result(
    base_url: str,
    text: str,
    *,
    emit: Optional[PartEmitter] = None,
    timeout: Optional[float] = None,
) -> A2ATaskResult:
    """Send `text` to the A2A agent at base_url; return a typed A2ATaskResult.

    Raises:
      A2ACallError: the remote task reached TASK_STATE_FAILED.
    """
    task_id = ""
    status = ""
    artifacts: list[ExtendedArtifact] = []
    history: list[ExtendedMessage] = []

    async with httpx.AsyncClient(timeout=timeout) as http:
        client = await create_client(
            base_url, ClientConfig(streaming=True, httpx_client=http)
        )
        try:
            async for ev in client.send_message(_build_request(text)):
                if ev.HasField("status_update"):
                    su = ev.status_update
                    task_id = su.task_id or task_id
                    st = su.status
                    status = TaskState.Name(st.state)
                    if st.state == TaskState.TASK_STATE_WORKING and st.HasField("message"):
                        if emit:
                            for p in st.message.parts:
                                await emit(ExtendedPart.from_protobuf(p))
                    elif st.state == TaskState.TASK_STATE_FAILED:
                        detail = "unknown error"
                        if st.HasField("message") and st.message.parts:
                            detail = st.message.parts[0].text
                        raise A2ACallError(f"agent at {base_url} failed: {detail}")
                elif ev.HasField("artifact_update"):
                    au = ev.artifact_update
                    task_id = au.task_id or task_id
                    artifacts.append(ExtendedArtifact.from_protobuf(au.artifact))
                elif ev.HasField("task"):
                    t = ev.task
                    task_id = t.id or task_id
                    status = TaskState.Name(t.status.state)
                    for a in t.artifacts:
                        artifacts.append(ExtendedArtifact.from_protobuf(a))
                    history = [ExtendedMessage.from_protobuf(m) for m in t.history]
        finally:
            await client.close()

    return A2ATaskResult(task_id=task_id, status=status, artifacts=artifacts, history=history)


async def call_agent_parts(
    base_url: str,
    text: str,
    *,
    emit: Optional[PartEmitter] = None,
    timeout: Optional[float] = None,
) -> list[ExtendedPart]:
    result = await call_agent_result(base_url, text, emit=emit, timeout=timeout)
    return result.parts()


async def call_agent(
    base_url: str,
    text: str,
    *,
    emit: Optional[PartEmitter] = None,
    timeout: Optional[float] = None,
) -> str:
    """Convenience: return only the concatenated text parts (the answer)."""
    result = await call_agent_result(base_url, text, emit=emit, timeout=timeout)
    return result.text()
