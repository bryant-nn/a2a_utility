"""Drives full_featured_agent.py (a2a_wrapper) using a2a_utility.client.

a2a_wrapper doesn't have its own client *package* yet (see native_client.py
for a minimal, dependency-free one) — but it speaks the same native a2a wire
protocol a2a_utility's server does, so a2a_utility.client works against it
unmodified. This is the proof, and the pattern to copy for testing any
a2a_wrapper agent with a2a_utility's richer client in the meantime.

Run the server first, then this:

    python -m a2a_wrapper.examples.full_featured_agent &
    python -m a2a_wrapper.examples.call_full_featured_agent
"""

from __future__ import annotations

import asyncio

from a2a_utility.client import A2ACallError, ExtendedAgentClient
from a2a_utility.schema import ExtendedTaskState

BASE_URL = "http://127.0.0.1:9070"


async def main() -> None:
    async with ExtendedAgentClient(BASE_URL) as agent:
        # ---- default path ------------------------------------------------
        result = await agent.send_result("hello there")
        print("default        :", result.status, "->", result.text())

        # ---- streamed artifact: append/last_chunk chunks assemble into one
        # final artifact.
        result = await agent.send_result("stream please")
        print("stream         :", result.status, "-> assembled:", repr(result.text()))

        # ---- pause + resume (INPUT_REQUIRED) -------------------------------
        first = await agent.send_result("input please")
        assert first.status == ExtendedTaskState.INPUT_REQUIRED
        second = await agent.send_result("boston", task_id=first.task_id)
        print("input->resume  :", second.status, "->", second.text())

        # ---- pause + resume (AUTH_REQUIRED) ---------------------------------
        first = await agent.send_result("auth please")
        second = await agent.send_result("token-xyz", task_id=first.task_id)
        print("auth->resume   :", second.status, "->", second.text())

        # ---- rejected -------------------------------------------------------
        try:
            await agent.send("reject me")
        except A2ACallError as e:
            print("reject         : A2ACallError as expected ->", e.status, e.detail)

        # ---- failed, with the real exception text preserved -----------------
        try:
            await agent.send("fail now")
        except A2ACallError as e:
            print("fail           : A2ACallError as expected ->", e.status, e.detail)

        # ---- cancel a paused task ---------------------------------------------
        paused = await agent.send_result("input please")
        canceled = await agent.cancel_task(paused.task_id)
        print("cancel         :", canceled.state, "->", canceled.status_message.text()
              if canceled.status_message else None)


if __name__ == "__main__":
    asyncio.run(main())
