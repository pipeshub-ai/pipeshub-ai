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

The classification prompt/heuristic itself is NOT duplicated here — the
tier decision is made by `intent.parse_intent_and_route()` below, which
builds its prompt from `build_tier_rubric()`/`build_capability_context()`/
`build_sql_verify_override()` (`app.modules.agents.qna.router`) — the
same shared building blocks the now-deleted legacy `classify_route()`/
`app/api/routes/agent.py::_auto_select_graph` used to call directly, so
there is exactly one place "quick"/"react"/"deep" is defined.

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

`chatMode` -> loop KIND resolution goes through `modes.MODE_CATALOG`
(`resolve_mode()`) rather than special-casing mode names here — this
module's only remaining mode-specific logic is `_build_loop()`, keyed off
`ModeDefinition.loop_kind`, never off a mode's name. See that module's
docstring for the current name -> (loop_kind, compose_domain_agents)
mapping.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent.loops import LoopStrategy, ReActLoop
from app.agent_loop_lib.core.types import Goal
from app.agents.agent_loop.intent import IntentRouteDecision, parse_intent_and_route
from app.agents.agent_loop.loops.orchestrator import OrchestratorLoop
from app.agents.agent_loop.loops.plan_execute import PlanCritiqueExecuteLoop
from app.agents.agent_loop.modes import ModeDefinition, resolve_mode

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agent_loop_lib.transport.registry import TransportRegistry
    from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Guaranteed present in `MODE_CATALOG` — the fallback this module uses when
# the tier classifier's own verdict is somehow missing/unrecognized
# (defensive: never raise out of routing just because the classifier
# returned something odd).
_FALLBACK_MODE = resolve_mode("react")
assert _FALLBACK_MODE is not None


def _context_to_query_info(context: "AgentContext", query: str) -> dict[str, Any]:
    """Adapts `AgentContext`'s already-loaded fields into the plain-dict
    shape `parse_intent_and_route()` expects (same shape `chat_stream`
    used to pass to the now-deleted `_select_agent_graph_for_query`) — no
    new data fetching."""
    return {
        "query": query,
        "knowledge": context.agent_knowledge or [],
        "connector_configs": context.connector_configs or {},
        "filters": context.filters or {},
        "toolsets": context.agent_toolsets or [],
        "previous_conversations": context.previous_conversations,
        "attachments": context.tool_state.get("attachments") or [],
    }


def _build_loop(
    loop_kind: str,
    llm: "BaseChatModel",
    tool_names: list[str] | None = None,
    *,
    sandbox_has_network: bool = False,
    opik_active: bool = False,
    opik_project_name: str | None = None,
) -> LoopStrategy:
    """Constructs the `LoopStrategy` for a `ModeDefinition.loop_kind` —
    keyed off the loop KIND (`"react"` / `"plan_execute"` / `"orchestrator"`),
    never off a mode's name, so adding a new mode that reuses an existing
    loop kind (see `modes.py`) never needs a change here.

    `llm`/`tool_names`/`sandbox_has_network`/`opik_active`/
    `opik_project_name` are unused by `"plan_execute"`/`"orchestrator"`
    today (both loop shapes plan via the `create_plan` TOOL, at run time,
    rather than a construction-time `Planner` needing to know the tool
    list or an LLM handle upfront) — kept on this signature for `"react"`'s
    call site symmetry and so a future loop kind that DOES need
    construction-time steering has somewhere to plug in without a
    signature change."""
    if loop_kind == "plan_execute":
        return PlanCritiqueExecuteLoop()
    if loop_kind == "orchestrator":
        return OrchestratorLoop()
    return ReActLoop()


def _base_constraints(attachments: list[dict[str, Any]] | None = None) -> list[str]:
    """Seed constraints common to every Goal: the names of attached files. The
    query itself is `Goal.user_message`, which the prompt builder anchors
    verbatim; repeating it here put it under a "this is data" header."""
    if not attachments:
        return []
    names = ", ".join(a.get("recordName", "unknown") for a in attachments)
    return [f"User attached file(s): {names}"]


import re as _re

_COMPARISON_RE = _re.compile(
    r"\b(compare|difference|differ|vs\.?|versus|between .+ and)\b", _re.IGNORECASE
)
_LIST_ALL_RE = _re.compile(
    r"\b(list all|show all|find all|every|all .{1,30} (?:in|from|across))\b", _re.IGNORECASE
)
_MULTI_PART_RE = _re.compile(
    r"\b(and also|additionally|as well as|plus|along with)\b", _re.IGNORECASE
)
_SUMMARIZE_RE = _re.compile(
    r"\b(summarize|summary|overview|recap|outline)\b", _re.IGNORECASE
)


