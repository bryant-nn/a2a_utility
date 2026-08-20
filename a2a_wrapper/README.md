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
from a2a_wrapper.server import ExtendedAgentCard, ExtendedAgentSkill, create_a2a_server


agent_card = ExtendedAgentCard(
    name="My AI Agent",
    description="An example agent",
    port=8000,
    # host defaults to 127.0.0.1; version/input/output modes/capabilities
    # are defaulted internally too, all still overridable.
    skills=[ExtendedAgentSkill(id="chat", name="Chat", description="General chat")],
)

executor = MyChatExecutor()
app = create_a2a_server(agent_card, executor)

# Start with uvicorn
# uvicorn main:app --host {agent_card.host} --port {agent_card.port}
```

Run from the repo root — `a2a_wrapper` isn't pip-installed (unlike `a2a_utility`), so a
script run any other way won't find it on `sys.path`:

```bash
python -m a2a_wrapper.examples.full_featured_agent
```

### Try it in a browser

```bash
python -m a2a_wrapper.examples.full_featured_agent &
python -m a2a_wrapper.examples.chat_server
```

Open `http://127.0.0.1:8199`. `chat_server.py` is a thin SSE bridge over `native_client.py`
(a client built directly on `a2a.client`, no `a2a_utility` dependency) — `index.html` shows
progress/artifact events live as they stream in, tracks the `task_id` so a paused
(INPUT_REQUIRED/AUTH_REQUIRED) task resumes on your next message instead of starting a new
one, and has a Cancel button. Point `A2A_AGENT_URL` at any other a2a_wrapper (or native a2a)
agent to use it against something else.

### What to test

`full_featured_agent.py` picks a path by keyword — case-insensitive, matched anywhere in
what you type (not just the browser UI; the same keywords work through
`call_full_featured_agent.py` or your own script against `native_client.py`):

| Type | What happens | TaskEvent |
|---|---|---|
| anything else | one "thinking..." progress bubble, then the final answer, COMPLETED | `TextChunk` + `ArtifactResult` |
| `stream please` | 3 "thinking" bubbles ~1s apart ("analyzing the request..." → "breaking it down into steps..." → "composing..."), then the answer streamed in across 6 artifact chunks ~0.4s apart, then a source list | `StatusMessage` × 3 (each a `vercel_thinking` data part), then `ArtifactResult` × 6 (`append`/`last_chunk`; the last chunk also carries a `source_reference` data part) |
| `input please` | pauses INPUT_REQUIRED, asks "which city?" | `InputRequired` |
| `auth please` | pauses AUTH_REQUIRED, asks for credentials | `AuthRequired` |
| `reject me` | ends REJECTED outright | `Rejected` |
| `fail now` | raises; the real exception text reaches you as the FAILED status message | an exception, not an event |

**Resume**: send `input please` (or `auth please`) — the task pauses waiting on you. In the
browser UI, just type your reply next (`boston`, say) and hit send — it automatically resends
the same `task_id`, so the agent sees `context.is_resuming=True` and picks up where it left
off instead of starting over. Doing this by hand (script/curl) means passing `task_id=` from
the paused response's `task_id` on your next call.

**Cancel**: pause a task (`input please`), then hit Cancel in the UI (or call
`native_client.cancel_agent(base_url, task_id)` directly). The agent's `cancel()` override
returns a message that lands on the CANCELED status.

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

`ExtendedPart.data` carries a `CustomizedData` envelope (`{data_type, data_content}`) instead
of plain text — a structural way to tag a part's meaning so a frontend can render it
differently from an ordinary status/artifact, without parsing text. Two are predefined:

| `DataType` | Content shape | Use it when |
|---|---|---|
| `VERCEL_THINKING` | `{"text": str}` | narrating a reasoning/thinking step (as opposed to a plain progress message) — the name follows the Vercel AI SDK convention of a distinct "reasoning" message part, so a UI can show it collapsed/muted and separate from the final answer instead of mixing it into regular status text |
| `SOURCE_REFERENCE` | `{"merged_reference": list[str]}` | attaching the citations/sources an answer draws from — typically as an extra part on the final `ArtifactResult` chunk, alongside (not merged into) the closing text part |

```python
from a2a_wrapper.types import CustomizedData, DataType, ExtendedPart

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

`full_featured_agent.py`'s `stream please` path demonstrates both live — the 3 thinking
steps are `vercel_thinking` data parts, and the final chunk carries a `source_reference`
data part alongside its text. `native_client.py` decodes both off the wire (reading
`google.protobuf.Struct` directly, no `a2a_wrapper` import) into `{"type": "thinking", ...}`
/ `{"type": "sources", ...}` SSE events, and `index.html` renders them as their own bubble
styles — see `_data_payload()` in `native_client.py` for the parsing pattern to copy
against any other data type you register.

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

Since then, brought closer to parity with `a2a_utility` on request:

- **`ExtendedAgentCard`/`ExtendedAgentSkill`/`ExtendedAgentProvider` added**
  (`server/card.py`) — mirrors `a2a_utility/server/card.py` field-for-field, so
  `create_a2a_server()` and the example never import `a2a.types` directly (the original
  sketch's example did, to build the card).
- **`ExtendedPart` expanded from `text`/`data`-only to the full `text`/`raw`/`url`/`data`
  oneof**, plus `filename`/`media_type`. The "at least one" validator generalized to
  "exactly one of the four" — deliberately stricter than `a2a_utility`'s own `ExtendedPart`,
  which allows zero (see below).
- **`to_protobuf()` rewritten to build the `Part` via `a2a.helpers`'s own
  `new_text_part`/`new_raw_part`/`new_url_part`/`new_data_part`**, instead of hand-rolled
  `Part(**kwargs)` with manual `Struct`/`Value` construction — found the hard way that
  `Part.data` is a `google.protobuf.Value` but `Part.metadata` is a plain `Struct` (two
  different types); the hand-rolled version shared one conversion helper for both, which
  crashes for one of them (verified: `Part(metadata=Value(...))` raises `AttributeError`,
  `Part(data=Struct(...))` raises `TypeError`). Matches `a2a_utility` exactly now,
  including `part.metadata.update(...)` after construction.

**A known, currently-unresolved difference from `a2a_utility`'s `ExtendedPart`**: on an
unrecognized `data` payload, `a2a_utility.schema.parts.ExtendedPart.from_protobuf()` has
no try/except around `CustomizedData(**MessageToDict(...))` and raises `ValidationError`
straight out (verified); this package's `from_protobuf()` catches that and falls back to
JSON-dumped `text`, per its own "never raises" docstring. Neither is a bug — they're just
different resilience postures that happened independently. Not aligned yet.

## Project Structure

```
a2a_wrapper/                      # repo-root sibling of a2a_utility/
├── types.py            # Domain data type definitions (ExtendedPart, DomainContext, ...)
├── events.py            # Domain event definitions (StreamEvent and its variants)
├── examples/
│   ├── full_featured_agent.py         # runnable domain agent, exercises every event type
│   ├── call_full_featured_agent.py    # scripted walk through every path, via native_client.py
│   ├── native_client.py               # a minimal client on bare a2a.client, no a2a_utility
│   ├── chat_server.py                 # SSE bridge for index.html, built on native_client.py
│   └── index.html                     # a small browser chat UI
├── server/
│   ├── base_executor.py  # A2A adapter implementation
│   ├── card.py            # ExtendedAgentCard/ExtendedAgentSkill/ExtendedAgentProvider
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
