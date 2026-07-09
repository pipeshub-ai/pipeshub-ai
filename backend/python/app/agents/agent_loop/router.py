"""Phase 7c/10 (+ intent): `chatMode` -> agent-loop `LoopStrategy` selection,
including the LLM auto-router for `chatMode == "auto"`, merged with intent
understanding for EVERY chat mode.

The migration plan's sketch frames this as a `PRE_AGENT` hook (`hooks.on
(HookEvent.PRE_AGENT).use(auto_route)`), mirroring the legacy LangGraph
router being "just another node" the graph runs before the main loop. The
`agent-loop` library (`app/agent_loop_lib`) already reserves `HookEvent.PRE_AGENT`
in `HookRegistry`'s pipeline table, but `Agent.run()`/`Agent.step()` never
actually dispatch it yet (no `AgentLifecycleContext` type exists either) —
so there is nothing to attach a hook to today. `PipesHubAgentFactory`
(`factory.py`) therefore calls `select_loop_and_goal()` directly,
synchronously, before constructing the `Agent`, which is observably
identical (the router still runs exactly once, before the first turn) and
forward-compatible: the day `PRE_AGENT` dispatch lands upstream, this
module's logic can move behind a real hook without changing its public
signature.

The classification prompt/heuristic itself is NOT duplicated here — both
this router and the legacy `app/api/routes/agent.py::_auto_select_graph`
call the same `classify_route()`/`build_tier_rubric()`
(`app.modules.agents.qna.router`), so the two execution paths can never
silently disagree on what "quick"/"react"/"deep" means.

Intent understanding is now merged into the SAME call: every request (not
just `chatMode == "auto"`) goes through `intent.parse_intent_and_route()`
once, which reorganizes the raw query into a Goal (`rewritten_query` +
`requirements` + `success_criteria`) and — only for auto/unrecognized modes
— also returns the tier classification, so auto mode pays no extra LLM
round-trip versus before this module existed. See `02_orchestrator.py` in
the `agent-loop` library's examples for the agent-as-tool version of this
same idea (a standalone `intent_agent` the top-level orchestrator calls);
this adapter merges that step into one programmatic call instead of a
second agent hop, matching how `select_loop_and_goal()` already runs
synchronously before `Agent` construction rather than as a tool call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent.loops import LoopStrategy, PlanExecuteLoop, ReActLoop
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.models.transport import TransportModel
from app.agent_loop_lib.modules.pipeline.planner.plan_ahead import PlanAheadPlanner
from app.agent_loop_lib.transport.opik_tracing import wrap_if_enabled
from app.agents.agent_loop.intent import IntentRouteDecision, parse_intent_and_route
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.loops.orchestrator import OrchestratorLoop

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

_DIRECT_MODES = {"verification", "react"}
_EXPLICIT_ROUTE_MODES = {"quick", "deep"}


def _context_to_query_info(context: "AgentContext", query: str) -> dict[str, Any]:
    """Adapts `AgentContext`'s already-loaded fields into the plain-dict
    shape `classify_route()`/`parse_intent_and_route()` expect (same shape
    `chat_stream` passes to `_select_agent_graph_for_query` today) — no new
    data fetching."""
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


def _loop_for_route(
    route: str,
    llm: "BaseChatModel",
    tool_names: list[str] | None = None,
    *,
    sandbox_has_network: bool = False,
    opik_active: bool = False,
    opik_project_name: str | None = None,
) -> LoopStrategy:
    if route == "quick":
        # Same `wrap_if_enabled` gate `PipesHubAgentFactory.create()` applies to
        # the main transport — this planner's upfront-plan call runs inside
        # `PlanExecuteLoop`, i.e. inside `Agent.run()`'s trace scope, so without
        # this it would be the one LLM call in the request invisible to Opik.
        planner_transport = wrap_if_enabled(
            LangChainTransport(llm), enabled=opik_active, project_name=opik_project_name,
        )
        planner = PlanAheadPlanner(
            TransportModel(planner_transport),
            tool_names=tool_names,
            sandbox_has_network=sandbox_has_network,
        )
        return PlanExecuteLoop(planner)
    if route == "deep":
        return OrchestratorLoop()
    return ReActLoop()


def _build_goal(raw_query: str, decision: IntentRouteDecision) -> Goal:
    """Builds the structured `Goal` the agent actually runs with.

    `rewritten_query` becomes `description` (falling back to the raw query
    if the model returned nothing usable — `parse_intent_and_route()` also
    guards this, this is belt-and-suspenders). The ORIGINAL raw query is
    always preserved verbatim as a constraint so nothing downstream loses
    it: `PipesHubPromptBuilder` surfaces `goal.constraints`, and
    `conversation_enrichment`'s (PRE_TURN hook) follow-up-reuse note keys
    off `goal.description`/history, not this constraint — appending to it
    is additive, never a replacement of that hook's own note.

    `decision.gaps` lands on `Goal.gaps` unchanged — the same field
    `agent_loop_lib`'s own `GoalBuilder` populates, so anything downstream
    that already knows to look at `goal.gaps` (prompt builders, future
    planners) sees identical shape regardless of which pipeline produced
    the `Goal`. When `decision.clarifying_questions` is non-empty, the
    caller (`select_loop_and_goal`) short-circuits before this `Goal` is
    ever handed to an `Agent` — see that function's docstring.
    """
    description = decision.rewritten_query.strip() or raw_query
    return Goal(
        description=description,
        requirements=list(decision.requirements),
        success_criteria=list(decision.success_criteria),
        constraints=[f'Original user query: "{raw_query}"'],
        gaps=list(decision.gaps),
    )


async def select_loop_and_goal(
    *,
    chat_mode: str,
    query: str,
    llm: "BaseChatModel",
    context: "AgentContext",
    tool_names: list[str] | None = None,
    sandbox_has_network: bool = False,
    opik_active: bool = False,
    opik_project_name: str | None = None,
) -> tuple[LoopStrategy, Goal, list[AskUserQuestionItemInput]]:
    """Single entry point `PipesHubAgentFactory.create()` calls to resolve
    `chatMode` to a concrete `LoopStrategy` AND the `Goal` the agent runs
    with — see Phase 7b's mapping table (unchanged) plus the intent step
    that now runs for every mode:

    - `verification`/`react` -> `ReActLoop()`, no routing LLM call — but
      intent parsing (`include_routing=False`) still runs so these modes
      get a reorganized Goal too.
    - `deep` -> `OrchestratorLoop()` (Phase 10) — `PipesHubAgentFactory.
      create()` is responsible for restricting `spec.tool_names` to the
      four coordination tools and wiring `AgentRuntime.spec_factory`
      whenever this loop is returned; `select_loop_and_goal()` itself stays
      tool/runtime-agnostic.
    - `quick` -> `PlanExecuteLoop` backed by `PlanAheadPlanner`, seeded with
      `tool_names` (this request's already-loaded tool registry names) so
      the upfront plan can reference real tools like `run_code` by name
      instead of producing abstract, tool-agnostic phases. `sandbox_has_network`
      is forwarded unchanged so the planner's own steering about `run_code`
      matches what the tool's description/`CodeRequest.allow_network`
      actually grant it this request (see `sandbox_bridge.sandbox_network_enabled()`).
    - `auto` (default, or any unrecognized mode) -> the SAME intent call
      also classifies the tier (`include_routing=True`), then dispatches to
      one of the above — no second LLM round-trip versus the old
      classify_route()-only auto path.

    The third return value is `decision.clarifying_questions` verbatim: a
    non-empty list means the request was too ambiguous to safely reorganize
    into the `Goal` above. The `LoopStrategy`/`Goal` are still returned
    (never `None`) so this function's signature never has to special-case
    the ambiguous path — `PipesHubAgentFactory.create()`/`stream_bridge.py`
    are the ones that check this list and skip `Agent.run()` entirely when
    it's non-empty (see `clarification.py`), exactly like `02_orchestrator.
    py`'s `intent_agent` gating what `executor_agent` receives, except the
    gate here can end the whole turn instead of only choosing a sub-agent.
    """
    normalized = (chat_mode or "auto").lower().strip()
    include_routing = normalized not in _DIRECT_MODES and normalized not in _EXPLICIT_ROUTE_MODES

    decision = await parse_intent_and_route(
        _context_to_query_info(context, query),
        logging.getLogger("app.agents.agent_loop.router.intent"),
        llm,
        include_routing=include_routing,
        config_service=context.config_service,
        graph_provider=context.graph_provider,
        is_multimodal_llm=context.is_multimodal_llm,
        org_id=context.org_id,
    )
    goal = _build_goal(query, decision)
    clarifying_questions = list(decision.clarifying_questions)

    if normalized in _DIRECT_MODES:
        return ReActLoop(), goal, clarifying_questions
    if normalized in _EXPLICIT_ROUTE_MODES:
        loop = _loop_for_route(
            normalized, llm, tool_names,
            sandbox_has_network=sandbox_has_network,
            opik_active=opik_active, opik_project_name=opik_project_name,
        )
        return loop, goal, clarifying_questions

    # "auto" (or any unrecognized mode -- same fallback `_select_agent_graph_
    # for_query` uses for its own unmatched-mode branch); `decision.route`
    # was populated above since `include_routing=True` on this branch, but
    # fall back to "react" defensively (mirrors `classify_route()`'s own
    # failure fallback) in case a caller ever reaches here without it.
    loop = _loop_for_route(
        decision.route or "react", llm, tool_names,
        sandbox_has_network=sandbox_has_network,
        opik_active=opik_active, opik_project_name=opik_project_name,
    )
    return loop, goal, clarifying_questions


__all__ = ["select_loop_and_goal"]
