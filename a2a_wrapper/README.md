# A2A Wrapper

A wrapper layer for the A2A (Agent-to-Agent) Protocol, providing a Domain-Driven Design (DDD) style architecture that allows agent developers to focus on business logic without dealing with underlying Protobuf and A2A SDK details.

> **Status**: domain-agent-executor only, in progress toward the same completeness as
> `a2a_utility`. No discovery node yet — see `a2a_utility.server` for that. This copy
> lives at the repo root (`a2a_wrapper/`, a sibling of `a2a_utility/`) because every
> internal import treats `a2a_wrapper` as its own top-level package
> (`from a2a_wrapper.events import ...`) — an earlier untracked copy at
> `a2a_utility/a2a_wrapper/` (nested inside the `a2a_utility` package directory) had
> several bugs that would crash against a real a2a-sdk 1.1.2 server; see "What changed"
> below.

## Design Philosophy

### The Problem

Using the A2A SDK directly requires handling:
- Protobuf serialization and deserialization
- Complex task lifecycle management (submit → working → complete/failed)
- Event queue operations
- Various state transitions

### The Solution

`a2a_wrapper` acts as an **Anti-Corruption Layer**:
- Isolates A2A SDK complexity within the framework layer
- Provides a clean domain-layer API
- Developers only need to implement a single async generator

## Quick Start

### 1. Implement Your Domain Executor

```python
from a2a_wrapper.events import ArtifactResult, StatusMessage, TextChunk
from a2a_wrapper.server.ports import DomainAgentExecutorPort
from a2a_wrapper.types import DomainContext, ExtendedPart


class MyChatExecutor(DomainAgentExecutorPort):
    async def execute(self, context: DomainContext):
        # Get user input
        user_text = context.get_text()
        if not user_text:
            raise ValueError('Empty input')

        # Phase 1: Push status update (e.g., thinking process)
        yield StatusMessage(
            parts=[ExtendedPart(text='Analyzing user intent...')]
        )

        # Phase 2: Stream generated results
        accumulated = []
        async for chunk in self._call_llm(user_text):
            accumulated.append(chunk)
            yield TextChunk(text=chunk)

        # Phase 3: Submit final result
        yield ArtifactResult(
            parts=[
                ExtendedPart(text=''.join(accumulated)),
            ],
        )

    async def _call_llm(self, text: str):
        # Your LLM call logic
        for word in ['Hello ', 'World']:
            yield word
```

### 2. Create and Start the Server

```python
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from a2a_wrapper.server.server_factory import create_a2a_server


agent_card = AgentCard(
    name="My AI Agent",
    description="An example agent",
    version="1.0",
    capabilities=AgentCapabilities(),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[AgentSkill(id="chat", name="Chat", description="General chat", tags=[])],
    # AgentCard has no plain `url=` field — the interface (and its transport
    # binding) is declared here instead.
    supported_interfaces=[AgentInterface(url="https://example.com", protocol_binding="JSONRPC")],
)

executor = MyChatExecutor()
app = create_a2a_server(agent_card, executor)

# Start with uvicorn
# uvicorn main:app --host 0.0.0.0 --port 8000
```

Run from the repo root — `a2a_wrapper` isn't pip-installed (unlike `a2a_utility`), so a
script run any other way won't find it on `sys.path`:

```bash
python -m a2a_wrapper.examples.full_featured_agent
```

## Event Types

| Event | Purpose | A2A State |
|-------|---------|-----------|
| `TextChunk` | Streaming text output | working |
| `StatusMessage` | Structured status updates (e.g., thinking process, progress) | working |
| `ArtifactResult` | Submit final or intermediate results — `artifact_id`/`append`/`last_chunk` stream one artifact across multiple chunks | - |
| `InputRequired` | Pause and request more input | input_required |
| `AuthRequired` | Pause and request authentication/authorization | auth_required |
| `Rejected` | Reject task (validation failed, safety policy, etc.) | rejected |

