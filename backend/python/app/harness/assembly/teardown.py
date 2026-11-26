"""Releases what one attempt's tool environment opened (X-1, X-2).

MCP sessions open lazily on the first call to an instance and stay cached on
``context.tool_state``; a stdio session is a live child process of the worker.
The coding sandbox manager is built during assembly and owns containers or
remote sandboxes. Neither is released by garbage collection, so every exit of
an attempt (and a build that fails after the environment exists) comes here.
The attempt's credential handles are revoked here too (P2.4a): nothing can
resolve them once the attempt is over, and the conversation's queued tasks it
left are dropped (P5.11).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agents.agent_loop.context import AgentContext
    from harness_core.logs import HarnessLog

MCP_SESSIONS_KEY = "_mcp_client_managers"


async def _close_mcp_sessions(context: AgentContext) -> int:
    from app.agents.agent_loop.mcp_session import close_request_sessions

    return await close_request_sessions(context)


async def _destroy_sandboxes(context: AgentContext) -> int:
    manager = context.sandbox_manager
    if manager is None:
        return 0
    await manager.destroy_all()
    return 1


async def _revoke_credentials(context: AgentContext) -> int:
    return await context.close_credentials()


async def _drop_conversation_tasks(context: AgentContext) -> int:
    """Artifact markers an attempt queued and never journaled (it died mid-call): the
    registry is process-wide, so a later attempt here would present them a second time."""
    conversation_id = getattr(context, "conversation_id", None)
    if not conversation_id:
        return 0
    from app.utils.conversation_tasks import pop_tasks

    left = pop_tasks(conversation_id)
    for task in left:
        _ = task.cancel()
    return len(left)


_CLOSERS: tuple[tuple[str, Callable[[AgentContext], Awaitable[int]]], ...] = (
    ("mcp_sessions", _close_mcp_sessions),
    ("sandboxes", _destroy_sandboxes),
    ("credentials", _revoke_credentials),
    ("conversation_tasks", _drop_conversation_tasks),
)


async def close_environment(context: AgentContext, log: HarnessLog) -> None:
    """Runs every closer even when an earlier one raises; never raises itself,
    because it runs on exits that already carry their own outcome or error."""
    closed: dict[str, int] = {}
    failed = 0
    for name, closer in _CLOSERS:
        try:
            closed[name] = await closer(context)
        except Exception as exc:
            failed += 1
            log.error("run.close_failed", exc=exc, closer=name, errorType=type(exc).__name__)
    log.info(
        "run.closed",
        mcpSessions=closed.get("mcp_sessions", 0),
        sandboxManagers=closed.get("sandboxes", 0),
        credentialHandles=closed.get("credentials", 0),
        droppedTasks=closed.get("conversation_tasks", 0),
        failed=failed,
    )


__all__ = ["MCP_SESSIONS_KEY", "close_environment"]
