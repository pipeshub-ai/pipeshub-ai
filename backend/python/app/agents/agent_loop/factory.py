"""`PipesHubAgentFactory`: Phase 7 — assembles an agent-loop `Agent` +
`AgentRuntime` from PipesHub's per-request `AgentContext`, wiring together
every adapter-layer piece built in Phases 2-6:

- `LangChainTransport` (Phase 2) registered under the `"langchain"` provider
- `PipesHubToolLoader` (Phase 3) for the per-request `ToolRegistry`
- `PipesHubPromptBuilder` (Phase 4) for the system prompt
- Phase 5's hook middleware (tool blocking, citation tracking, conversation
  memory, result accumulation, `ask_user_question` SSE)
- `SSEEventEmitter` (this phase) for real-time tool-orchestration events
- `router.select_loop_and_goal()` (this phase + intent) for `chatMode` ->
  `LoopStrategy` + the resolved `ModeDefinition` (`modes.py`), AND (every
  mode) the intent-parsed `Goal` the agent runs with — see `intent.py` for
  the merged intent+routing call
- Mode-driven composition, keyed off the resolved `ModeDefinition` (see
  `modes.py` for the current catalog), never off `isinstance(loop, ...)`:
  - `loop_kind == "orchestrator"` (deep): the top-level `spec.tool_names`
    is narrowed to the four coordination tools (`create_plan`/
    `critique_plan`/`spawn_agent`/`verify_result` — registered onto the
    request's `tool_registry` here) and `AgentRuntime.spec_factory` is
    wired so `spawn_agent` can resolve a domain "role" string into a
    scoped child `AgentSpec` — see `loops/orchestrator.py` for the full
    Decompose/Critique/Dispatch/Verify design rationale. When
    `compose_domain_agents` is also set (deep's catalog entry always has
    it on), the spawn pool becomes composed domain-agent delegates +
    residual instead of the raw flat tool list.
  - `compose_domain_agents` (react/planExecute; off for quick): for every
    OTHER loop kind, the top-level `spec.tool_names` is narrowed to a
    handful of `AgentTool` delegates (coding/web/internal-search/
    calculator/calendar) plus whatever tools no domain claimed —
    `plan_domain_agents()` runs BEFORE `select_loop_and_goal()` so the
    `planExecute` mode's planner is steered with the same composed names
    the executing agent ends up with, and `register_domain_agents()` runs
    after the `AgentRuntime` exists to actually build/register the child
    specs. Quick mode skips this entirely and keeps the flat registry.

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
    ToolMessageMeta,
    UserMessage,
)
from app.agent_loop_lib.events.base import CompositeEmitter
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin._message_boundaries import (
    shape_tool_pairing_repair,
)
from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
    shape_artifact_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.artifact_registration import (
    shape_artifact_registration,
)
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import (
    make_llm_summarizer,
    shape_auto_compact,
)
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    shape_budget_reduction,
)
from app.agent_loop_lib.hooks.middleware.builtin.deterministic_compact import (
    shape_deterministic_compact,
)
from app.agent_loop_lib.hooks.middleware.builtin.loop_compaction import (
    shape_loop_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import (
    shape_sliding_window,
)
from app.agent_loop_lib.hooks.middleware.builtin.synthesis_guard import (
    shape_synthesis_guard,
)
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    shape_tool_result_clearing,
)
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.data.retrieve_artifact import (
    RetrieveArtifactContentTool,
)
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import CodingSandboxTool
from app.agent_loop_lib.transport.opik_tracing import (
    resolve_opik_gate,
    traced_transport_factory,
)
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.artifact_store import build_artifact_store
from app.agents.agent_loop.direct_transport import build_direct_transport
from app.agents.agent_loop.domain_agents import (
    plan_domain_agents,
    register_domain_agents,
)
from app.agents.agent_loop.hooks import (
    CitationCollector,
    ToolErrorTracker,
    artifact_context_reminder,
    ask_user_question_sse,
    attachment_rehydration,
    citation_tracking,
    completion_gate,
    conversation_enrichment,
    resolve_attachments_for_goal,
    resolve_history_attachments,
    result_accumulation,
    retry_with_status,
    seed_visible_tools_from_history,
    shape_image_injection,
    shape_retrieved_image_injection,
    stash_tool_call_metadata,
)
from app.agents.agent_loop.langchain_transport import (
    LangChainTransport,
    _supports_multipart_tool_result,
)
from app.agents.agent_loop.lazy_tools_wiring import (
    CONNECTORS_PARENT,
    META_TOOL_NAMES,
    lazy_tools_enabled,
    lazy_tools_scope,
    make_lazy_tools_decider,
    register_lazy_tool_meta_tools,
    register_tool_preloading,
)
from app.agents.agent_loop.loops.orchestrator import (
    COORDINATION_TOOL_NAMES,
    domain_spec_factory,
    install_phase_gate,
    register_coordination_tools,
)
from app.agents.agent_loop.loops.plan_execute import PLANNING_TOOL_NAMES, register_planning_tools
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.protocol.agui_emitter import AGUIEventEmitter
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector
from app.agents.agent_loop.router import select_loop_and_goal
from app.agents.agent_loop.sandbox_bridge import (
    build_coding_sandbox_manager,
    register_coding_sandbox_hooks,
    register_coding_sandbox_tools,
    sandbox_network_enabled,
)
from app.agents.agent_loop.skills_wiring import (
    DOMAIN_SHARED_SKILL_TOOL_NAMES,
    build_skill_manager,
    register_skill_learning,
    register_skill_preloading,
    register_skill_tools,
    skills_enabled,
)
from app.agents.agent_loop.sse_emitter import SSEEventEmitter
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer
from app.agents.mcp.service import is_mcp_enabled


def _register_final_answer_if_enabled(tool_registry: "ToolRegistry") -> None:
    """Register FinalAnswerTool when PIPESHUB_ENABLE_FINAL_ANSWER is set."""
    from app.agent_loop_lib.tools.builtin.planning.final_answer import (
        FinalAnswerTool,
        final_answer_enabled,
    )
    if final_answer_enabled():
        tool_registry.register_tool(FinalAnswerTool())
from app.modules.agents.qna.tool_system import code_execution_enabled

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agent_loop_lib.core.types import Goal
    from app.agent_loop_lib.transport.base import LLMTransport
    from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Navigation tool names shared with every domain agent so children can resolve
# identifiers and browse the graph without being routed through the parent.
_DOMAIN_SHARED_NAV_TOOL_NAMES: frozenset[str] = frozenset({
    "knowledgegraph__navigate",
    "knowledgegraph__lookup_record",
})

# Matches nodes.py's ReAct/planner loop cap (`MAX_ITERATIONS` — see
# tool_system.py / react_agent_node's `recursion_limit`); kept as one named
# constant here rather than a magic number in `create()`.
_MAX_TURNS = 15


DIRECT_TRANSPORT = "direct"
LANGCHAIN_TRANSPORT = "langchain"


def _transport_provider() -> str:
    """Which registered transport the agent loop talks through.

    Two values only: "langchain" (default) or "direct", the provider's own SDK,
    which skips the per-token AIMessageChunk validation LangChain does. It stays
    a binary switch because a deployment chooses *how* it talks to providers;
    *which* provider a request uses comes from the model it selected, and
    `build_direct_transport` resolves that per request.

    "azure_direct" is accepted as a deprecated alias so existing deployments,
    compose files and load-test scripts keep working across the rename.

    Read per call rather than cached so an arm can be switched by restarting the
    service, without a rebuild.
    """
    raw = os.getenv("PIPESHUB_AGENT_TRANSPORT", LANGCHAIN_TRANSPORT).strip().lower()
    if raw in ("azure_direct", DIRECT_TRANSPORT):
        return DIRECT_TRANSPORT
    if raw and raw != LANGCHAIN_TRANSPORT:
        # Anything else -- a typo, a stale value, different casing -- would reach
        # TransportRegistry.resolve and raise RegistryError, failing every turn.
        # A misconfigured env var should cost the optimisation, not the service.
        logger.warning(
            "PIPESHUB_AGENT_TRANSPORT=%r is not a known transport; using %s",
            raw, LANGCHAIN_TRANSPORT,
        )
    return LANGCHAIN_TRANSPORT

# Phase-1 auto-compact (gentle): protects up to 70% of budget in the tail,
# summarizes only the oldest portion. Phase-2 (aggressive): if still over
# budget, shrinks tail to 40%, forcing more into the summary.
_AUTO_COMPACT_TRIGGER_RATIO = 0.85
_AUTO_COMPACT_PHASE1_TAIL_RATIO = 0.70
_AUTO_COMPACT_PHASE2_TAIL_RATIO = 0.40


def _composed_agents_enabled() -> bool:
    """Kill-switch for domain-agent composition (see `domain_agents.py`) —
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
        model_key: str | None = None,
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
        # Same gate `ControlPlane.start()` uses for its own transports (see
        # `resolve_opik_gate`'s docstring) — this adapter path builds its
        # own `TransportRegistry`/`AgentRuntime` directly rather than going
        # through `ControlPlane`, so nothing is inherited automatically;
        # calling the SAME shared function keeps the two from drifting.
        # `OPIK_PROJECT_NAME` is optional; Opik falls back to its default
        # project when unset, same as the legacy `OpikTracer()` call site
        # (`utils/streaming.py`).
        opik_active = resolve_opik_gate(True)
        opik_project_name = os.getenv("OPIK_PROJECT_NAME")

        transport_registry = TransportRegistry()
        transport_registry.register(
            "langchain",
            traced_transport_factory(
                lambda: LangChainTransport(
                    llm, model_name=model_name, opik_project_name=opik_project_name, model_key=model_key,
                ),
                opik_active=opik_active,
                project_name=opik_project_name,
            ),
        )
        # Same model as "langchain" above, reached through the provider's own
        # SDK instead. LangChain builds an AIMessageChunk per streamed token and
        # pydantic validates each one, measured at ~9% of query-service CPU.
        # Registered rather than substituted: selection is per ModelSpec, so
        # "langchain" stays the default and this is revertible without a deploy.
        # The whole configuration comes off `llm` -- not just credentials -- so
        # both transports issue the same request; `model_key` lets this one
        # share the learned api-mode store with the LangChain path.
        #
        # Which provider is decided from `llm` rather than from the env value:
        # with several models configured, one env string cannot describe the
        # provider of whichever model this request chose. A provider with no
        # direct transport falls back to LangChain rather than failing.
        #
        # Wrapped in the same tracing factory: traced_transport_factory works on
        # any LLMTransport, only build_langchain_opik_callbacks is LangChain-
        # specific, so leaving this bare simply lost the spans.
        def _direct_or_langchain() -> "LLMTransport":
            direct = build_direct_transport(
                llm, model_name=model_name, model_key=model_key,
            )
            if direct is not None:
                return direct
            return LangChainTransport(
                llm, model_name=model_name,
                opik_project_name=opik_project_name, model_key=model_key,
            )

        transport_registry.register(
            DIRECT_TRANSPORT,
            traced_transport_factory(
                _direct_or_langchain,
                opik_active=opik_active,
                project_name=opik_project_name,
            ),
        )

        # Gate on the SAME resolution the legacy LangGraph path uses
        # (per-request state flag -> PIPESHUB_ENABLE_CODE_EXECUTION env ->
        # default True) rather than re-deriving an env-only check here, so
        # both paths apply identically.
        code_exec_enabled = code_execution_enabled(context.tool_state)
        logger.info(
            "PipesHubAgentFactory.create: code_execution_enabled=%s (org_id=%s conversation_id=%s)",
            code_exec_enabled, context.org_id, context.conversation_id,
        )

        # `coding_sandbox` (legacy execute_python/execute_typescript) and
        # `database_sandbox` are ALWAYS skipped, regardless of
        # `code_exec_enabled`: when enabled, agent_loop_lib's own run_code
        # is registered below as the sole code-execution tool, so the model
        # never sees two competing code-execution tools; when disabled,
        # skipping `coding_sandbox` here is what actually makes
        # `PIPESHUB_ENABLE_CODE_EXECUTION` disable code execution —
        # `coding_sandbox` is `.as_internal()` (see coding_sandbox.py), so it
        # bypasses the "configured on this agent" gate in tool_loader.py and would
        # otherwise load unconditionally even with the flag off.
        # database_sandbox is dropped outright — its functionality is
        # subsumed by the coding sandbox's SQL capabilities.
        #
        # When knowledge is active, `knowledgegraph` covers both search and
        # file listing — skip the legacy `retrieval` and `knowledgehub` apps so
        # the model sees a single set of knowledge tools, not two competing
        # duplicates.  Old conversation history that mentions the retired names
        # still resolves through _tool_naming.py's INTERNAL_SEARCH_TOOL_NAMES.
        skip_apps: set[str] = {"coding_sandbox", "database_sandbox"}
        if context.has_knowledge:
            skip_apps |= {"retrieval", "knowledgehub"}
        tool_registry = await PipesHubToolLoader().load(
            context, skip_apps=skip_apps,
        )
        # MCP servers attached to this agent (or, for the assistant/placeholder
        # agent, every MCP instance the executing user has authenticated — see
        # `get_authenticated_mcp_servers`) — loaded right after connector
        # toolsets so their tools count toward every composition/lazy-disclosure
        # decision below exactly like any other tool. `context.mcp_servers`/
        # `mcp_server_configs` are empty for a request with no attached MCP
        # servers, so this is a cheap no-op in the common case (see
        # `MCPToolProvider.load_into`).
        #
        # Gated on `ENABLE_MCP` — belt-and-suspenders alongside `api/routes/agent.py`
        # forcing `agent_mcp_servers` empty when disabled, so MCP tools never load
        # into an agent regardless of entry point. The flag read is skipped when
        # nothing is attached (nothing to gate, and it saves a settings read on
        # every MCP-less chat).
        if not context.mcp_servers or await is_mcp_enabled(context.config_service):
            await MCPToolProvider().load_into(tool_registry, context)
        # Registered unconditionally (not just when lazy disclosure ends up
        # active — see `register_lazy_tool_meta_tools`'s docstring):
        # `search_tools` provides auth-aware global discovery in eager mode
        # too, and `list_toolsets`/`fetch_tools` are harmless no-ops when
        # nothing is grouped. Every tool-name list assembled below has
        # `META_TOOL_NAMES` appended so they're always in the grant.
        register_lazy_tool_meta_tools(tool_registry, context)

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

        # Skills subsystem (env-gated, off by default — see skills_wiring.py's
        # module docstring for the full rollout/ordering rationale). Built
        # and its tools registered BEFORE `plan_domain_agents()` runs below
        # so `skill_search`/`load_skill`/`skill_manage`/... land in that
        # call's registered-tool snapshot and fall into the residual (never
        # domain-claimed) top-level grant.
        skill_manager = None
        if skills_enabled():
            skill_manager = await build_skill_manager(context, transport_registry)
            if skill_manager is not None:
                register_skill_tools(tool_registry, skill_manager)
                logger.info(
                    "PipesHubAgentFactory.create: skills enabled — %d skill(s) in catalog "
                    "(org_id=%s)", len(skill_manager.catalog_snapshot()), context.org_id,
                )

        artifact_store = build_artifact_store(context)
        hooks = self._build_hooks(
            context, sandbox_manager, allow_network=network_enabled,
            artifact_store=artifact_store, tool_registry=tool_registry,
            transport_registry=transport_registry, model_name=model_name,
            supports_multipart_tool_result=_supports_multipart_tool_result(llm),
        )
        tool_registry.register_tool(RetrieveArtifactContentTool(store=artifact_store))
        _register_final_answer_if_enabled(tool_registry)
        if skill_manager is not None:
            register_skill_preloading(hooks, skill_manager)
        # Lazy-toolset preloading (env+threshold gated — see
        # lazy_tools_wiring.py's module docstring). Registered unconditionally
        # once the flag is on: the middleware itself no-ops for any spec that
        # ends up eager (default), so there's no harm registering it even if
        # the threshold below never actually flips anything to lazy this
        # request.
        if lazy_tools_enabled():
            register_tool_preloading(hooks)

        # Domain-agent composition, planning half (see domain_agents.py):
        # decided HERE, before loop routing, purely by claiming registered
        # tool names — no AgentSpec/AgentTool is built yet (that needs the
        # AgentRuntime, which doesn't exist until below). `composed_tool_names`
        # is only actually consumed downstream by the tool-grant block below
        # (`tool_names = [*composed_tool_names, *PLANNING_TOOL_NAMES]` for
        # `plan_execute`, `= composed_tool_names` for plain `react`/`quick`)
        # — `select_loop_and_goal`/`_build_loop` themselves ignore it now
        # that `plan_execute` plans via the `create_plan` TOOL on the SAME
        # agent spec instead of a construction-time `PlanAheadPlanner` that
        # needed steering separately (see `loops/plan_execute.py`). Still
        # computed unconditionally here since every `loop_kind` shares this
        # one call site.
        composition_plan = plan_domain_agents(tool_registry) if _composed_agents_enabled() else None
        composed_tool_names = (
            composition_plan.top_level_names if composition_plan is not None else tool_registry.names()
        )

        loop, goal, clarifying_questions, mode = await select_loop_and_goal(
            chat_mode=chat_mode,
            query=query,
            llm=llm,
            context=context,
            tool_names=composed_tool_names,
            sandbox_has_network=network_enabled,
            opik_active=opik_active,
            opik_project_name=opik_project_name,
            transport_registry=transport_registry,
        )

        # Stash model_name on context so ensure_fetch_full_record_available()
        # (called from attachment_resolver outside _build_hooks) and the
        # fetch-tool block cap can both reach it without the TransportRegistry.
        context.model_name = model_name

        if not clarifying_questions:
            attachment_text, image_blocks = await resolve_attachments_for_goal(
                context, logger,
            )
            if attachment_text:
                goal.description = (
                    f"{goal.description}\n\n{attachment_text}"
                    "\n\nWhen citing facts from attached documents, use the "
                    "Citation ID as a markdown link, e.g. [source](ref2). "
                    "Do NOT use record URLs or block indices as citation links."
                )
            if image_blocks:
                context.attachment_image_blocks = image_blocks

        # Per-mode composition/tool-grant, keyed off `mode` (the resolved
        # `ModeDefinition` — see `modes.py`), never off `isinstance(loop, ...)`:
        # - `loop_kind == "orchestrator"` (deep): the top-level `Agent` is a
        #   pure planner/dispatcher — it must never call connector tools
        #   directly (that's what spawned, domain-scoped sub-agents are
        #   for), so its own `tool_names` is narrowed to the four
        #   coordination tools regardless of `compose_domain_agents` (deep
        #   mode's catalog entry always has it on, but this branch would
        #   still be correct if it didn't). What DOES vary on
        #   `compose_domain_agents` for this loop kind is the SPAWN POOL:
        #   `domain_spec_factory`'s `default_tool_names` gets wired onto
        #   `runtime.spec_factory` further down, once domain agents (if
        #   any) are registered — a plain flat residual list otherwise.
        # - `loop_kind == "plan_execute"` (planExecute): the top-level
        #   `Agent` executes directly (no spawn pool) — its grant is its
        #   composed/flat tools PLUS the planning tools (`create_plan`/
        #   `critique_plan`/`verify_result`/`replan`, no `spawn_agent`)
        #   `PlanCritiqueExecuteLoop` composes over (`loops/plan_execute.py`).
        # - `mode.compose_domain_agents` (quick: off; react/planExecute:
        #   on): the top-level grant is either the composed names computed
        #   above or, when composition is off for this mode (or globally
        #   via the kill-switch), the full flat registry.
        spec_factory = None
        if mode.loop_kind == "orchestrator":
            register_coordination_tools(tool_registry)
            install_phase_gate(hooks)
            tool_names = list(COORDINATION_TOOL_NAMES)
        elif mode.loop_kind == "plan_execute":
            register_planning_tools(tool_registry)
            # `composed_tool_names` was snapshotted BEFORE this call — the
            # planning tools just registered above are never IN it, so they
            # must be added back explicitly rather than relying on
            # `tool_registry.names()` (which would also silently include
            # anything domain composition claimed away from the top level).
            tool_names = (
                [*composed_tool_names, *PLANNING_TOOL_NAMES]
                if mode.compose_domain_agents and composition_plan is not None
                else tool_registry.names()
            )
        elif mode.compose_domain_agents and composition_plan is not None:
            tool_names = composed_tool_names
        else:
            tool_names = tool_registry.names()

        # `tool_registry.names()` already includes them (registered above),
        # but the curated lists (composed/planning tools) do not — append
        # unconditionally so `search_tools`/`list_toolsets`/`fetch_tools`
        # are callable regardless of `tool_disclosure` (see
        # `register_lazy_tool_meta_tools`'s docstring for why eager mode
        # needs this too: `tool_schemas_for_turn` binds exactly
        # `spec.tool_names` when disclosure is eager). EXCEPT deep mode's
        # orchestrator: its own grant must stay exactly the four
        # coordination tools (see `lazy_tools_wiring.py`'s module
        # docstring — deep mode is out of scope for this pass; its spawn
        # pool, not its own grant, is where a large tool catalog lives).
        if mode.loop_kind != "orchestrator":
            for meta_name in META_TOOL_NAMES:
                if meta_name not in tool_names:
                    tool_names.append(meta_name)

        logger.info(
            "PipesHubAgentFactory.create: mode=%s | loop=%s | %d tool(s) granted to agent spec "
            "(run_code available=%s)",
            mode.name, loop.name if hasattr(loop, "name") else type(loop).__name__,
            len(tool_names), "run_code" in tool_registry.names(),
        )

        runtime = AgentRuntime(
            transport_registry=transport_registry,
            tool_registry=tool_registry,
            hooks=hooks,
            event_emitter=self._build_event_emitter(context),
            spec_factory=spec_factory,
            opik_enabled=opik_active,
            opik_project_name=opik_project_name,
            skills=skill_manager,
            summarizer=PipesHubToolSummarizer(),
        )
        if skill_manager is not None:
            # `hooks` here is the SAME HookRegistry instance now held by
            # `runtime.hooks` — appending the learning-loop middleware post
            # construction is equivalent to having registered it earlier;
            # it just needs `runtime` itself for `run_child()`, which
            # doesn't exist until this line.
            register_skill_learning(hooks, skill_manager, runtime, provider=_transport_provider(), model_name=model_name)

        # Domain-agent composition, registration half: now that the
        # AgentRuntime exists, actually build each claimed domain's child
        # AgentSpec and register it as an AgentTool on the shared registry.
        # Runs for `loop_kind == "orchestrator"` too now (deep mode always
        # composes — see `modes.py`): the names built there don't replace
        # the orchestrator's OWN `tool_names` (still just the four
        # coordination tools, set above) — they become `spawn_agent`'s
        # SPAWN POOL instead, wired onto `runtime.spec_factory` below.
        # `composition_plan.registered_names` was snapshotted BEFORE
        # `register_coordination_tools()` ran (composed above `select_
        # loop_and_goal`, when `mode` wasn't known yet), so `composed_names`
        # here never contains a coordination-tool name to filter out.
        # Whether `run_code` ends up reachable ONLY through `coding_agent`'s
        # `AgentTool` (built with `share_parent_results=True` — see
        # `domain_agents.py`) rather than granted directly to a flat,
        # non-delegated agent. This is exactly the condition under which
        # `PARENT_RESULTS_INPUT_PATH` staging can ever actually happen (see
        # `CodingSandboxTool.set_advertise_parent_results`'s docstring) —
        # False here covers quick mode (`compose_domain_agents=False`), the
        # `PIPESHUB_USE_COMPOSED_AGENTS` kill-switch, and a request where
        # `coding_agent` claimed nothing (code execution disabled).
        # Declarative essential set (see `@Toolset(essential=...)`/
        # `ToolsetBuilder.as_essential()`): whichever loaded toolset group
        # names `PipesHubToolLoader` marked essential this request (e.g.
        # "retrieval"/"knowledgehub" only when knowledge is attached,
        # "artifacts") plus `"skills"` when wired — the single source of
        # truth for `pinned_toolsets` below AND for what `group_connector_
        # toolsets` must never nest under `CONNECTORS_PARENT`. Computed
        # once, before any lazy-tools decision, so both the domain and
        # top-level deciders exclude the same set.
        essential_toolset_names = list(context.essential_toolset_names)
        if skill_manager is not None:
            essential_toolset_names = ["skills", *essential_toolset_names]

        run_code_delegated_to_coding_agent = False
        if composition_plan is not None and mode.compose_domain_agents:
            flat_count = len(tool_registry.names())
            domain_lazy_tools = make_lazy_tools_decider(
                apply=lazy_tools_enabled() and lazy_tools_scope() in ("domain", "both"),
                essential_names=frozenset(essential_toolset_names),
                context=context,
            )
            composed_names = register_domain_agents(
                composition_plan, tool_registry, runtime, context,
                provider=_transport_provider(), model_name=model_name,
                lazy_tools=domain_lazy_tools,
                shared_tool_names=(
                    (DOMAIN_SHARED_SKILL_TOOL_NAMES if skill_manager is not None else frozenset())
                    | _DOMAIN_SHARED_NAV_TOOL_NAMES
                ),
            )
            run_code_delegated_to_coding_agent = "coding_agent" in composed_names
            logger.info(
                "PipesHubAgentFactory.create: composed %s — %d flat tool(s) -> %d "
                "(domain agents + residual): %s",
                "spawn pool" if mode.loop_kind == "orchestrator" else "top-level agent",
                flat_count, len(composed_names), composed_names,
            )
            if mode.loop_kind == "orchestrator":
                runtime.spec_factory = domain_spec_factory(
                    provider=_transport_provider(), model_name=model_name,
                    default_tool_names=composed_names, context=context,
                )
            elif mode.loop_kind == "plan_execute":
                # `composition_plan` was snapshotted by `plan_domain_agents()`
                # BEFORE `register_planning_tools()` ran above, so its
                # residual-tool bookkeeping (`plan.registered_names`,
                # `domain_agents.py::register_domain_agents`) never saw the
                # four planning tools — `composed_names` here would silently
                # drop them without this explicit append, same reason the
                # interim `tool_names` computed further up needed it too.
                tool_names = [*composed_names, *PLANNING_TOOL_NAMES]
            else:
                tool_names = composed_names
        elif mode.loop_kind == "orchestrator":
            # Kill-switch off (or nothing to compose) — deep mode's spawn
            # pool falls back to the flat, uncomposed residual, same as
            # this feature's pre-existing behavior.
            runtime.spec_factory = domain_spec_factory(
                provider=_transport_provider(), model_name=model_name,
                default_tool_names=[
                    n for n in tool_registry.names() if n not in COORDINATION_TOOL_NAMES
                ],
                context=context,
            )

        # `run_code`'s own description (see `CodingSandboxTool.description`)
        # must agree with `run_code_delegated_to_coding_agent` computed
        # above — set AFTER composition is fully decided, on the one
        # instance already registered onto this request's `tool_registry`
        # (registration happened earlier, before mode/composition were
        # known — see `set_advertise_parent_results`'s docstring for why
        # this can't be a constructor-time decision instead).
        if code_exec_enabled and tool_registry.has("run_code"):
            coding_sandbox_tool = tool_registry.resolve_by_name("run_code")
            if isinstance(coding_sandbox_tool, CodingSandboxTool):
                coding_sandbox_tool.set_advertise_parent_results(run_code_delegated_to_coding_agent)
                # Same false-instruction bug the prompt-side fix addresses,
                # at the source: name only the web tool(s) actually reachable
                # by whichever agent ends up with `run_code` bound — the flat
                # pair when granted directly, `web_agent` when composed (only
                # ever nested onto `coding_agent` when it was itself claimed —
                # see `domain_agents.py`'s `delegate_names` filter), or none.
                from app.modules.agents.context.tool_names import granted, granted_any
                _web_agent = granted("web_agent", tool_names)
                if _web_agent:
                    web_tool_names: tuple[str, ...] = (_web_agent,)
                else:
                    web_tool_names = granted_any(["web_search", "fetch_url"], tool_names)
                coding_sandbox_tool.set_web_tool_names(web_tool_names)

        prompt_builder = PipesHubPromptBuilder(context)

        # Top-level lazy disclosure (env+threshold+scope gated — see
        # lazy_tools_wiring.py). Deliberately excludes `loop_kind ==
        # "orchestrator"`: that spec's own grant is always just the four
        # coordination tools (never large — see that module's docstring for
        # why deep mode's actual big pool, the spawn-pool default, is out of
        # scope for this pass). No-op (returns `tool_names` unchanged,
        # `tool_disclosure="eager"`) whenever the flag is off, this scope
        # wasn't selected, or `tool_names` doesn't exceed the threshold.
        top_level_lazy_tools = make_lazy_tools_decider(
            apply=(
                lazy_tools_enabled()
                and mode.loop_kind != "orchestrator"
                and lazy_tools_scope() in ("top_level", "both")
            ),
            essential_names=frozenset(essential_toolset_names),
            context=context,
        )
        tool_names, tool_disclosure = top_level_lazy_tools(tool_registry, tool_names)
        # Every toolset group `group_connector_toolsets` was told to leave
        # alone (skills, plus whichever internal toolsets loaded with
        # `essential=True` metadata this request — see
        # `essential_toolset_names` above) stays pinned back to essential
        # via `AgentSpec.pinned_toolsets`, so `initial_visible_tools()`
        # (`all_names - grouped + pinned`) includes them regardless of
        # whatever else got grouped under lazy disclosure this request. A
        # skill/knowledge-search/artifact need needs to be visible BEFORE
        # the model commits to an approach, not discovered after a
        # `fetch_tools` round trip — same reasoning `ControlPlane.start()`
        # already applies for skills (see `control_plane.py`'s "skills"
        # auto-pin), generalized here to every declared-essential toolset.
        pinned_toolsets = essential_toolset_names if tool_disclosure == "lazy" else []
        if tool_disclosure == "lazy":
            grouped = sorted(g.name for g in tool_registry.children_of(CONNECTORS_PARENT))
            logger.info(
                "PipesHubAgentFactory.create: tool_disclosure=lazy — %d tool(s) granted, "
                "%d grouped into toolset(s) %s, pinned toolset(s) %s (schemas loaded on "
                "demand via list_toolsets/fetch_tools/search_tools) (org_id=%s conversation_id=%s)",
                len(tool_names), len(grouped), grouped, pinned_toolsets, context.org_id, context.conversation_id,
            )
        else:
            logger.info(
                "PipesHubAgentFactory.create: tool_disclosure=eager — %d tool(s) bound eagerly "
                "(org_id=%s conversation_id=%s)",
                len(tool_names), context.org_id, context.conversation_id,
            )

        spec = AgentSpec(
            name="pipeshub-agent",
            system_prompt=prompt_builder,
            tool_names=tool_names,
            tool_disclosure=tool_disclosure,
            pinned_toolsets=pinned_toolsets,
            model=ModelSpec(provider=_transport_provider(), model=model_name),
            loop=loop,
            max_turns=_MAX_TURNS,
        )
        # Let `hooks/citations.py` grant `dynamic_fetch_full_record` to this
        # spec too once any nested search populates it — see
        # `AgentContext.root_agent_spec`'s docstring.
        context.root_agent_spec = spec

        agent = Agent(spec, runtime, session_id=session_id)
        # `AGUIFormatter` (direct EventSink writers — see protocol/formatter.py)
        # has no Agent/RunContext reference of its own; stash the top-level
        # run_id here, the one place both `context` and the freshly-built
        # `agent` are in scope.
        context.run_id = agent.run_ctx.run_id

        if context.previous_conversations:
            await self._seed_conversation_history(agent, context.previous_conversations, context)

        return agent, runtime, goal, clarifying_questions

    @staticmethod
    def _build_event_emitter(context: "AgentContext") -> Any:
        """`AGUIEventEmitter` composed with a `TranscriptCollector` (see
        that module's docstring for why it has to be wired at this same
        `EventEmitter` layer rather than downstream of it) when this
        request negotiated the `agui` protocol, `SSEEventEmitter` (today's
        behavior, byte-for-byte unchanged) otherwise — including every
        request that never set `context.protocol` at all. `None` when
        there's no streaming client for this call (background/test runs),
        same as before this method existed."""
        if context.event_sink is None:
            return None
        if context.protocol == "agui":
            emitter = AGUIEventEmitter(context.event_sink, thread_id=context.conversation_id or "")
            collector = TranscriptCollector()
            context.transcript_collector = collector
            return CompositeEmitter([emitter, collector])
        return SSEEventEmitter(context.event_sink)

    @staticmethod
    def _build_hooks(
        context: "AgentContext", sandbox_manager: Any = None, *, allow_network: bool = False,
        artifact_store: Any = None, tool_registry: Any = None,
        transport_registry: Any = None, model_name: str = "",
        supports_multipart_tool_result: bool = True,
    ) -> HookRegistry:
        """Phase 5's hooks, wired onto a fresh `HookRegistry` (never a
        shared/global one — see that phase's hook docstrings for why
        per-request instances matter for `ToolErrorTracker`/`CitationCollector`
        state isolation across concurrent requests)."""
        hooks = HookRegistry()

        # Exposed on tool_state so search/fetch tools (retrieval.py,
        # citations.py) can skip stashing into `pending_tool_images` when
        # the model already receives images natively via the multipart
        # ToolMessage — that stash only exists to feed
        # `shape_retrieved_image_injection`'s fallback, which is only
        # registered below (and thus only ever consumes the stash) when
        # this flag is False.
        context.tool_state["supports_multipart_tool_result"] = supports_multipart_tool_result

        # --- POST_TOOL_USE: artifact registration (Phase 1 of two-phase compaction) ---
        # Large tool results (>2K tokens) are persisted in the artifact store
        # and annotated with ToolMessageMeta.  Full content stays for the
        # current turn; PRE_MODEL shapers below compact it on later turns.
        if artifact_store is not None:
            def _resolve_schema(tool_name: str):
                if tool_registry is None:
                    return None
                try:
                    tool = tool_registry.resolve_by_name(tool_name)
                    return getattr(tool, "result_schema", None)
                except Exception:
                    return None

            hooks.on(HookEvent.POST_TOOL_USE).use(
                shape_artifact_registration(
                    store=artifact_store,
                    resolve_schema=_resolve_schema,
                    threshold_tokens=4_000,
                )
            )

        # --- PRE_MODEL context-shaping pipeline (cheapest-first, L0→L9) ---
        # Same ordering ControlPlane.start() uses for its context_engine.
        # L0 injects image attachments into the initial UserMessage (no-op
        # when context.attachment_image_blocks is empty — deferred check).
        # L1–L6 are pure-Python (no LLM call). L7a/L7b are LLM-backed
        # auto-compact — two phases so old conversation history is
        # summarized first (gentle, keep 12) and old turns second
        # (aggressive, keep 6) only when the gentle pass wasn't enough.
        # L9 is the safety-net: validates tool_call/tool_result pairing
        # after all shapers ran — catches any orphans from shaper
        # interactions or future shapers that don't use safe_tail_boundary.
        hooks.on(HookEvent.PRE_MODEL).use(shape_image_injection(context))     # L0
        if not supports_multipart_tool_result:
            # Ollama's transport (see `LangChainTransport`/`converters.py`)
            # strips images out of every ToolMessage before it reaches the
            # provider — this is the fallback that gets them to the model
            # anyway, via the same UserMessage-injection mechanism as L0.
            # Only registered for providers that actually need it so
            # OpenAI/Anthropic never see an image delivered twice.
            hooks.on(HookEvent.PRE_MODEL).use(shape_retrieved_image_injection(context))  # L0.1
        hooks.on(HookEvent.PRE_MODEL).use(shape_budget_reduction())           # L1
        hooks.on(HookEvent.PRE_MODEL).use(shape_artifact_compaction(          # L2
            keep_last_n_turns=2,
        ))
        hooks.on(HookEvent.PRE_MODEL).use(shape_tool_result_clearing(         # L3
            protected_tool_names=frozenset({
                "create_plan", "critique_plan",
                # WebToolAdapter's Citation IDs (`url/Citation ID: ...`)
                # are only readable from the tool result itself — clearing
                # it makes every citation the model already saw unciteable
                # on a later turn. `shape_artifact_compaction` above still
                # summarizes these after `keep_last_n_turns=2` (see
                # `_find_web_record_by_url`'s page-level fallback for why
                # that's an acceptable, not fully solved, trade-off).
                "dynamic__web_search", "dynamic__fetch_url",
                # Same reasoning: fetch_full_record results carry [refN]
                # markers that AnswerFinalizer needs to build citations.
                "knowledgegraph__fetch_record",
            }),
        ))
        hooks.on(HookEvent.PRE_MODEL).use(shape_loop_compaction())            # L4
        hooks.on(HookEvent.PRE_MODEL).use(shape_sliding_window())             # L5
        hooks.on(HookEvent.PRE_MODEL).use(shape_deterministic_compact())      # L6

        if transport_registry is not None:
            summarizer = make_llm_summarizer(transport_registry, "langchain", model_name)
            hooks.on(HookEvent.PRE_MODEL).use(shape_auto_compact(             # L7a
                summarizer=summarizer,
                trigger_ratio=_AUTO_COMPACT_TRIGGER_RATIO,
                max_tail_ratio=_AUTO_COMPACT_PHASE1_TAIL_RATIO,
            ))
            hooks.on(HookEvent.PRE_MODEL).use(shape_auto_compact(             # L7b
                summarizer=summarizer,
                trigger_ratio=_AUTO_COMPACT_TRIGGER_RATIO,
                max_tail_ratio=_AUTO_COMPACT_PHASE2_TAIL_RATIO,
            ))

        hooks.on(HookEvent.PRE_MODEL).use(shape_synthesis_guard())            # L8
        hooks.on(HookEvent.PRE_MODEL).use(shape_tool_pairing_repair())       # L9

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
        hooks.on(HookEvent.PRE_TURN).use(attachment_rehydration(context))
        hooks.on(HookEvent.PRE_TURN).use(artifact_context_reminder(context))
        hooks.on(HookEvent.PRE_TURN).use(seed_visible_tools_from_history(context))

        # Recovers from empty model responses (no text, no tool calls).
        hooks.on(HookEvent.POST_MODEL).use(completion_gate(context))

        # This adapter path builds its own HookRegistry directly (never
        # goes through ControlPlane.start()), so the coding_sandbox_safety
        # auto-add there does not apply — it, and the artifact/package-policy
        # bridge hooks, must be wired explicitly here.
        if sandbox_manager is not None:
            register_coding_sandbox_hooks(
                hooks, context, sandbox_manager,
                allow_network=allow_network, artifact_store=artifact_store,
            )

        return hooks

    @staticmethod
    async def _seed_conversation_history(
        agent: Agent,
        previous_conversations: list[dict[str, Any]],
        context: "AgentContext",
    ) -> None:
        """Loads prior turns into the agent's `ContextManager` so the model
        sees them as ordinary conversation history on turn 0 — the
        agent-loop equivalent of `nodes.py::_build_conversation_messages`.

        Document attachments (PDF, text, markdown) on historical user_query
        turns are resolved from blob storage and appended to the
        corresponding ``UserMessage``, mirroring the old agent's behavior
        where the model always has file content directly in context.
        ``virtual_record_id_to_result`` and ``citation_ref_mapper`` are
        populated as a side-effect so ``dynamic_fetch_full_record`` remains
        available as a post-compaction fallback.
        """
        from app.agent_loop_lib.context.manager import ContextManager

        blob_store = context.blob_store
        org_id = context.org_id

        state = context.tool_state
        ref_mapper = state.get("citation_ref_mapper")
        if ref_mapper is None:
            from app.utils.chat_helpers import CitationRefMapper  # noqa: PLC0415
            ref_mapper = CitationRefMapper()
            state["citation_ref_mapper"] = ref_mapper

        vrmap: dict[str, Any] = state.get("virtual_record_id_to_result") or {}
        if not isinstance(vrmap, dict):
            vrmap = {}
        state["virtual_record_id_to_result"] = vrmap

        is_multimodal = context.is_multimodal_llm
        from app.utils.chat_helpers import ImageBudget  # noqa: PLC0415
        image_budget: ImageBudget = state.setdefault("image_budget", ImageBudget())

        ctx = ContextManager()
        for turn in previous_conversations:
            messages = _convert_conversation_turn(turn)

            if (
                turn.get("role") == "user_query"
                and blob_store
                and messages
            ):
                attachments = turn.get("attachments") or []
                if attachments:
                    extra_text, image_blocks = await resolve_history_attachments(
                        attachments, blob_store, org_id, ref_mapper, vrmap,
                        is_multimodal_llm=is_multimodal,
                        image_budget=image_budget,
                    )
                    msg = messages[0]
                    if extra_text:
                        msg.content = (
                            f"{msg.content}\n\nAttached documents:\n{extra_text}"
                            "\n\nWhen citing facts from these documents, use the "
                            "Citation ID as a markdown link, e.g. [source](ref2). "
                            "Do NOT use record URLs or block indices as citation links."
                        )
                    if image_blocks and is_multimodal:
                        _inject_images_into_message(msg, image_blocks)
                    elif image_blocks:
                        names = [
                            a.get("recordName", "image")
                            for a in attachments
                            if (a.get("mimeType") or "").lower().startswith("image/")
                        ]
                        placeholder = "\n".join(
                            f"[Image attached: {n}]" for n in names
                        )
                        msg.content = f"{msg.content}\n\n{placeholder}"

            for message in messages:
                await ctx.add(message)

        agent.seed_context(ctx)