**Error Handling**: If `execute()` raises an exception, the framework calls `failed()`
with the real exception text attached (`"Agent error: <message>"`) — not a generic
message, so a caller (and whoever's debugging) can see what actually broke.

**Resuming after InputRequired/AuthRequired**: the framework re-invokes `execute()`
with a *new* call (same `task_id`, not the same coroutine) once the caller replies.
Check `context.is_resuming` and read `context.prior_parts` (what the pause message
said) to pick up where you left off:

```python
async def execute(self, context: DomainContext):
    if context.is_resuming:
        # context.prior_parts[0].text == whatever InputRequired/AuthRequired said
        yield ArtifactResult(parts=[ExtendedPart(text=f"you replied: {context.get_text()!r}")])
        return
    yield InputRequired(parts=[ExtendedPart(text="which city?")])
```

## Custom Data Types

Supports registering custom data types (e.g., Vercel thinking, source references):

```python
from a2a_wrapper.types import CustomizedData, DataType, ExtendedPart

# Use predefined types
yield StatusMessage(
    parts=[
        ExtendedPart(
            data=CustomizedData(
                data_type=DataType.VERCEL_THINKING,
                data_content={'text': 'Analyzing intent...'},
            )
        )
    ]
)

# Or use custom types (schema must be registered first)
```

## File Parts

`ExtendedPart`'s content is one of `text` / `raw` (bytes) / `url` / `data` — exactly one,
enforced at construction. `metadata` / `filename` / `media_type` may accompany any of them:

```python
yield ArtifactResult(parts=[
    ExtendedPart(url="https://example.com/report.pdf", filename="report.pdf",
                 media_type="application/pdf"),
    ExtendedPart(raw=image_bytes, filename="chart.png", media_type="image/png"),
])
```

## Architecture Overview

```
┌─────────────────────────────────────────┐
│         Domain Layer (Your Code)         │
│  DomainAgentExecutorPort                 │
│  ─ yield StreamEvent                     │
└───────────────┬─────────────────────────┘
                │
┌───────────────▼─────────────────────────┐
│      Anti-Corruption Layer               │
│  BaseA2AWrapperExecutor                  │
│  ─ Protobuf ↔ ExtendedPart               │
│  ─ Task Lifecycle Management             │
└───────────────┬─────────────────────────┘
                │
┌───────────────▼─────────────────────────┐
│         A2A SDK Layer                    │
│  AgentExecutor, EventQueue, Protobuf     │
└─────────────────────────────────────────┘
```

## Advanced Usage

### Context Object

`DomainContext` contains:
- `task_id`, `context_id`: Task and conversation IDs
- `parts`: Parsed user input (`ExtendedPart` list)
- `metadata`: Metadata from the request
- `configuration`: Configuration parameters
- `is_resuming`: True when this call is a continuation after InputRequired/AuthRequired
- `prior_parts`: what the pause message said, only meaningful when `is_resuming` is True

### Helper Methods

```python
# Extract all text content
user_text = context.get_text()

# Extract data parts of a specific type
data_parts = context.get_data_parts(DataType.VERCEL_THINKING)
```

### Cancellation Handling

Optionally implement the `cancel()` method. The task is marked CANCELED regardless of
what this returns or whether it's overridden at all — the return value only supplies
an optional message attached to that status:

```python
async def cancel(self, context: DomainContext) -> str | None:
    # Clean up resources, abort LLM calls, etc.
    return "cleanly stopped on request"
```

## What changed from the original sketch

The version that used to live at `a2a_utility/a2a_wrapper/` had a few bugs that would
crash or silently misbehave against a real a2a-sdk 1.1.2 server, found and fixed while
getting this copy to actually run end-to-end:

- **`submit()`/`start_work()` fired before any `Task` event was enqueued** — native
  `TaskUpdater.update_status()` doesn't send one automatically, and the framework
  rejects any status event that arrives first (`InvalidAgentResponseError`). Fixed by
  building and enqueueing the task's initial `Task` object first.
- **`TaskState.input_required`/`.auth_required`/`.rejected`** aren't real attributes on
  the native enum (`TASK_STATE_INPUT_REQUIRED` etc. are) — would raise `AttributeError`
  the moment any of those three events fired.
- **`ArtifactResult.artifact_id`/`.append`/`.last_chunk`/`.metadata` were defined but
  silently dropped** — chunked artifact streaming didn't actually work even though the
  event advertised it.
- **No resume support at all** — `InputRequired`/`AuthRequired` paused a task, but
  `DomainContext` had no way to tell a resumed call from a fresh one, so an executor
  couldn't know it was continuing (or what it had just asked). Added `is_resuming`/
  `prior_parts`, and skipped the unconditional `submit()`/`start_work()` on resume —
  sending SUBMITTED→WORKING on a task already paused at INPUT_REQUIRED/AUTH_REQUIRED
  was observed to break the framework's routing for that resumed call.
- **`cancel()` didn't shield the domain's own `cancel()` from raising**, and had the
  same missing-initial-`Task` risk as `execute()` if a cancel arrived before any Task
  was ever sent. `DomainAgentExecutorPort.cancel()` can now also return an optional
  message attached to the CANCELED status.
- **The exception handler swallowed the real error into a generic `"Internal server
  error"`** — changed to preserve the actual exception text, matching the decision
  `a2a_utility` made and documented for the same trade-off (deliberately not the
  original behavior — flagging it here rather than changing it silently).
- **Broken/inconsistent imports everywhere** (`ai4bi_utils.a2a_wrapper.x` in some
  files, bare `a2a_wrapper.x` in another, no `__init__.py` anywhere) — the package as
  originally pasted couldn't be imported at all. Fixed to consistently use
  `a2a_wrapper.x` throughout.
- **No shutdown lifespan** — `server_factory.py` never called
  `DefaultRequestHandlerV2.aclose()`, the same pending-asyncio-task-on-shutdown issue
  `a2a_utility` found and fixed. Added.

All of the above were verified against a real running server (real ASGI transport, real
native `a2a.client`, not just import-checked) — every event type, the resume round trip,
reject, fail-with-real-message, and cancel-with-custom-message all confirmed working.

## Project Structure

```
a2a_wrapper/                      # repo-root sibling of a2a_utility/
├── types.py            # Domain data type definitions
├── events.py            # Domain event definitions
├── examples/
│   └── full_featured_agent.py    # runnable, exercises every event type
├── server/
│   ├── base_executor.py  # A2A adapter implementation
│   ├── server_factory.py # Server creation factory
│   └── ports/
│       └── domain_agent_executor.py  # Port interface
└── README.md
```

## Developer Checklist

When implementing your Domain Executor:
- [ ] Inherit from `DomainAgentExecutorPort`
- [ ] Implement `execute()` method returning `AsyncIterator[StreamEvent]`
- [ ] Choose appropriate event types based on scenarios
- [ ] Handle exceptions (raising exceptions triggers `failed()`, with the real message)
- [ ] Check `context.is_resuming`/`context.prior_parts` if you use InputRequired/AuthRequired
- [ ] (Optional) Implement `cancel()` to handle cancellation requests, optionally
      returning a message to attach to the CANCELED status

## Not yet covered

Explicitly out of scope for this pass (per the current ask — domain agent executor
only):
- A discovery node (`a2a_utility.server`'s `ServerMode.DISCOVERY` equivalent)
- Message-mode (a standalone reply with no `Task` ever created) — `a2a_utility` found
  this useful; this package's event vocabulary doesn't have an equivalent yet
- Packaging (`pyproject.toml`) so this can be `pip install`ed on its own
