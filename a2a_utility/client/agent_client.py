"""Outbound A2A client: SendStreamingMessage over JSON-RPC + SSE.

Any coordinator that can await a coroutine (google-adk via a plain async tool,
LangChain deepagents, a script, a test) can call a domain agent through
call_agent() without knowing the A2A wire format.

The server (a2a_utility.server, Executor-Callback model) returns its answer as an
Artifact of Typed Parts — each part tagged with metadata.dataType (text /
source_reference / thinking_process / file). Two entry points:

  - call_agent(...)       -> str: the concatenated `text`-dataType parts (the
    answer). Non-text parts are ignored. Back-compatible with callers that just
    want the answer string.
  - call_agent_parts(...) -> list[Part]: every Typed Part, for callers that want
    source references / thinking / files too.

Either way, WORKING status messages are forwarded live through emit_thought as
they stream in, and the final COMPLETED status carries no message (the server
omits it so the artifact is unambiguously the answer).
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import httpx

ThoughtEmitter = Callable[[str], Awaitable[None]]


class A2ACallError(RuntimeError):
    pass


@dataclass
class Part:
    """A Typed Part as seen by the client (mirror of the server's Data Contract)."""

    data: Any
    data_type: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_artifact_parts(wire_parts: list[dict]) -> list[Part]:
    parts: list[Part] = []
    for wp in wire_parts:
        metadata = wp.get("metadata") or {}
        data_type = metadata.get("dataType", "text")
        if "text" in wp:
            parts.append(Part(data=wp["text"], data_type=data_type, metadata=metadata))
        elif "data" in wp:
            parts.append(Part(data=wp["data"], data_type=data_type, metadata=metadata))
    return parts


async def call_agent_parts(
    base_url: str,
    text: str,
    *,
    emit_thought: Optional[ThoughtEmitter] = None,
    timeout: Optional[float] = None,
) -> list[Part]:
    """Send `text` to the A2A agent at base_url; return its Artifact's Typed Parts.

    Args:
      base_url: the agent's A2A endpoint, e.g. "http://127.0.0.1:9040/".
      text: the self-contained question/task to send.
      emit_thought: awaited with each WORKING-status message as it streams in.
      timeout: httpx timeout in seconds; None waits indefinitely.

    Raises:
      A2ACallError: the remote task reached TASK_STATE_FAILED.
      httpx.HTTPError: the request itself failed.
    """
    request_body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendStreamingMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": text}],
            }
        },
    }
    parts: list[Part] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            base_url,
            headers={
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
                "Accept": "text/event-stream",
            },
            json=request_body,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                frame = json.loads(line[len("data:") :].strip())
                result = frame.get("result")
                if not result:
                    continue

                status_update = result.get("statusUpdate")
                if status_update:
                    status = status_update.get("status", {})
                    state = status.get("state")
                    message = status.get("message")
                    if state == "TASK_STATE_WORKING" and message:
                        for part in message.get("parts", []):
                            if part.get("text") and emit_thought:
                                await emit_thought(part["text"])
                    elif state == "TASK_STATE_FAILED":
                        msg_parts = (message or {}).get("parts", [])
                        detail = msg_parts[0].get("text") if msg_parts else "unknown error"
                        raise A2ACallError(f"agent at {base_url} failed: {detail}")
                    continue

                artifact_update = result.get("artifactUpdate")
                if artifact_update:
                    wire_parts = artifact_update.get("artifact", {}).get("parts", [])
                    parts.extend(_parse_artifact_parts(wire_parts))

    return parts


def parts_to_text(parts: list[Part]) -> str:
    """Concatenate the `text`-dataType parts (the answer)."""
    return "".join(str(p.data) for p in parts if p.data_type == "text")


async def call_agent(
    base_url: str,
    text: str,
    *,
    emit_thought: Optional[ThoughtEmitter] = None,
    timeout: Optional[float] = None,
) -> str:
    """Send `text` to the A2A agent at base_url and return its final answer text.

    Convenience over call_agent_parts(): returns only the concatenated
    `text`-dataType parts. Use call_agent_parts() for the full Typed Parts.
    """
    parts = await call_agent_parts(
        base_url, text, emit_thought=emit_thought, timeout=timeout
    )
    return parts_to_text(parts)