def _inject_images_into_message(
    msg: UserMessage,
    image_blocks: list[dict[str, Any]],
) -> None:
    """Convert ``image_url`` dicts to ``ImagePart`` and set multipart content."""
    from app.agent_loop_lib.core.messages import TextPart  # noqa: PLC0415
    from app.agents.agent_loop.hooks.attachment_resolver import (  # noqa: PLC0415
        _langchain_image_to_part,
    )

    parts = [_langchain_image_to_part(b) for b in image_blocks]
    image_parts = [p for p in parts if p is not None]
    if not image_parts:
        return

    content = msg.content
    if isinstance(content, str):
        msg.content = [TextPart(text=content), *image_parts]
    elif isinstance(content, list):
        msg.content = [*content, *image_parts]


# Two previous `_EXPLORATION_RESULT_NOTE` headers (see `domain_agents.py`)
# shipped inside tool results and tripped Azure OpenAI's jailbreak /
# content-filter shield:
#
# V1: "[SYSTEM NOTE — how to use the findings above in your answer]"
#     Blatant system-role impersonation — 400'd immediately.
# V2: "Guidance for using the findings above in the final answer:"
#     Dropped the fake role marker but kept "Rules:" + imperative bullets
#     ("Do NOT ...", "**Match...**") — still triggered the filter in
#     `planExecute` mode, where the tool result sits in the top-level LLM
#     context alongside injected phase-instruction user messages (a denser
#     instruction surface than deep mode's child-agent context).
#
# Conversations persisted under EITHER header replay their bounded
# tool-result transcripts verbatim through `_convert_conversation_turn`
# on every later turn, so both must be neutralized here or an affected
# conversation stays permanently broken. The V3 header ("About these
# findings:") is purely descriptive — see `domain_agents.py`'s comment.
_V1_NOTE_HEADER = "[SYSTEM NOTE — how to use the findings above in your answer]"
_V2_NOTE_HEADER = "Guidance for using the findings above in the final answer:"
_NEUTRAL_NOTE_HEADER = "About these findings:"


