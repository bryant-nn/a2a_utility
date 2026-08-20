"""Drives every path in full_featured_agent.py — run that server first:

    python examples/full_featured_agent.py &
    python examples/call_full_featured_agent.py

Shows the caller-side half of each capability: what a2a_utility.client sees
back for each task outcome, and how to continue a paused task (task_id=) or
cancel one in flight.
"""

from __future__ import annotations

import asyncio

from a2a_utility.client import A2ACallError, ExtendedAgentClient
from a2a_utility.schema import ExtendedTaskState

BASE_URL = "http://127.0.0.1:9060"


async def main() -> None:
    async with ExtendedAgentClient(BASE_URL) as agent:
        # ---- default path ------------------------------------------------
        result = await agent.send_result("hello there")
        print("default        :", result.status, "->", result.text())

        # ---- streamed artifact: append/last_chunk chunks assemble into one
        # final artifact. emit= only fires for Progress (WORKING) messages
        # and message-mode replies, not per artifact chunk — the chunks
        # arrive as separate wire events but the caller only sees them
        # already assembled in the final result.
        progress_seen: list[str] = []

        async def collect(part) -> None:
            if part.text:
                progress_seen.append(part.text)

        result = await agent.send_result("stream please", emit=collect)
        print("stream         :", result.status, "-> assembled:", repr(result.text()),
              "| progress seen live:", progress_seen)

        # ---- message-mode: no task_id at all -------------------------------
        result = await agent.send_result("just a message")
        print("message-mode   :", result.status, "is_message_mode=", result.is_message_mode,
              "task_id=", repr(result.task_id))

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

        # ---- cancel a task in flight ------------------------------------------
        # A paused task (INPUT_REQUIRED/AUTH_REQUIRED) is the easy case to
        # demo: it returns immediately with a task_id, still "in flight" from
        # the server's point of view (waiting on the caller), so there's
        # something real to cancel. Same RPC works on a task that's still
        # streaming — you'd just need its task_id from wherever your own
        # code tracks in-flight work.
        paused = await agent.send_result("input please")
        canceled = await agent.cancel_task(paused.task_id)
        print("cancel         :", canceled.state, "->", canceled.status_message.text()
              if canceled.status_message else None)


if __name__ == "__main__":
    asyncio.run(main())
