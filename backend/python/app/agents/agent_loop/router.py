"""Phase 7c/10: `chatMode` -> agent-loop `LoopStrategy` selection, including the
LLM auto-router for `chatMode == "auto"`.

The migration plan's sketch frames this as a `PRE_AGENT` hook (`hooks.on
(HookEvent.PRE_AGENT).use(auto_route)`), mirroring the legacy LangGraph
router being "just another node" the graph runs before the main loop. The
`agent-loop` library (`app/agent_loop_lib`) already reserves `HookEvent.PRE_AGENT`
in `HookRegistry`'s pipeline table, but `Agent.run()`/`Agent.step()` never
actually dispatch it yet (no `AgentLifecycleContext` type exists either) —
so there is nothing to attach a hook to today. `PipesHubAgentFactory`
(`factory.py`) therefore calls `select_loop()` directly, synchronously,
before constructing the `Agent`, which is observably identical (the router
still runs exactly once, before the first turn) and forward-compatible: the
day `PRE_AGENT` dispatch lands upstream, this module's logic can move behind
a real hook without changing its public signature.

The classification prompt/heuristic itself is NOT duplicated here — both
this router and the legacy `app/api/routes/agent.py::_auto_select_graph`
call the same `classify_route()` (`app.modules.agents.qna.router`), so the
two execution paths can never silently disagree on what "quick"/"react"/
"deep" means.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent.loops import LoopStrategy, PlanExecuteLoop, ReActLoop
from app.agent_loop_lib.models.transport import TransportModel
from app.agent_loop_lib.modules.pipeline.planner.plan_ahead import PlanAheadPlanner
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.loops.orchestrator import OrchestratorLoop
from app.modules.agents.qna.router import RouteDecision, classify_route

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

_DIRECT_MODES = {"verification", "react"}


def _context_to_query_info(context: "AgentContext", query: str) -> dict[str, Any]:
    """Adapts `AgentContext`'s already-loaded fields into the plain-dict
    shape `classify_route()` expects (same shape `chat_stream` passes to
    `_select_agent_graph_for_query` today) — no new data fetching."""
    return {
        "query": query,
        "knowledge": context.agent_knowledge or [],
        "connector_configs": context.connector_configs or {},
        "filters": context.filters or {},
        "toolsets": context.agent_toolsets or [],
        "previous_conversations": context.previous_conversations,
        # Attachments for the CURRENT turn aren't tracked on AgentContext
        # (they're resolved once into the Goal by the route handler, not
        # re-fetched here) -- routing on prior-turn attachments plus the
        # query text is the same signal `_auto_select_graph` falls back to
        # whenever `blob_store`/`attachments` are unavailable.
        "attachments": [],
    }


def _loop_for_route(route: str, llm: "BaseChatModel") -> LoopStrategy:
    if route == "quick":
        planner = PlanAheadPlanner(TransportModel(LangChainTransport(llm)))
        return PlanExecuteLoop(planner)
    if route == "deep":
        return OrchestratorLoop()
    return ReActLoop()


async def select_loop(
    *,
    chat_mode: str,
    query: str,
    llm: "BaseChatModel",
    context: "AgentContext",
) -> LoopStrategy:
    """Single entry point `PipesHubAgentFactory.create()` calls to resolve
    `chatMode` to a concrete `LoopStrategy` — see Phase 7b's mapping table:

    - `verification`/`react` -> `ReActLoop()` directly, no LLM call.
    - `deep` -> `OrchestratorLoop()` (Phase 10) — `PipesHubAgentFactory.create()`
      is responsible for restricting `spec.tool_names` to the four
      coordination tools and wiring `AgentRuntime.spec_factory` whenever this
      loop is returned; `select_loop()` itself stays tool/runtime-agnostic.
    - `quick` -> `PlanExecuteLoop` backed by `PlanAheadPlanner`.
    - `auto` (default) -> classify via the shared LLM router, then dispatch
      to one of the above.
    """
    normalized = (chat_mode or "auto").lower().strip()

    if normalized in _DIRECT_MODES:
        return ReActLoop()
    if normalized in ("quick", "deep"):
        return _loop_for_route(normalized, llm)

    # "auto" (or any unrecognized mode -- same fallback `_select_agent_graph_
    # for_query` uses for its own unmatched-mode branch).
    decision: RouteDecision = await classify_route(
        _context_to_query_info(context, query),
        logging.getLogger("app.agents.agent_loop.router.classify"),
        llm,
        config_service=context.config_service,
        graph_provider=context.graph_provider,
        is_multimodal_llm=context.is_multimodal_llm,
        org_id=context.org_id,
    )
    return _loop_for_route(decision.route, llm)


__all__ = ["select_loop"]
