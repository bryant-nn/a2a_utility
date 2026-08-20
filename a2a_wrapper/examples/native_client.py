"""A minimal outbound A2A client, built directly on `a2a.client` — no
`a2a_utility` import anywhere. Exists to show that talking to an
a2a_wrapper-served agent (or any native a2a agent) doesn't require anything
beyond the SDK itself; `a2a_utility.client` is a richer, more convenient
version of the same idea, not a requirement.

`stream_agent()` is the core: an async generator yielding one dict per
wire event, so a caller (chat_server.py's SSE endpoint, or your own script)
can show progress live instead of waiting for the whole exchange to finish.
"""

from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Optional

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import new_text_part
from a2a.types import CancelTaskRequest, Message, Role, SendMessageRequest, TaskState


def _build_message(text: str, task_id: Optional[str] = None) -> Message:
    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
        parts=[new_text_part(text)],
    )
    if task_id:
        message.task_id = task_id
    return message


async def stream_agent(
    base_url: str,
    text: str,
    *,
    task_id: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Send `text` to the agent at `base_url` and yield one dict per event.

    Args:
        base_url: the agent's base URL (its card is fetched from here).
        text: the message to send.
        task_id: pass a previous `done` event's `task_id` to resume a
            paused (INPUT_REQUIRED/AUTH_REQUIRED) task instead of starting
            a new one.
        http_client: reuse an existing httpx client if given; otherwise one
            is opened and closed for this call only.

    Yields:
        `{"type": "progress", "text": str}` for a WORKING status message,
        `{"type": "artifact", "text": str}` for each artifact chunk,
        `{"type": "message", "text": str}` for a message-mode reply, and
        finally exactly one `{"type": "done", "status": str, "task_id": str,
        "text": str, "status_message": str | None}` — `status` is the
        native TaskState name (e.g. "TASK_STATE_COMPLETED").
    """
    owns_client = http_client is None
    http_client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        client = await create_client(
            base_url, ClientConfig(streaming=True, httpx_client=http_client)
        )

        current_task_id = task_id or ""
        status = TaskState.TASK_STATE_UNSPECIFIED
        artifact_text: list[str] = []
        status_message: Optional[str] = None
        message_mode_text: Optional[str] = None

        message = _build_message(text, task_id)
        async for event in client.send_message(SendMessageRequest(message=message)):
            if event.HasField("task"):
                task = event.task
                current_task_id = task.id or current_task_id
                status = task.status.state
                for artifact in task.artifacts:
                    for part in artifact.parts:
                        if part.text:
                            artifact_text.append(part.text)
                if task.status.HasField("message"):
                    status_message = task.status.message.parts[0].text if task.status.message.parts else None

            elif event.HasField("status_update"):
                update = event.status_update
                current_task_id = update.task_id or current_task_id
                status = update.status.state
                if update.status.HasField("message"):
                    parts = update.status.message.parts
                    text_piece = parts[0].text if parts else None
                    if status == TaskState.TASK_STATE_WORKING and text_piece:
                        yield {"type": "progress", "text": text_piece}
                    status_message = text_piece

            elif event.HasField("artifact_update"):
                update = event.artifact_update
                current_task_id = update.task_id or current_task_id
                for part in update.artifact.parts:
                    if part.text:
                        artifact_text.append(part.text)
                        yield {"type": "artifact", "text": part.text}

            elif event.HasField("message"):
                # message-mode: a standalone reply, no Task involved at all.
                parts = event.message.parts
                message_mode_text = parts[0].text if parts else None
                if message_mode_text:
                    yield {"type": "message", "text": message_mode_text}

        yield {
            "type": "done",
            "status": TaskState.Name(status),
            "task_id": current_task_id,
            "text": message_mode_text if message_mode_text is not None else "".join(artifact_text),
            "status_message": status_message,
        }
    finally:
        if owns_client:
            await http_client.aclose()


async def cancel_agent(
    base_url: str,
    task_id: str,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """Cancel a task in flight (or paused).

    Args:
        base_url: the agent's base URL.
        task_id: the task to cancel.
        http_client: reuse an existing httpx client if given.

    Returns:
        `{"status": str, "status_message": str | None}`.
    """
    owns_client = http_client is None
    http_client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        client = await create_client(
            base_url, ClientConfig(streaming=True, httpx_client=http_client)
        )
        task = await client.cancel_task(CancelTaskRequest(id=task_id))
        status_message = None
        if task.status.HasField("message") and task.status.message.parts:
            status_message = task.status.message.parts[0].text
        return {"status": TaskState.Name(task.status.state), "status_message": status_message}
    finally:
        if owns_client:
            await http_client.aclose()
