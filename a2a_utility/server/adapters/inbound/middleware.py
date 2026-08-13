"""Inbound middleware (mock) — assembles a UserContext per request.

adjust.md §2: "Middleware 現階段僅做 Stub/Mock 組裝". This is the single place a
real deployment would parse auth headers / tokens / tenant and build identity;
today it stubs those fields and wires the request-scoped live-stream hook.
"""

from ...application.dtos import ThoughtEmitter, UserContext


def build_user_context(*, emit_thought: ThoughtEmitter | None = None) -> UserContext:
    # TODO(real middleware): derive user_id/roles/tenant_id/token from the
    # incoming request's auth headers instead of these mock values.
    return UserContext(
        user_id="mock-user",
        roles=["user"],
        tenant_id="default",
        token=None,
        emit_thought=emit_thought,
    )
