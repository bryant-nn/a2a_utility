"""Example domain agent showing every capability `a2a_utility.server` offers
an agent developer, in one file.

Run it:

    python examples/full_featured_agent.py

Then drive it from `examples/call_full_featured_agent.py` (or curl, or your
own coordinator) — send a message containing one of these words to exercise
each path:

    (default)  -> Progress + a single artifact, then COMPLETED
    "stream"   -> the answer streamed across several artifact chunks
                  (append/last_chunk)
    "input"    -> pauses INPUT_REQUIRED, resumes on the next call with the
                  same task_id
    "auth"     -> pauses AUTH_REQUIRED, same resume mechanics
    "reject"   -> ends REJECTED outright
    "message"  -> message-mode: an immediate reply, no Task ever created
    "fail"     -> raises; the real exception text reaches the caller as
                  FAILED's status message
    "slow"     -> sleeps between chunks, so there's time to send a cancel
                  RPC while it's running (see the client script)

Not one `a2a.*` import below — that's the point of writing against
`DomainAgentExecutorPort` instead of the native SDK directly.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from a2a_utility.server import (
    AuthRequired,
    DomainAgentExecutorPort,
    ExtendedAgentCard,
    ExtendedAgentSkill,
    ExtendedRequestContext,
    InputRequired,
    MessageReply,
    Progress,
    PublishArtifact,
    Rejected,
    TaskEvent,
    serve_as_a2a,
)
from a2a_utility.schema import ExtendedPart, MessageLike


class FullFeaturedAgent(DomainAgentExecutorPort):
    """One branch per capability, picked by a keyword in the request text.
    A real agent normally only needs a handful of these — this file exists
    to show the whole menu in one place."""

    async def execute(self, context: ExtendedRequestContext) -> AsyncIterator[TaskEvent]:
        text = context.get_user_input().strip().lower()

        # ---- resume: this execute() call is a *new* one, re-invoked by the
        # framework after a prior INPUT_REQUIRED/AUTH_REQUIRED pause, with
        # the same task_id. Check this before any keyword branch below. ---
        if context.is_resuming:
            async for event in self._resume(context):
                yield event
            return

        # ---- message-mode: no Task is ever created for this request ------
        if "message" in text:
            yield MessageReply("this is a message-mode reply — no task_id, no task history")
            return

        # ---- pause, waiting on the caller ---------------------------------
        if "input" in text:
            yield InputRequired("which city?")
            return

        if "auth" in text:
            yield AuthRequired("please provide credentials")
            return

        # ---- reject outright (not attempted-and-broken, just refused) -----
        if "reject" in text:
            yield Rejected("not authorized for this request")
            return

        # ---- raise: the real exception text reaches the caller ------------
        if "fail" in text:
            raise ValueError(f"could not handle: {text!r}")

        # ---- stream one artifact across several chunks, slowly enough to --
        # ---- cancel mid-flight if the text also says "slow" ---------------
        if "stream" in text:
            yield Progress("composing a long answer in pieces...")
            words = ["This ", "answer ", "arrives ", "in ", "chunks."]
            for i, word in enumerate(words):
                if "slow" in text:
                    await asyncio.sleep(1)
                yield PublishArtifact(
                    parts=[ExtendedPart.from_text(word)],
                    artifact_id="streamed-answer",
                    append=i > 0,
                    last_chunk=(i == len(words) - 1),
                )
            return

        # ---- default: Progress + one artifact, then implicit COMPLETED ----
        yield Progress(ExtendedPart.thinking(f"looking into: {text!r}"))
        yield PublishArtifact(
            parts=[
                ExtendedPart.source_reference([{"source": "example", "note": "no real data source"}]),
                ExtendedPart.from_text(f"you said: {text!r}"),
            ]
        )
        # Falling off the end here is what marks the task COMPLETED — no
        # explicit "done" event to yield.

    async def _resume(self, context: ExtendedRequestContext) -> AsyncIterator[TaskEvent]:
        """`context.current_task` carries the prior state/history — the
        coroutine that paused is gone, this is a fresh call."""
        reply = context.get_user_input()
        prior_state = context.current_task.state if context.current_task else None
        yield PublishArtifact(
            parts=[ExtendedPart.from_text(f"resumed from {prior_state}, you said: {reply!r}")]
        )

    async def cancel(self, context: ExtendedRequestContext) -> Optional[MessageLike]:
        """Optional: reacts to an externally requested cancel RPC. The task
        is marked CANCELED regardless of what (or whether) this returns —
        this only supplies the message attached to that status. Real
        cleanup doesn't need this override at all: the asyncio task running
        execute() is cancelled by the framework independently, so a
        try/finally around the loop above would already run."""
        return "cleanly stopped on request"


if __name__ == "__main__":
    serve_as_a2a(
        executor=FullFeaturedAgent(),
        card=ExtendedAgentCard(
            name="full_featured_example",
            description="Demonstrates every DomainAgentExecutorPort capability",
            port=9060,
            skills=[
                ExtendedAgentSkill(
                    id="demo",
                    name="Demo",
                    description="Try 'stream', 'input', 'auth', 'reject', 'message', 'fail', 'slow', or anything else",
                )
            ],
        ),
    )