def _infer_quick_mode_criteria(query: str) -> list[str]:
    """Zero-cost (regex, no LLM) heuristic that infers basic success
    criteria from query structure for quick mode, so the goal brief has
    SOMETHING meaningful even without the full intent call."""
    criteria: list[str] = []
    if _COMPARISON_RE.search(query):
        criteria.append("Address all items being compared with concrete differences.")
    if _LIST_ALL_RE.search(query):
        criteria.append("Provide a comprehensive list, not just the first few results.")
    if _MULTI_PART_RE.search(query):
        criteria.append("Answer every part of the multi-part question.")
    if _SUMMARIZE_RE.search(query):
        criteria.append("Cover all key points — do not truncate prematurely.")
    return criteria


def _build_goal(
    raw_query: str,
    decision: IntentRouteDecision,
    attachments: list[dict[str, Any]] | None = None,
) -> Goal:
    """Builds the structured `Goal` the agent actually runs with.

    The raw query is `user_message`: the current user message is always the
    person's own words. `rewritten_query` is the `interpretation`, rendered
    as context in the system prompt, and the `description` the planners and
    tool search work from (falling back to the raw query if the model
    returned nothing usable — `parse_intent_and_route()` also guards this,
    this is belt-and-suspenders).

    `decision.gaps` lands on `Goal.gaps` unchanged — the same field
    `agent_loop_lib`'s own `GoalBuilder` populates, so anything downstream
    that already knows to look at `goal.gaps` (prompt builders, future
    planners) sees identical shape regardless of which pipeline produced
    the `Goal`. When `decision.clarifying_questions` is non-empty, the
    caller (`select_loop_and_goal`) short-circuits before this `Goal` is
    ever handed to an `Agent` — see that function's docstring.
    """
    interpretation = decision.rewritten_query.strip()
    return Goal(
        description=interpretation or raw_query,
        user_message=raw_query,
        interpretation=interpretation if interpretation and interpretation != raw_query.strip() else None,
        requirements=list(decision.requirements),
        success_criteria=list(decision.success_criteria),
        constraints=_base_constraints(attachments),
        gaps=list(decision.gaps),
    )


