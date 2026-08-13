"""Inbound port: the callback an agent developer implements.

adjust.md §step-2. The server hands the callback a normalized Task plus a mock
UserContext and expects a list of Typed Parts back; the server then packs them
into an Artifact on the A2A Task response.

    async def my_executor(task: Task, ctx: UserContext) -> list[Part]:
        # run LLM / MCP tool ...
        return [Part.thinking_process("..."), Part.text("the answer")]
"""

from typing import Awaitable, Callable

from ....domain.models.part import Part
from ...dtos import Task, UserContext

ExecutorCallback = Callable[[Task, UserContext], Awaitable[list[Part]]]
