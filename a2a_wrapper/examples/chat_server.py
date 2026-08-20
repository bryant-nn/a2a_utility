"""A tiny web front end for talking to an a2a_wrapper-served agent —
serves index.html and bridges it to native_client.py's SSE stream.

Run the domain agent first, then this:

    python -m a2a_wrapper.examples.full_featured_agent &
    python -m a2a_wrapper.examples.chat_server

Then open http://127.0.0.1:8199 in a browser. AGENT_URL below points at
full_featured_agent.py's default address — change it (or set the
A2A_AGENT_URL env var) to point at any other a2a_wrapper or native a2a
agent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from a2a_wrapper.examples.native_client import cancel_agent, stream_agent

AGENT_URL = os.environ.get("A2A_AGENT_URL", "http://127.0.0.1:9070")
HOST, PORT = "127.0.0.1", 8199
INDEX_HTML = Path(__file__).parent / "index.html"


async def index(request: Request) -> FileResponse:
    return FileResponse(INDEX_HTML)


async def info(request: Request) -> JSONResponse:
    return JSONResponse({"agent_url": AGENT_URL})


async def chat_stream(request: Request) -> StreamingResponse:
    message = request.query_params.get("message", "")
    task_id = request.query_params.get("task_id") or None

    async def sse() -> AsyncIterator[str]:
        async for event in stream_agent(AGENT_URL, message, task_id=task_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


async def cancel(request: Request) -> JSONResponse:
    body = await request.json()
    task_id = body.get("task_id")
    if not task_id:
        return JSONResponse({"error": "task_id required"}, status_code=400)
    result = await cancel_agent(AGENT_URL, task_id)
    return JSONResponse(result)


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/info", info),
        Route("/api/chat/stream", chat_stream),
        Route("/api/cancel", cancel, methods=["POST"]),
    ]
)


if __name__ == "__main__":
    print(f"agent: {AGENT_URL}  ->  serving on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
