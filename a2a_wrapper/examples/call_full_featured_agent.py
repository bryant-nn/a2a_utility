"""Drives full_featured_agent.py using only native_client.py — no
a2a_utility import anywhere, so testing an a2a_wrapper agent doesn't require
pulling in the whole a2a_utility package.

Run the server first, then this:

    python -m a2a_wrapper.examples.full_featured_agent &
    python -m a2a_wrapper.examples.call_full_featured_agent
"""

from __future__ import annotations

import asyncio

from a2a_wrapper.examples.native_client import cancel_agent, stream_agent

BASE_URL = "http://127.0.0.1:9070"


async def run(text: str, *, task_id: str | None = None) -> dict:
    """Send `text` and return the final `done` event (progress/artifact events are dropped)."""
    done = None
    async for event in stream_agent(BASE_URL, text, task_id=task_id):
        if event["type"] == "done":
            done = event
    assert done is not None
    return done


async def main() -> None:
    # ---- default path ------------------------------------------------
    result = await run("hello there")
    print("default        :", result["status"], "->", result["text"])

    # ---- streamed artifact: append/last_chunk chunks assemble into one
    # final artifact.
    result = await run("stream please")
    print("stream         :", result["status"], "-> assembled:", repr(result["text"]))

    # ---- pause + resume (INPUT_REQUIRED) -------------------------------
    first = await run("input please")
    assert first["status"] == "TASK_STATE_INPUT_REQUIRED"
    second = await run("boston", task_id=first["task_id"])
    print("input->resume  :", second["status"], "->", second["text"])

    # ---- pause + resume (AUTH_REQUIRED) ---------------------------------
    first = await run("auth please")
    second = await run("token-xyz", task_id=first["task_id"])
    print("auth->resume   :", second["status"], "->", second["text"])

    # ---- rejected -------------------------------------------------------
    result = await run("reject me")
    print("reject         :", result["status"], "->", result["status_message"])

    # ---- failed, with the real exception text preserved -----------------
    result = await run("fail now")
    print("fail           :", result["status"], "->", result["status_message"])

    # ---- cancel a paused task ---------------------------------------------
    paused = await run("input please")
    canceled = await cancel_agent(BASE_URL, paused["task_id"])
    print("cancel         :", canceled["status"], "->", canceled["status_message"])


if __name__ == "__main__":
    asyncio.run(main())
