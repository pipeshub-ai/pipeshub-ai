"""`PipesHubAgentFactory`: Phase 7 — assembles an agent-loop `Agent` +
`AgentRuntime` from PipesHub's per-request `AgentContext`, wiring together
every adapter-layer piece built in Phases 2-6:

- `LangChainTransport` (Phase 2) registered under the `"langchain"` provider
- `PipesHubToolLoader` (Phase 3) for the per-request `ToolRegistry`
- `PipesHubPromptBuilder` + `ToolGuidanceProvider` (Phase 3/4) for the system prompt
- Phase 5's hook middleware (tool blocking, citation tracking, conversation
  memory, result accumulation, `ask_user_question` SSE)
- `SSEEventEmitter` (this phase) for real-time tool-orchestration events
- `router.select_loop_and_goal()` (this phase + intent) for `chatMode` ->
  `LoopStrategy`, AND (every mode) the intent-parsed `Goal` the agent runs
  with — see `intent.py` for the merged intent+routing call
- Phase 10: when `select_loop_and_goal()` resolves to `OrchestratorLoop`, the deep
  agent's top-level `spec.tool_names` is narrowed to the four coordination
  tools (`create_plan`/`critique_plan`/`spawn_agent`/`verify_result` —
  registered onto the request's `tool_registry` here) and
  `AgentRuntime.spec_factory` is wired so `spawn_agent` can resolve a
  domain "role" string into a scoped child `AgentSpec` — see
  `loops/orchestrator.py` for the full Decompose/Critique/Dispatch/Verify
  design rationale.
- Domain-agent composition (`domain_agents.py`): for every OTHER loop
  (ReAct, quick/`PlanExecuteLoop`), the top-level `spec.tool_names` is
  narrowed to a handful of `AgentTool` delegates (coding/web/internal-
  search/calculator/calendar) plus whatever tools no domain claimed —
  `plan_domain_agents()` runs BEFORE `select_loop_and_goal()` so the
  quick-mode planner is steered with the same composed names the
  executing agent ends up with, and `register_domain_agents()` runs after
  the `AgentRuntime` exists to actually build/register the child specs.

Deliberately minimal on the `AgentRuntime` side (`budget`/stores/etc. all
left `None`) — this migration keeps PipesHub's existing storage backends
(no SQLite, no new databases; see the plan's Design Constraints), so none
of agent-loop's optional persistence stores apply here.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import shape_budget_reduction
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import shape_sliding_window
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    shape_tool_result_clearing,
)
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.transport.opik_tracing import is_opik_configured, wrap_if_enabled
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.hooks import (
    CitationCollector,
    ToolErrorTracker,
    ask_user_question_sse,
    citation_tracking,
    conversation_enrichment,
    result_accumulation,
    retry_with_status,
    stash_tool_call_metadata,
)
from app.agents.agent_loop.domain_agents import plan_domain_agents, register_domain_agents
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.loops.orchestrator import (
    COORDINATION_TOOL_NAMES,
    OrchestratorLoop,
    domain_spec_factory,
    register_coordination_tools,
)
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.router import select_loop_and_goal
from app.agents.agent_loop.sandbox_bridge import (
    build_coding_sandbox_manager,
    register_coding_sandbox_hooks,
    register_coding_sandbox_tools,
    sandbox_network_enabled,
)
from app.agents.agent_loop.sse_emitter import SSEEventEmitter
from app.agents.agent_loop.tool_guidance import ToolGuidanceProvider
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.modules.agents.qna.tool_system import code_execution_enabled

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agent_loop_lib.core.types import Goal
    from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Matches nodes.py's ReAct/planner loop cap (`MAX_ITERATIONS` — see
# tool_system.py / react_agent_node's `recursion_limit`); kept as one named
# constant here rather than a magic number in `create()`.
_MAX_TURNS = 15


def _composed_agents_enabled() -> bool:
    """Kill-switch for domain-agent composition (see `domain_agents.py`) —
    same env-var rollout pattern as `PIPESHUB_USE_AGENT_LOOP`: not a
    customer-facing setting, exists so a deployment can fall back to the
    flat all-tools agent without a code change."""
    return os.getenv("PIPESHUB_USE_COMPOSED_AGENTS", "true").strip().lower() == "true"


class PipesHubAgentFactory:
    """Creates an agent-loop `Agent` (+ its `AgentRuntime`) from PipesHub's
    per-request context. One instance is stateless and reusable across
    requests; all per-request state lives on the `AgentContext` passed in."""

    async def create(
        self,
        context: "AgentContext",
        llm: "BaseChatModel",
        chat_mode: str,
        *,
        query: str,
        model_name: str = "",
        session_id: str | None = None,
    ) -> tuple[Agent, AgentRuntime, "Goal", list["AskUserQuestionItemInput"]]:
        """Builds a fully-wired `Agent` plus the intent-parsed `Goal` it
        should run with. Async because every request now resolves its
        `Goal` (and, for `chatMode == "auto"`, its `LoopStrategy`) via one
        structured LLM call (`router.select_loop_and_goal`) before the
        `Agent` can be constructed.

        The 4th return value is non-empty exactly when the intent call
        decided the request is too ambiguous to run at all — the `Agent`
        is still built and returned (simpler than threading a nullable
        `Agent` through this signature; it's a cheap, side-effect-free
        object to construct), but callers MUST check this list first and
        skip `agent.run()` entirely when it's non-empty, routing to
        `clarification.emit_pre_run_clarification()` instead. See
        `stream_bridge.py`.
        """
        # Same "configure via env, no-op otherwise" gate `ControlPlane.start()`
        # uses for its own transports (see `OpikConfig`'s docstring) — this
        # adapter path builds its own `TransportRegistry`/`AgentRuntime`
        # directly rather than going through `ControlPlane`, so it has to
        # replicate the gate rather than inherit it. `OPIK_PROJECT_NAME` is
        # optional; Opik falls back to its default project when unset, same
        # as the legacy `OpikTracer()` call site (`utils/streaming.py`).
        opik_active = is_opik_configured()
        opik_project_name = os.getenv("OPIK_PROJECT_NAME")

        transport_registry = TransportRegistry()
        transport_registry.register(
            "langchain",
            lambda: wrap_if_enabled(
                LangChainTransport(llm, model_name=model_name, opik_project_name=opik_project_name),
                enabled=opik_active,
                project_name=opik_project_name,
            ),
        )

        # Gate on the SAME resolution the legacy LangGraph path uses
        # (per-request state flag -> PIPESHUB_ENABLE_CODE_EXECUTION env ->
        # ENABLE_CODE_EXECUTION Labs feature flag -> default True) rather
        # than re-deriving an env-only check here, so the admin Labs toggle
        # applies identically to both paths.
        code_exec_enabled = code_execution_enabled(context.tool_state)
        logger.info(
            "PipesHubAgentFactory.create: code_execution_enabled=%s (org_id=%s conversation_id=%s)",
            code_exec_enabled, context.org_id, context.conversation_id,
        )

        # `skip_apps={"coding_sandbox"}` drops the legacy
        # execute_python/execute_typescript tools once agent_loop_lib's own
        # run_code is registered below, so the model never sees two
        # competing code-execution tools. database_sandbox tools are always
        # kept — agent_loop_lib has no equivalent ephemeral SQL sandbox.
        tool_registry = PipesHubToolLoader().load(
            context, skip_apps={"coding_sandbox"} if code_exec_enabled else set(),
        )

        # Resolved ONCE per request and threaded into every surface the
        # model sees or that shapes `run_code`'s actual behavior — the
        # sandbox manager/tool, the package-policy deny message, the
        # planner's upfront-plan steering, and (via `sandbox_has_network`
        # below) the system prompt — so none of them can disagree about
        # whether the sandbox has network access this turn.
        network_enabled = sandbox_network_enabled()

        sandbox_manager = None
        if code_exec_enabled:
            sandbox_manager = build_coding_sandbox_manager(allow_network=network_enabled)
            register_coding_sandbox_tools(tool_registry, sandbox_manager, allow_network=network_enabled)
            # Stashed on the context (not returned from create()) so
            # stream_bridge.py's finally block can tear it down without
            # widening this method's (Agent, AgentRuntime) return contract.
            context.sandbox_manager = sandbox_manager
            logger.info(
                "PipesHubAgentFactory.create: registered coding-sandbox tools: %s (network=%s)",
                [n for n in tool_registry.names() if n in ("run_code", "install_packages", "read_sandbox_file")],
                network_enabled,
            )
        else:
            logger.info(
                "PipesHubAgentFactory.create: code execution disabled — coding-sandbox tools "
                "(run_code/install_packages/read_sandbox_file) will NOT be available this turn"
            )

        hooks = self._build_hooks(context, sandbox_manager, allow_network=network_enabled)

        # Domain-agent composition, planning half (see domain_agents.py):
        # decided HERE, before loop routing, purely by claiming registered
        # tool names — no AgentSpec/AgentTool is built yet (that needs the
        # AgentRuntime, which doesn't exist until below). This is load-
        # bearing ordering, not a style choice: the "quick" route's
        # `PlanAheadPlanner` (inside `select_loop_and_goal` below) must be
        # steered with the SAME top-level names the executing agent will
        # end up granted, or its upfront plan references tools (`run_code`,
        # `web_search`, ...) the agent no longer has directly. Discarded
        # entirely for `OrchestratorLoop` (deep mode keeps its own
        # spawn_agent dispatch, never composed agent-tool delegates) —
        # computed unconditionally anyway since the route itself isn't
        # known until `select_loop_and_goal()` returns.
        composition_plan = plan_domain_agents(tool_registry) if _composed_agents_enabled() else None
        planning_tool_names = (
            composition_plan.top_level_names if composition_plan is not None else tool_registry.names()
        )

        loop, goal, clarifying_questions = await select_loop_and_goal(
            chat_mode=chat_mode,
            query=query,
            llm=llm,
            context=context,
            tool_names=planning_tool_names,
            sandbox_has_network=network_enabled,
            opik_active=opik_active,
            opik_project_name=opik_project_name,
        )

        # Phase 10: the deep agent's top-level `Agent` is a pure
        # planner/dispatcher — it must never call connector tools directly
        # (that's what spawned, domain-scoped sub-agents are for), so its
        # own `tool_names` is narrowed to the four coordination tools while
        # every OTHER loop keeps the composed (or, with composition off,
        # full unrestricted) registry.
        spec_factory = None
        if isinstance(loop, OrchestratorLoop):
            register_coordination_tools(tool_registry)
            tool_names = list(COORDINATION_TOOL_NAMES)
            spec_factory = domain_spec_factory(
                provider="langchain",
                model_name=model_name,
                default_tool_names=[
                    name for name in tool_registry.names() if name not in COORDINATION_TOOL_NAMES
                ],
                context=context,
            )
        else:
            tool_names = planning_tool_names

        logger.info(
            "PipesHubAgentFactory.create: loop=%s | %d tool(s) granted to agent spec "
            "(run_code available=%s)",
            loop.name if hasattr(loop, "name") else type(loop).__name__,
            len(tool_names), "run_code" in tool_registry.names(),
        )

        runtime = AgentRuntime(
            transport_registry=transport_registry,
            tool_registry=tool_registry,
            hooks=hooks,
            event_emitter=SSEEventEmitter(context.event_sink) if context.event_sink is not None else None,
            spec_factory=spec_factory,
            opik_enabled=opik_active,
            opik_project_name=opik_project_name,
        )

        # Domain-agent composition, registration half: now that the
        # AgentRuntime exists, actually build each claimed domain's child
        # AgentSpec and register it as an AgentTool on the shared registry.
        # Skipped for OrchestratorLoop (see above) even when composition is
        # otherwise enabled for this request.
        if composition_plan is not None and not isinstance(loop, OrchestratorLoop):
            flat_count = len(tool_registry.names())
            tool_names = register_domain_agents(
                composition_plan, tool_registry, runtime, context,
                provider="langchain", model_name=model_name,
            )
            logger.info(
                "PipesHubAgentFactory.create: composed top-level agent — %d flat tool(s) -> %d "
                "(domain agents + residual): %s",
                flat_count, len(tool_names), tool_names,
            )

        prompt_builder = PipesHubPromptBuilder(context, ToolGuidanceProvider())

        spec = AgentSpec(
            name="pipeshub-agent",
            system_prompt=prompt_builder,
            tool_names=tool_names,
            model=ModelSpec(provider="langchain", model=model_name),
            loop=loop,
            max_turns=_MAX_TURNS,
        )

        agent = Agent(spec, runtime, session_id=session_id)

        if context.previous_conversations:
            await self._seed_conversation_history(agent, context.previous_conversations)

        return agent, runtime, goal, clarifying_questions

    @staticmethod
    def _build_hooks(
        context: "AgentContext", sandbox_manager: Any = None, *, allow_network: bool = False,
    ) -> HookRegistry:
        """Phase 5's five hooks, wired onto a fresh `HookRegistry` (never a
        shared/global one — see that phase's hook docstrings for why
        per-request instances matter for `ToolErrorTracker`/`CitationCollector`
        state isolation across concurrent requests)."""
        hooks = HookRegistry()

        # Context-shaping (PRE_MODEL, cheapest-first — same ordering
        # `ControlPlane.start()` uses for its own `context_engine` hooks):
        # this adapter path builds its own `HookRegistry` directly rather
        # than going through `ControlPlane`, so none of that pipeline
        # applies unless wired here too. Multi-tool turns with large
        # results (e.g. a Jira search returning 50+ tickets) would
        # otherwise grow the outgoing context unbounded within a single
        # request. `offload`/`auto_compact` are deliberately NOT wired
        # here — `auto_compact` needs an LLM summarizer bound to this
        # request's transport, which would need threading the transport
        # registry/model name into this staticmethod; these three (all
        # pure-Python, no LLM call) already cover the common blow-up cases.
        # `ContextBudget` itself needs no wiring here — `Agent.step()`
        # (`agent/__init__.py`) computes it fresh every turn via
        # `ContextBudget.for_model(spec.model.model)` regardless of caller.
        hooks.on(HookEvent.PRE_MODEL).use(shape_budget_reduction())
        hooks.on(HookEvent.PRE_MODEL).use(shape_tool_result_clearing())
        hooks.on(HookEvent.PRE_MODEL).use(shape_sliding_window())

        # LLM transport retry (429/5xx/network) with SSE "retrying..." status
        # feedback — see retry_with_status.py's docstring for why this lives
        # here instead of agent_loop_lib's own RetryHook (which ControlPlane
        # wires by default but this adapter path never goes through).
        hooks.wrapper(HookEvent.PRE_MODEL_CALL).use(retry_with_status(context.event_sink))

        error_tracker = ToolErrorTracker()
        hooks.on(HookEvent.PRE_TOOL_USE).use(error_tracker.pre_tool_use)
        hooks.on(HookEvent.POST_TOOL_USE).use(error_tracker.post_tool_use)

        collector = CitationCollector(context)
        hooks.on(HookEvent.POST_TOOL_USE).use(citation_tracking(context, collector))

        hooks.on(HookEvent.PRE_TOOL_USE).use(stash_tool_call_metadata)
        hooks.on(HookEvent.POST_TOOL_USE).use(result_accumulation(context))

        hooks.on(HookEvent.POST_TOOL_USE).use(ask_user_question_sse(context))

        hooks.on(HookEvent.PRE_TURN).use(conversation_enrichment(context))

        # This adapter path builds its own HookRegistry directly (never
        # goes through ControlPlane.start()), so the coding_sandbox_safety
        # auto-add there does not apply — it, and the artifact/package-policy
        # bridge hooks, must be wired explicitly here.
        if sandbox_manager is not None:
            register_coding_sandbox_hooks(hooks, context, sandbox_manager, allow_network=allow_network)

        return hooks

    @staticmethod
    async def _seed_conversation_history(agent: Agent, previous_conversations: list[dict[str, Any]]) -> None:
        """Loads prior turns into the agent's `ContextManager` so the model
        sees them as ordinary conversation history on turn 0 — the
        agent-loop equivalent of `nodes.py::_build_conversation_messages`
        (called from `_build_planner_messages`, which both the legacy
        planner AND `react_agent_node` use to seed multi-turn context).

        Text-only for attachments: unlike `_build_conversation_messages`,
        this does not re-fetch/interleave historical PDF or image
        attachment blocks — `conversation_enrichment` (Phase 5, PRE_TURN)
        already covers the practical "reuse what the previous turn fetched"
        signal via `goal.constraints`. The current turn's OWN attachments
        are NOT resolved into multimodal blocks anywhere on this path today
        — `RespondPipeline` used to do that right before its own
        (now-removed, see `respond.py`) second LLM call, but the ReAct
        loop's tool-calling turns never saw them either, so this was always
        a synthesis-only capability, not a general one. Wiring current-turn
        attachments into this method (agent-loop's `UserMessage` already
        supports multimodal `Part` lists — see `agent_loop_lib/core/
        messages.py` and `converters.py`) is a tracked follow-up.

        NOT text-only for tool calls, though: see `_convert_conversation_turn`.
        """
        from app.agent_loop_lib.context.manager import ContextManager

        ctx = ContextManager()
        for turn in previous_conversations:
            for message in _convert_conversation_turn(turn):
                await ctx.add(message)
        # `Agent.run()` only builds its own `ContextManager` when `self.
        # _context is None` (see agent/__init__.py) — pre-seeding here,
        # before `run()` is ever called, is the documented extension point
        # for exactly this (`run_child()` does the same thing).
        agent._context = ctx


def _convert_conversation_turn(turn: dict[str, Any]) -> list[Message]:
    """One `previousConversations` entry -> zero or more agent-loop
    `Message`s. `user_query` is always a single `UserMessage`.

    `bot_response` used to always collapse to one plain-text
    `AssistantMessage`, silently dropping any tool calls that turn made —
    the model would see a past answer with no record of HOW it was
    produced, unlike the ReAct loop's OWN live turns (which always thread
    `AssistantMessage(tool_calls=[...])` -> `ToolMessage`s -> final
    `AssistantMessage`). If the turn dict carries a `tool_results` list in
    the SAME shape `result_accumulation.py` already appends to
    `all_tool_results` (`tool_name`/`result`/`status`/`tool_id`/`args` —
    also what `completion_data["tool_results"]` sends the frontend every
    turn, on both the agent-loop and legacy paths, so no new wire format is
    invented here), it's reconstructed as that same tool_calls/ToolMessage
    sequence before the final answer.

    `tool_results` is NOT populated in `previousConversations` today — the
    frontend/conversation-storage layer doesn't persist or resend it yet,
    so every turn falls back to the old plain-text-only path in practice.
    This makes the Python side ready for when that data is wired through,
    without waiting on it (tracked as a follow-up: persisting
    `completion_data["tool_results"]` per bot_response message and
    resending it as `previousConversations[i]["tool_results"]`).
    """
    role = turn.get("role", "")
    content = str(turn.get("content", "")).strip()

    if role == "user_query":
        return [UserMessage(content=content)] if content else []

    if role != "bot_response":
        return []

    messages: list[Message] = []
    tool_results = turn.get("tool_results") or []
    tool_calls: list[ToolCall] = []
    tool_messages: list[ToolMessage] = []
    for i, entry in enumerate(tool_results):
        if not isinstance(entry, dict):
            continue
        call_id = str(entry.get("tool_id") or f"history_{i}")
        args = entry.get("args")
        result = entry.get("result", "")
        tool_calls.append(ToolCall(
            id=call_id,
            name=str(entry.get("tool_name") or "unknown_tool"),
            arguments=args if isinstance(args, dict) else {},
        ))
        tool_messages.append(ToolMessage(
            content=result if isinstance(result, str) else json.dumps(result, default=str),
            tool_call_id=call_id,
            is_error=entry.get("status") == "error",
        ))

    if tool_calls:
        messages.append(AssistantMessage(content=[], tool_calls=tool_calls))
        messages.extend(tool_messages)
    if content:
        messages.append(AssistantMessage(content=content))
    return messages


__all__ = ["PipesHubAgentFactory"]
