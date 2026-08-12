"""Outbound A2A client: SendStreamingMessage over JSON-RPC + SSE.

Generalized from root_agent_deep_agent's original hand-rolled a2a_client.py —
any coordinator that can await a coroutine (google-adk via a plain async tool,
LangChain deepagents, a script, a test) can call a domain agent through
call_agent() without knowing the A2A wire format. Confirmed against a live
capture of a real SendStreamingMessage exchange against a2a_utility.server's
own ChatAgentExecutor:

  data: {"result": {"task": {..., "status": {"state": "TASK_STATE_SUBMITTED"}}}}
  data: {"result": {"statusUpdate": {"status": {"state": "TASK_STATE_WORKING",
                                                 "message": {"parts": [{"text": "..."}]}}}}}
  data: {"result": {"artifactUpdate": {"artifact": {"parts": [{"text": "...", "mediaType": "text/plain"}]}}}}
  data: {"result": {"statusUpdate": {"status": {"state": "TASK_STATE_COMPLETED"}}}}

WORKING messages are forwarded through emit_thought as they arrive; the
concatenated artifact text (not any trailing status message) is the return
value — a2a_utility.server never sends a message on the final COMPLETED
status precisely so callers here don't have to guess which text is the real
answer (see adapters/inbound/chat_agent_executor.py's docstring).

google-adk coordinators get an equivalent client for free from
RemoteA2aAgent/AgentTool; this module exists for every framework that doesn't
ship one (deepagents today, anything else tomorrow) and is also just a
convenient plain httpx client for scripts/tests.
"""

import json
import uuid
from typing import Awaitable, Callable, Optional

import httpx

ThoughtEmitter = Callable[[str], Awaitable[None]]


class A2ACallError(RuntimeError):
    pass


async def call_agent(
    base_url: str,
    text: str,
    *,
    emit_thought: Optional[ThoughtEmitter] = None,
    timeout: Optional[float] = None,
) -> str:
    """Sends `text` to the A2A agent at base_url and returns its final answer.

    Args:
      base_url: the agent's A2A endpoint, e.g. "http://127.0.0.1:9040/"
        (the same URL a RemoteA2aAgent/agent card resolves to).
      text: the self-contained question/task to send.
      emit_thought: awaited with each WORKING-status message as it streams in
        (the agent's "thinking process"); omit to just wait for the answer.
      timeout: httpx timeout in seconds; None waits indefinitely (the default,
        since streamed thinking has no fixed upper bound).

    Raises:
      A2ACallError: the remote task reached TASK_STATE_FAILED.
      httpx.HTTPError: the request itself failed (connection refused, non-2xx, ...).
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
    answer_parts: list[str] = []

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
                        parts = (message or {}).get("parts", [])
                        detail = parts[0].get("text") if parts else "unknown error"
                        raise A2ACallError(f"agent at {base_url} failed: {detail}")
                    continue

                artifact_update = result.get("artifactUpdate")
                if artifact_update:
                    for part in artifact_update.get("artifact", {}).get("parts", []):
                        if part.get("text"):
                            answer_parts.append(part["text"])

    return "".join(answer_parts)
