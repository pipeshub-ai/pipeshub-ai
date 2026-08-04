"""Phase 5 PipesHub-specific hook middleware for agent-loop's `HookRegistry`.

Each module exposes either a small stateful class (when the hook needs
per-request state carried between calls, e.g. `ToolErrorTracker`) or a
factory function that closes over the per-request `AgentContext` and returns
a plain `async def middleware(ctx, next_fn)` callable — the shape
`agent_loop.hooks.middleware.pipeline.Pipeline.use()` expects.

Wired up fresh per request in Phase 7 (`app/agents/agent_loop/factory.py`)
onto that request's own `HookRegistry`/`AgentRuntime` — never onto a
process-global kernel, so none of this state leaks across concurrent
requests.
"""

from app.agents.agent_loop.hooks.artifact_context import artifact_context_reminder
from app.agents.agent_loop.hooks.ask_user_question import ask_user_question_sse
from app.agents.agent_loop.hooks.attachment_resolver import (
    attachment_rehydration,
    resolve_attachments_for_goal,
    resolve_history_attachments,
    shape_image_injection,
)
from app.agents.agent_loop.hooks.citations import (
    CitationCollector,
    citation_tracking,
    ensure_fetch_full_record_available,
)
from app.agents.agent_loop.hooks.completion_gate import completion_gate
from app.agents.agent_loop.hooks.memory import (
    conversation_enrichment,
    seed_visible_tools_from_history,
)
from app.agents.agent_loop.hooks.result_accumulation import (
    result_accumulation,
    stash_tool_call_metadata,
)
from app.agents.agent_loop.hooks.retry_with_status import retry_with_status
from app.agents.agent_loop.hooks.tool_blocking import ToolErrorTracker

__all__ = [
    "CitationCollector",
    "ToolErrorTracker",
    "artifact_context_reminder",
    "ask_user_question_sse",
    "attachment_rehydration",
    "citation_tracking",
    "ensure_fetch_full_record_available",
    "completion_gate",
    "conversation_enrichment",
    "seed_visible_tools_from_history",
    "resolve_attachments_for_goal",
    "resolve_history_attachments",
    "shape_image_injection",
    "result_accumulation",
    "retry_with_status",
    "stash_tool_call_metadata",
]