def _scrub_legacy_system_note(text: str) -> str:
    text = text.replace(_V1_NOTE_HEADER, _NEUTRAL_NOTE_HEADER)
    return text.replace(_V2_NOTE_HEADER, _NEUTRAL_NOTE_HEADER)


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
    `all_tool_results` (`tool_name`/`result`/`status`/`tool_id`/`args`),
    it's reconstructed as that same tool_calls/ToolMessage sequence before
    the final answer.

    The Node.js conversation layer (`formatPreviousConversations`, see
    `enterprise_search/utils/utils.ts`) populates `tool_results` from each
    persisted message's already-bounded `parts` transcript (`toolName`/
    `resultSummary`/`resultPreview`/`status` — the same size-capped data
    `TranscriptCollector` writes, NOT a full-payload resend: this module
    deliberately stopped shipping `completion_data["tool_results"]`
    verbatim, see `_tool_names_from_state` in `respond.py`) — `result`
    here is that bounded summary/preview text, and `args` is best-effort
    (only present when the persisted `args` string happened to be JSON).
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
        tool_name = str(entry.get("tool_name") or "unknown_tool")
        tool_calls.append(ToolCall(
            id=call_id,
            name=tool_name,
            arguments=args if isinstance(args, dict) else {},
        ))
        result_str = result if isinstance(result, str) else json.dumps(result, default=str)
        summary_str = entry.get("result_summary") or ""

        artifact_id = entry.get("artifact_id")
        artifact_meta = None
        if isinstance(artifact_id, str) and artifact_id:
            display_summary = summary_str or (result_str[:200] if result_str else "")
            artifact_meta = ToolMessageMeta(
                artifact_id=artifact_id,
                summary=display_summary,
                tool_name=tool_name,
                tool_args=args if isinstance(args, dict) else None,
                result_schema=None,
                original_token_count=0,
                turn_index=-1,
            )
            compact_lines = [
                f"[artifact:{artifact_id}]",
                f"tool: {tool_name}",
            ]
            if isinstance(args, dict) and args:
                args_str = json.dumps(args, default=str)
                if len(args_str) > 200:
                    args_str = args_str[:200] + "..."
                compact_lines.append(f"args: {args_str}")
            if display_summary:
                compact_lines.append(f"summary: {display_summary}")
            compact_lines.append(
                f'hint: Use retrieve_artifact_content(artifact_id="{artifact_id}") '
                "to read, filter, and curate this data before using it"
            )
            result_str = "\n".join(compact_lines)

        tool_messages.append(ToolMessage(
            content=_scrub_legacy_system_note(result_str),
            tool_call_id=call_id,
            is_error=entry.get("status") == "error",
            artifact_meta=artifact_meta,
        ))

    if tool_calls:
        messages.append(AssistantMessage(content=[], tool_calls=tool_calls))
        messages.extend(tool_messages)
    if content:
        messages.append(AssistantMessage(content=_scrub_legacy_system_note(content)))
    return messages


__all__ = ["PipesHubAgentFactory"]
