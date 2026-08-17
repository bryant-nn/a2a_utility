"""AgentExecutor — the inbound adapter bridging native a2a execute()/cancel()
calls to an injected AgentHandlerPort (+ optional OnCancelPort).

This *is* a real subclass of the native `a2a.server.agent_execution.
AgentExecutor` ABC (not a reimplementation) — it genuinely satisfies the SDK's
own contract, so `DefaultRequestHandler`/`create_jsonrpc_routes` work with it
unmodified. Domain agents never see this class at all: `serve_as_a2a(handler=
..., ...)`/`create_app(handler=..., ...)` build one internally from whatever
AgentHandlerPort (a plain callable) the domain agent supplied, exactly the
way `adapters/inbound/discovery_agent_executor.py` is constructor-injected
with a DiscoveryUseCasePort instead of being subclassed per mode.

execute()/cancel() have zero task-state decision logic of their own — the
handler (or on_cancel) drives the task to wherever it ends up by calling
methods on the `ExtendedTaskUpdater` it builds for itself from the
`ExtendedEventQueue` it's handed, exactly the way a native a2a-sdk
implementation drives things. The only things this class adds on top of pure
pass-through:

  1. `execute()`: uncaught exceptions become a FAILED status *carrying the
     error text* via a throwaway `ExtendedTaskUpdater` built fresh in the
     except block.

     Traced against a2a-sdk 1.1.2, where `DefaultRequestHandler` is
     `DefaultRequestHandlerV2`: the framework does now catch this itself.
     `ActiveTask._run_producer()` wraps `agent_executor.execute()` in a
     try/except that persists `TASK_STATE_FAILED` via the task manager and
     forwards the exception to the consumer, and `EventConsumer.run()` has
     its own except doing the same. So unlike under the older
     `LegacyRequestHandler` — where `_run_event_stream()` had no try/except
     and an exception only surfaced on the server's own consume loop — the
     task no longer hangs without this.

     What native still does not do is say *what went wrong*: it emits a bare
     `TaskStatusUpdateEvent(FAILED)` with no message. This safety net exists
     for that one reason — an A2A caller gets "Agent error: ..." instead of
     an unexplained failure. It is a diagnostics improvement layered on top
     of native behavior, not a correctness gap being patched.

     (Known residual trade-off: if the handler's own updater already sent a
     terminal state and *then* raised, this throwaway instance doesn't know
     — native's double-terminal guard is per-instance — so a redundant
     status goes out. The `contextlib.suppress(RuntimeError)` catches the
     case where the guard does fire.)

  2. `cancel()`: if `on_cancel` isn't given, or is given but raises, falls
     back to a plain `.cancel()` on a throwaway `ExtendedTaskUpdater` — a
     reasonable default so an external cancel request doesn't go completely
     unacknowledged.

Deliberately NOT included (present in an earlier design pass, removed after
re-examining against native): auto-sending an initial WORKING status before
handing off to the handler, and checking whether the handler ever reached a
terminal/interrupted state before returning. Native itself doesn't do either
— a native implementation is expected to call `TaskUpdater.start_work()`
itself and is trusted to terminate the task correctly; this class holds
domain agents to exactly the same expectation, no more, no less.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Optional

from a2a.server.agent_execution import AgentExecutor as _NativeAgentExecutor
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.server.id_generator import IDGenerator

from ...application.dtos import ExtendedRequestContext
from ...application.ports.inbound.agent_handler_port import AgentHandlerPort
from ...application.ports.inbound.gate_keeper_port import GateKeeperPort
from ...application.ports.inbound.on_cancel_port import OnCancelPort
from ...domain.models.auth_decision import Allow, AuthRequired, Reject
from ...domain.models.user_context import PermissionDenied
from ..outbound.event_queue_adapter import ExtendedEventQueue
from ..outbound.task_updater_adapter import ExtendedTaskUpdater

logger = logging.getLogger(__name__)


class AgentExecutor(_NativeAgentExecutor):
    def __init__(
        self,
        handler: AgentHandlerPort,
        on_cancel: Optional[OnCancelPort] = None,
        *,
        gate_keeper: Optional[GateKeeperPort] = None,
        required_permission: Optional[str] = None,
        message_id_generator: Optional[IDGenerator] = None,
    ) -> None:
        self._handler = handler
        self._on_cancel = on_cancel
        self._gate_keeper = gate_keeper
        self._required_permission = required_permission
        self._message_id_generator = message_id_generator

    def _build_event_queue(self, context: RequestContext, event_queue: EventQueue) -> ExtendedEventQueue:
        return ExtendedEventQueue(event_queue, expected_task_id=context.task_id)

    def _task_updater(
        self, ctx: ExtendedRequestContext, eq: ExtendedEventQueue
    ) -> ExtendedTaskUpdater:
        return ExtendedTaskUpdater(ctx, eq, message_id_generator=self._message_id_generator)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        ctx = ExtendedRequestContext(context)
        eq = self._build_event_queue(context, event_queue)
        try:
            if not await self._passes_gate(ctx, eq):
                return
            await self._handler(ctx, eq)
        except PermissionDenied as e:
            # Distinct from a generic failure on purpose: "you may not" ends
            # the task REJECTED, "it broke" ends it FAILED. A caller reads
            # those differently — one is worth retrying with better
            # credentials, the other isn't worth retrying at all.
            logger.info("handler refused a tool-level permission: %s", e)
            with contextlib.suppress(RuntimeError):
                await self._task_updater(ctx, eq).reject(str(e))
        except Exception as e:
            with contextlib.suppress(RuntimeError):  # handler's own TaskUpdater may already be terminal
                await self._task_updater(ctx, eq).failed(f"Agent error: {e}")

    async def _passes_gate(
        self, ctx: ExtendedRequestContext, eq: ExtendedEventQueue
    ) -> bool:
        """Runs the gate, ending the task itself if the answer is no.

        Returns True when the handler should run. Deliberately inside
        execute()'s scope rather than in a middleware: only here can a refusal
        become a *task state* the A2A caller can interpret, instead of a bare
        HTTP status.
        """
        if self._gate_keeper is None:
            return True

        headers = ctx.headers
        decision = await self._gate_keeper.authorize(headers, self._required_permission)

        if isinstance(decision, Allow):
            ctx.attach_user(decision.user)
            return True

        task_updater = self._task_updater(ctx, eq)
        if isinstance(decision, AuthRequired):
            logger.info("gate: authentication required — %s", decision.reason)
            await task_updater.requires_auth(decision.reason)
        elif isinstance(decision, Reject):
            logger.info("gate: rejected — %s", decision.reason)
            await task_updater.reject(decision.reason)
        else:  # pragma: no cover - a GateKeeperPort returning something else
            raise TypeError(
                f"gate_keeper.authorize returned {type(decision).__name__}; "
                "expected Allow, AuthRequired, or Reject."
            )
        return False

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Reacts to an externally-requested cancellation (a client's cancel
        RPC). Structurally separate from execute()'s own path (an agent
        deciding on its own to give up, via `task_updater.cancel()` inside
        the handler) — the framework calls this independently, in addition
        to cancelling the asyncio task running execute() (which lets a
        handler's own try/finally clean up; no new plumbing needed for that
        half)."""
        ctx = ExtendedRequestContext(context)
        eq = self._build_event_queue(context, event_queue)
        if self._on_cancel is not None:
            try:
                await self._on_cancel(ctx, eq)
                return
            except Exception:
                pass  # fall through to the default below — cancellation must still complete
        with contextlib.suppress(RuntimeError):
            tu = ExtendedTaskUpdater(ctx, eq, message_id_generator=self._message_id_generator)
            await tu.cancel()
