"""handler_to_executor — bridge a legacy (text, emit_thought) -> str handler
onto the new ExecutorCallback (Task, UserContext) -> list[Part].

The domain agents already have good framework-specific `handle()` functions that
stream reasoning through `emit_thought` and return a final answer string. This
adapts one into the Typed-Parts model without rewriting its internals:

  - each emitted thought is forwarded live (UserContext.emit_thought) AND
    collected into a `thinking_process` Part,
  - the returned string becomes the final `text` Part.
"""

from typing import Awaitable, Callable

from ..domain.models.part import Part
from .dtos import Task, ThoughtEmitter, UserContext
from .ports.inbound.executor_callback import ExecutorCallback

LegacyHandler = Callable[[str, ThoughtEmitter], Awaitable[str]]


def handler_to_executor(handler: LegacyHandler) -> ExecutorCallback:
    async def executor(task: Task, ctx: UserContext) -> list[Part]:
        thoughts: list[str] = []

        async def emit(text: str) -> None:
            thoughts.append(text)
            if ctx.emit_thought is not None:
                await ctx.emit_thought(text)  # live WORKING stream

        answer = await handler(task.message, emit)

        parts: list[Part] = [Part.thinking_process(t) for t in thoughts]
        parts.append(Part.text(answer))
        return parts

    return executor