def _goal_without_intent(raw_query: str, attachments: list[dict[str, Any]] | None) -> Goal:
    """The goal when no intent call runs: the request as typed, plus the
    regex criteria `_infer_quick_mode_criteria` can read off its shape."""
    return Goal(
        description=raw_query,
        user_message=raw_query,
        success_criteria=_infer_quick_mode_criteria(raw_query),
        constraints=_base_constraints(attachments),
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
    transport_registry: "TransportRegistry | None" = None,
    resume: bool = False,
) -> tuple[LoopStrategy, Goal, list[AskUserQuestionItemInput], ModeDefinition]:
    """Single entry point `PipesHubAgentFactory.create()` calls to resolve
    `chatMode` to a concrete `LoopStrategy` AND the `Goal` the agent runs
    with, via `modes.MODE_CATALOG` (see that module's docstring for the
    current name -> (loop_kind, compose_domain_agents) mapping), plus the
    intent step that now runs for every mode:

    - Any mode `resolve_mode()` recognizes (canonical name or legacy
      alias, e.g. `verification` -> `planExecute`) resolves WITHOUT a
      routing LLM call — intent parsing (`include_routing=False`) still
      runs so every explicit mode gets a reorganized Goal too, UNLESS the
      resolved `ModeDefinition.skip_intent` is set (`quick` today — see
      `modes.py`), in which case the intent call is skipped entirely and
      `Goal.description` is the raw query verbatim.
    - `auto` (default, or any mode `resolve_mode()` doesn't recognize) ->
      the SAME intent call also classifies the tier (`include_routing=
      True`, verdict is always `quick`/`react`/`deep`), then resolves
      THAT word through the catalog — no second LLM round-trip versus the
      old classify_route()-only auto path. This ALWAYS runs the intent
      call regardless of `skip_intent` on the resolved tier: the call
      already happened (it's how the tier was picked), so there is no
      second call left to skip — `skip_intent` only ever short-circuits
      the call BEFORE it happens, which requires knowing the mode upfront.
    - `tool_names`/`sandbox_has_network` are accepted for call-site
      symmetry with `_build_loop()` but currently unused by every loop
      kind (`plan_execute` and `orchestrator` both plan via the
      `create_plan` TOOL at run time now, not a construction-time
      `Planner`) — harmless to pass unconditionally.
    - `transport_registry`, when given, is the CALLER's own registry
      (`PipesHubAgentFactory.create()` already built and registered
      "langchain" on one) — passed straight through to
      `parse_intent_and_route()` so the intent call resolves the SAME
      cached `LangChainTransport` instead of constructing (and re-wrapping
      for Opik) a second one just for this one call. `None` (e.g. a
      caller/test with no registry of its own) falls back to
      `parse_intent_and_route()` building its own, unchanged.

    The 4th return value is the resolved `ModeDefinition` itself —
    `PipesHubAgentFactory.create()` reads `.compose_domain_agents`/
    `.loop_kind` off it instead of re-deriving composition/tool-grant
    behavior from `isinstance(loop, OrchestratorLoop)`.

    The 3rd return value is `decision.clarifying_questions` verbatim: a
    non-empty list means the request was too ambiguous to safely reorganize
    into the `Goal` above. The `LoopStrategy`/`Goal` are still returned
    (never `None`) so this function's signature never has to special-case
    the ambiguous path — `PipesHubAgentFactory.create()`/`stream_bridge.py`
    are the ones that check this list and skip `Agent.run()` entirely when
    it's non-empty (see `clarification.py`), exactly like `02_orchestrator.
    py`'s `intent_agent` gating what `executor_agent` receives, except the
    gate here can end the whole turn instead of only choosing a sub-agent.

    `resume`: the request answers an ask-user or approval card, continuing
    the turn that stopped on it, so it asks no clarifying question. With the
    mode known up front the intent call is skipped too: the run continues
    the stopped turn, whose own question is `query`. Under `auto` the call
    still runs, because its tier picks the loop and the tools.
    """
    mode = resolve_mode(chat_mode)

    attachments = context.tool_state.get("attachments") or []

    if mode is not None and (mode.skip_intent or resume):
        # `quick` today: the mode is already known from the wire value
        # (not the tier classifier), so there is no round-trip left to
        # amortize an intent call against — skip it outright rather than
        # spending an extra LLM call just to rewrite a query nothing else
        # here needs rewritten. `Goal.description` is the raw query
        # verbatim; `_build_goal()` would have done the same fallback
        # anyway (`decision.rewritten_query.strip() or raw_query`) had the
        # model call failed, so this is that same fallback shape, just
        # reached without paying for the call.
        if resume and not mode.skip_intent:
            logger.info("intent_skipped reason=resume mode=%s", mode.name)
        goal = _goal_without_intent(query, attachments)
        clarifying_questions: list["AskUserQuestionItemInput"] = []
        # No intent call — use the conservative regex fallback so pinpoint
        # queries (the majority) still cost zero tokens.
        from app.modules.agents.record_escalation.policy import needs_whole_document
        context.needs_whole_document = needs_whole_document(None, query)
        context.tool_state["needs_whole_document"] = context.needs_whole_document
    else:
        include_routing = mode is None

        decision = await parse_intent_and_route(
            _context_to_query_info(context, query),
            logging.getLogger("app.agents.agent_loop.router.intent"),
            llm,
            include_routing=include_routing,
            config_service=context.config_service,
            graph_provider=context.graph_provider,
            is_multimodal_llm=context.is_multimodal_llm,
            org_id=context.org_id,
            user_id=context.user_id,
            is_service_account=context.is_service_account,
            transport_registry=transport_registry,
            opik_active=opik_active,
            opik_project_name=opik_project_name,
        )
        goal = _build_goal(query, decision, attachments=attachments)
        # A turn that answers a card always runs: it continues the turn that asked.
        clarifying_questions = [] if resume or context.tool_approval is not None else list(decision.clarifying_questions)

        # Propagate the whole-document bit from the intent result.
        # When the model emitted the WHOLE_DOCUMENT marker, use it directly;
        # when it was absent (e.g. fallback decision), apply the regex so
        # the gate still fires for obvious cases even if the model omitted
        # the marker.
        from app.modules.agents.record_escalation.policy import needs_whole_document
        if decision.whole_document:
            context.needs_whole_document = True
        else:
            context.needs_whole_document = needs_whole_document(None, query, goal.description)
        context.tool_state["needs_whole_document"] = context.needs_whole_document

        if mode is None:
            # "auto" (or any unrecognized mode) -- `decision.route` was
            # populated above since `include_routing=True` on this branch, and
            # is always one of "quick"/"react"/"deep", every one of which is a
            # canonical catalog name -- but resolve defensively in case a
            # caller ever reaches here without it (mirrors `classify_route()`'s
            # own failure fallback).
            mode = resolve_mode(decision.route or "react") or _FALLBACK_MODE

    loop = _build_loop(
        mode.loop_kind, llm, tool_names,
        sandbox_has_network=sandbox_has_network,
        opik_active=opik_active, opik_project_name=opik_project_name,
    )
    return loop, goal, clarifying_questions, mode


__all__ = ["select_loop_and_goal"]
