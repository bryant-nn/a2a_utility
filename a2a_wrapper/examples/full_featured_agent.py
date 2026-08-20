"""Example domain agent exercising every capability a2a_wrapper's
BaseA2AWrapperExecutor currently offers.

Run it from the repo root (a2a_wrapper isn't pip-installed yet, unlike
a2a_utility — the -m form puts the repo root on sys.path so `import
a2a_wrapper` resolves; a plain `python a2a_wrapper/examples/...py` will not):

    python -m a2a_wrapper.examples.full_featured_agent

Send a message containing one of these words to exercise each path (see
a2a_utility/examples/ for a client-side driver pattern you can adapt —
a2a_wrapper doesn't have its own client yet, it only covers the
server/domain-agent-executor side so far):

    (default)  -> TextChunk + ArtifactResult, then COMPLETED
    "stream"   -> one artifact streamed across several chunks (append/last_chunk)
    "input"    -> pauses INPUT_REQUIRED, resumes on the next call with the
                  same task_id (context.is_resuming / context.prior_parts)
    "auth"     -> pauses AUTH_REQUIRED, same resume mechanics
    "reject"   -> ends REJECTED outright
    "fail"     -> raises; the real exception text reaches the caller as
                  FAILED's status message
"""

from __future__ import annotations

from a2a_wrapper import (
    ArtifactResult,
    AuthRequired,
    DomainAgentExecutorPort,
    DomainContext,
    ExtendedAgentCard,
    ExtendedAgentSkill,
    ExtendedPart,
    InputRequired,
    Rejected,
    StatusMessage,
    TextChunk,
    create_a2a_server,
)

HOST, PORT = "127.0.0.1", 9070


class FullFeaturedAgent(DomainAgentExecutorPort):
    async def execute(self, context: DomainContext):
        """Pick a path by keyword; yield the matching StreamEvent(s).

        Args:
            context: this turn's input, or prior-pause state if resuming.
        """
        text = context.get_text().strip().lower()

        # resume: fresh execute() call, same task_id, after InputRequired/AuthRequired
        if context.is_resuming:
            prior = context.prior_parts[0].text if context.prior_parts else None
            yield ArtifactResult(
                parts=[ExtendedPart(text=f"resumed (was asked: {prior!r}), you said: {text!r}")]
            )
            return

        if "input" in text:
            yield InputRequired(parts=[ExtendedPart(text="which city?")])
            return

        if "auth" in text:
            yield AuthRequired(parts=[ExtendedPart(text="please provide credentials")])
            return

        if "reject" in text:
            yield Rejected(parts=[ExtendedPart(text="not authorized for this request")])
            return

        if "fail" in text:
            raise ValueError(f"could not handle: {text!r}")

        if "stream" in text:
            yield StatusMessage(parts=[ExtendedPart(text="composing a long answer in pieces...")])
            words = ["This ", "answer ", "arrives ", "in ", "chunks."]
            for i, word in enumerate(words):
                yield ArtifactResult(
                    parts=[ExtendedPart(text=word)],
                    artifact_id="streamed-answer",
                    append=i > 0,
                    last_chunk=(i == len(words) - 1),
                )
            return

        yield TextChunk(text="thinking...")
        yield ArtifactResult(parts=[ExtendedPart(text=f"you said: {text!r}")])

    async def cancel(self, context: DomainContext) -> str | None:
        """React to an externally requested cancel RPC.

        Args:
            context: the task's context at the time of cancellation.

        Returns:
            A message attached to the CANCELED status (task is canceled either way).
        """
        return "cleanly stopped on request"


if __name__ == "__main__":
    import uvicorn

    card = ExtendedAgentCard(
        name="a2a_wrapper_full_featured_example",
        description="Demonstrates every BaseA2AWrapperExecutor capability",
        port=PORT,
        host=HOST,
        version="1.0",
        skills=[
            ExtendedAgentSkill(
                id="demo",
                name="Demo",
                description="Try 'stream', 'input', 'auth', 'reject', 'fail', or anything else",
            )
        ],
    )
    app = create_a2a_server(card, FullFeaturedAgent(), rpc_url="/")
    uvicorn.run(app, host=card.host, port=card.port)
