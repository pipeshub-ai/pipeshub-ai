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
    handful of `AgentTool` delegates (coding/web/internal-search) plus
    whatever tools no domain claimed —
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
import re
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.context.pipeline import ContextProfile, build_context_pipeline
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
    dedupe_tool_call_ids,
)
from app.agent_loop_lib.events.base import CompositeEmitter
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.artifact_registration import (
    shape_artifact_registration,
)
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import (
    make_llm_summarizer,
)
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.sandbox.coding.settings import SandboxUnavailableError
from app.agent_loop_lib.tools.builtin.data.retrieve_artifact import (
    RetrieveArtifactContentTool,
)
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import CodingSandboxTool
from app.agent_loop_lib.transport.opik_tracing import (
    resolve_opik_gate,
    traced_transport_factory,
)
from app.agent_loop_lib.transport.prompt_cache import CacheScope, PromptCacheSettings
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.agents.actions.knowledge_graph.ops.timeline import TIMELINE_TOOL_NAME
from app.agents.agent_loop import parked_turn
from app.agents.agent_loop.artifact_store import build_artifact_store
from app.agents.agent_loop.cancellation_transport import with_cancellation
from app.agents.agent_loop.direct_transport import build_direct_transport
from app.agents.agent_loop.domain_agents import (
    plan_domain_agents,
    register_domain_agents,
)
from app.agents.agent_loop.entity_search_tool import (
    ENTITY_SEARCH_OFFERED_STATE_KEY,
    NAMED_ENTITIES_ENABLED_STATE_KEY,
    SEARCH_BY_ENTITIES_TOOL_NAME,
    entity_search_tool_offered,
)
from app.agents.agent_loop.history_window import (
    HISTORY_SHARE,
    attachment_stub,
    earlier_attachment_chars,
    exchange_tokens,
    omitted_note,
    plan_history,
    reduce_exchange,
    share_tokens,
    split_exchanges,
)
from app.agents.agent_loop.hooks import (
    CitationCollector,
    RepeatedReadGuard,
    ToolErrorTracker,
    artifact_context_reminder,
    ask_in_text_recovery,
    ask_user_question_gate,
    ask_user_question_sse,
    attachment_rehydration,
    citation_tracking,
    code_graph_unlock_after_tools,
    code_graph_unlock_on_turn,
    completion_gate,
    progressive_entity_tools,
    # repo_instructions_after_tools,
    # repo_instructions_on_turn,
    resolve_attachments_for_goal,
    resolve_history_attachments,
    result_accumulation,
    retrieval_context_emission,
    retry_with_status,
    seed_visible_tools_from_history,
    selected_nodes_prompt,
    shape_image_injection,
    stash_tool_call_metadata,
    sync_visible_tools_for_prompt,
)
from app.agents.agent_loop.hooks.attachment_resolver import default_message_budget
from app.agents.agent_loop.hooks.progressive_tools import (
    ENTITY_TOOL_NAMES,
    PROGRESSIVE_TOOL_NAMES,
    entity_tools_used_in_history,
)
from app.agents.agent_loop.image_guard import with_image_cap
from app.agents.agent_loop.langchain_transport import LangChainTransport
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
from app.agents.agent_loop.loops.plan_execute import (
    PLANNING_TOOL_NAMES,
    register_planning_tools,
)
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.agent_loop.openapi_tool_loader import OpenAPIToolProvider
from app.agents.agent_loop.output_cap_transport import (
    max_turn_output_chars,
    with_output_cap,
)
from app.agents.agent_loop.parked_turn import ASK_USER_QUESTION_RESUME_PREFIX
from app.agents.agent_loop.period_summary import period_summary_hint
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.prompt_cache import cache_strategy_for
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
from app.agents.agent_loop.tool_approvals import (
    WAITING_PREFIX,
    approval_gate,
    begin_resume,
    hold_history,
    resume_parked_call,
)
from app.agents.agent_loop.tool_approvals import (
    is_resume_query as is_tool_approval_resume_query,
)
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer
from app.agents.agent_loop.usage_transport import with_usage_reporting
from app.agents.mcp.service import is_mcp_enabled
from app.services.featureflag.platform_settings import (
    is_named_entity_extraction_enabled,
    is_skills_enabled,
)
from app.utils.citations import rewrite_request_refs
from app.utils.image_policy import resolve_image_policy
from app.utils.render_budget import fetch_result_cap


def _register_final_answer_if_enabled(tool_registry: "ToolRegistry") -> None:  # noqa: F821
    """Register FinalAnswerTool when PIPESHUB_ENABLE_FINAL_ANSWER is set."""
    from app.agent_loop_lib.tools.builtin.planning.final_answer import (
        FinalAnswerTool,
        final_answer_enabled,
    )
    if final_answer_enabled():
        tool_registry.register_tool(FinalAnswerTool())
from app.modules.agents.qna.tool_system import code_execution_enabled  # noqa: E402
from app.modules.agents.qna.turn_outcome import (  # noqa: E402
    FAILED,
    STOPPED,
    UNANSWERED_ANSWER,
    replayed_answer,
)

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

# Code structure is cross-cutting: the exploring agent is where code questions
# land, and routing them back through the parent costs a turn and loses the
# child's context. The full codegraph set ships as shared so a search that
# unlocks the toolset can walk edges and read symbols without another
# delegation.
#
# SHARED, never claimed. `plan_domain_agents` claiming is exclusive, so listing
# these on a definition would take them OFF the parent -- flipping
# `surfaces.code_graph` to False and deleting the tool's own entry from the
# parent's source list. Shared names never enter `claimed`, which is the same
# mechanism `_DOMAIN_SHARED_NAV_TOOL_NAMES` relies on.
_DOMAIN_SHARED_CODE_TOOL_NAMES: frozenset[str] = frozenset({
    "codegraph__query_code_graph",
    "codegraph__get_neighbour",
    "codegraph__read_code",
    "codegraph__find_symbol_path",
    "codegraph__search_code",
    "codegraph__outline",
    "codegraph__impact",
    "codegraph__entry_points",
})
# Tools a deployment switch adds to the knowledge tools. Not counted toward
# the lazy-disclosure threshold, so turning one on never hides web search or
# connector tools behind fetch_tools for a request that was eager without it.
_SWITCHED_TOOL_NAMES: frozenset[str] = frozenset({SEARCH_BY_ENTITIES_TOOL_NAME, TIMELINE_TOOL_NAME})

# Matches nodes.py's ReAct/planner loop cap (`MAX_ITERATIONS` — see
# tool_system.py / react_agent_node's `recursion_limit`); kept as one named
# constant here rather than a magic number in `create()`.
_MAX_TURNS = 15
# Deep research searches, reads and cross-checks before answering; its web budget bounds the cost.
_DEEP_RESEARCH_MAX_TURNS = 30

#: Traversal results L3 keeps past its turn window. A walk spans more turns
#: than the window holds, and each hop is the address the next one needs —
#: clearing an earlier hop strands the walk. Measured on the eval fixture
#: (A-8-result-size): a one-hop walk is ~3KB median / 7KB p90, a two-hop walk
#: ~15KB / 22KB, a symbol read under 1KB. A run of two-hop walks is the costly
#: case; L4-L7 still compact by budget, which bounds it. `query_code_graph` is
#: deliberately absent — its listings are re-callable, not something to carry.
_CODE_TRAVERSAL_TOOLS = frozenset({
    "codegraph__get_neighbour",
    "codegraph__read_code",
    "codegraph__find_symbol_path",
    # A walk too, grouped by file; and an outline is a few hundred bytes.
    "codegraph__impact",
    "codegraph__outline",
})


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
_AUTO_COMPACT_TAIL_RATIOS = (0.70, 0.40)

#: Tool results no context layer summarises or shortens (context/protection.py).
#: Plans are what a plan-execute run follows. Web search/fetch results carry
#: Citation IDs (`url/Citation ID: ...`) and fetch_full_record results carry
#: the [refN] markers AnswerFinalizer builds citations from: both are readable
#: only from the result itself, so shortening one makes every citation the
#: model already saw unciteable. Artifact compaction (L2) may still replace an
#: old one with its retrievable artifact reference.
_PROTECTED_TOOLS = frozenset({
    "create_plan", "critique_plan",
    "dynamic__web_search", "dynamic__fetch_url",
    FETCH_RECORD_TOOL_NAME,
})


def _chat_context_profile(
    context: "AgentContext", transport_registry: Any, model_name: str,  # noqa: ANN401
) -> ContextProfile:
    """The chat agent's PRE_MODEL profile for `build_context_pipeline`."""
    # Fetch sized by characters gets a cap equal to its own budget, so the
    # shared 64K cap never cuts a fetch in the middle.
    fetch_cap = fetch_result_cap(context.context_length)
    return ContextProfile(
        protected_tools=_PROTECTED_TOOLS,
        clearing_kept_tools=_CODE_TRAVERSAL_TOOLS,
        summarizer=(
            make_llm_summarizer(transport_registry, "langchain", model_name)
            if transport_registry is not None else None
        ),
        image_injectors=(shape_image_injection(context),),
        tool_caps={FETCH_RECORD_TOOL_NAME: fetch_cap} if fetch_cap else {},
        artifact_keep_last_n_turns=2,
        auto_compact_tail_ratios=_AUTO_COMPACT_TAIL_RATIOS,
    )


def _composed_agents_enabled() -> bool:
    """Kill-switch for domain-agent composition (see `domain_agents.py`) —
    customer-facing setting, exists so a deployment can fall back to the
    flat all-tools agent without a code change."""
    return os.getenv("PIPESHUB_USE_COMPOSED_AGENTS", "true").strip().lower() == "true"


def _initial_entity_tool_grant(tool_names: list[str], context: "AgentContext") -> list[str]:
    """Entity tools are hidden when no entity store is wired (they could only
    fail). ``find_records_by_entity`` needs an entityId, so it starts hidden
    until ``search_entities`` runs (``hooks/progressive_tools.py``) — unless an
    earlier turn already used an entity tool and its ids are in the history."""
    if not context.tool_state.get("entity_vector_store"):
        return [n for n in tool_names if n not in ENTITY_TOOL_NAMES]
    if entity_tools_used_in_history(context.previous_conversations):
        return tool_names
    return [n for n in tool_names if n not in PROGRESSIVE_TOOL_NAMES]


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
        _timer = context.tool_state.get("_stage_timer")

        def _mark(stage: str) -> None:
            if _timer is not None:
                _timer.mark(stage)

        opik_active = resolve_opik_gate(True)
        opik_project_name = os.getenv("OPIK_PROJECT_NAME")
        # Enforced again at the wire because the count admitted at the source
        # was decided for whichever model owned that tool state -- a sub-agent
        # on a smaller model shares it. See `image_guard`.
        image_cap = resolve_image_policy(
            provider=context.llm_provider, is_multimodal=context.is_multimodal_llm,
        ).max_images_per_request

        output_cap = max_turn_output_chars()
        configured_window = context.configured_context_window
        cache_settings = PromptCacheSettings.from_env()
        cache_scope = CacheScope(context.org_id, context.conversation_id)

        def _langchain_transport() -> LangChainTransport:
            return LangChainTransport(
                llm, model_name=model_name, opik_project_name=opik_project_name, model_key=model_key,
                max_images_per_request=image_cap,
                # Stop Generation (Phase 3b): per-chunk cancellation
                # check inside `stream()`. `None` for requests that
                # never registered a `runId` — the check is then a no-op.
                cancellation_token=context.cancellation_token,
                cache_strategy=cache_strategy_for(
                    llm, model_name=model_name, settings=cache_settings, scope=cache_scope,
                ),
            )

        # Opt-in: every agent of this request resolves its model from this
        # registry, so its transports are where each call's usage is counted.
        usage_sink = context.usage_ledger.add if context.include_retrieval_context else None
        transport_registry = TransportRegistry()
        transport_registry.register(
            "langchain",
            with_usage_reporting(traced_transport_factory(
                lambda: with_output_cap(_langchain_transport(), output_cap, context_window=configured_window),
                opik_active=opik_active,
                project_name=opik_project_name,
            ), usage_sink),
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
                cache_settings=cache_settings, cache_scope=cache_scope,
            )
            if direct is not None:
                # The direct SDK transports have no image cap and no
                # `CancellationToken` wiring of their own -- they live in
                # `agent_loop_lib` and know nothing about PipesHub's
                # per-provider image policy or Stop Generation -- so both
                # nets the LangChain arm applies inline (`max_images_per_
                # request`, `cancellation_token=` above) are wrapped around
                # them here instead. `with_cancellation` is a no-op when
                # `context.cancellation_token` is `None` (no `runId`
                # registered for this request).
                return with_cancellation(
                    with_output_cap(with_image_cap(direct, image_cap), output_cap, context_window=configured_window),
                    context.cancellation_token,
                )
            return with_output_cap(_langchain_transport(), output_cap, context_window=configured_window)

        transport_registry.register(
            DIRECT_TRANSPORT,
            with_usage_reporting(traced_transport_factory(
                _direct_or_langchain,
                opik_active=opik_active,
                project_name=opik_project_name,
            ), usage_sink),
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
        _mark("f:transports")
        context.tool_state[NAMED_ENTITIES_ENABLED_STATE_KEY] = await is_named_entity_extraction_enabled(
            context.tool_state.get("config_service"),
        )
        context.tool_state[ENTITY_SEARCH_OFFERED_STATE_KEY] = entity_search_tool_offered(context.tool_state)
        tool_registry = await PipesHubToolLoader().load(
            context, skip_apps=skip_apps,
        )
        _mark("f:tool_load")
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
        await OpenAPIToolProvider().load_into(tool_registry, context)
        # Registered unconditionally (not just when lazy disclosure ends up
        # active — see `register_lazy_tool_meta_tools`'s docstring):
        # `search_tools` provides auth-aware global discovery in eager mode
        # too. Every tool-name list assembled below has `META_TOOL_NAMES`
        # appended so all three are candidates for the grant, but
        # `list_toolsets`/`fetch_tools` are pruned back out below once
        # `tool_disclosure` is actually decided (see the `tool_disclosure
        # != "lazy"` prune near `top_level_lazy_tools`) — under eager
        # disclosure every schema is already bound, so they have nothing to
        # reveal and are not worth two extra bound schemas every turn.
        _mark("f:mcp_load")
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
            _mark("f:pre_sandbox")
            try:
                sandbox_manager = await build_coding_sandbox_manager(
                    allow_network=network_enabled, ctx=context,
                )
            except SandboxUnavailableError as exc:
                # Fail closed but keep the chat: no SANDBOX_MODE (or a typo)
                # means no code execution this turn, not an in-process
                # fallback and not a 500.
                code_exec_enabled = False
                logger.warning(
                    "PipesHubAgentFactory.create: coding-sandbox tools NOT registered "
                    "(org_id=%s conversation_id=%s): %s",
                    context.org_id, context.conversation_id, exc,
                )
        else:
            logger.info(
                "PipesHubAgentFactory.create: code execution disabled — coding-sandbox tools "
                "(run_code/install_packages/read_sandbox_file) will NOT be available this turn"
            )
        if sandbox_manager is not None:
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

        # Skills subsystem, gated by two layers that must BOTH be true:
        # `skills_enabled()` is the deployment-level env kill-switch
        # (default ON); `is_skills_enabled()` is the org-level `ENABLE_SKILLS`
        # platform feature flag toggled from Labs (also default ON — see
        # skills_wiring.py's module docstring for the full rollout/ordering
        # rationale). Built and its tools registered BEFORE
        # `plan_domain_agents()` runs below so `skill_search`/`load_skill`/
        # `skill_manage`/... land in that call's registered-tool snapshot and
        # fall into the residual (never domain-claimed) top-level grant.
        skill_manager = None
        if skills_enabled() and await is_skills_enabled(context.config_service):
            _mark("f:sandbox")
            skill_manager = await build_skill_manager(context, transport_registry)
            if skill_manager is not None:
                register_skill_tools(tool_registry, skill_manager)
                logger.debug(
                    "PipesHubAgentFactory.create: skills enabled — %d skill(s) in catalog "
                    "(org_id=%s)", len(skill_manager.catalog_snapshot()), context.org_id,
                )

        _mark("f:sandbox+skills")
        artifact_store = build_artifact_store(context)
        hooks = self._build_hooks(
            context, sandbox_manager, allow_network=network_enabled,
            artifact_store=artifact_store, tool_registry=tool_registry,
            transport_registry=transport_registry, model_name=model_name,
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
        _mark("f:hooks")
        composition_plan = plan_domain_agents(tool_registry) if _composed_agents_enabled() else None
        _mark("f:compose_plan")
        composed_tool_names = (
            composition_plan.top_level_names if composition_plan is not None else tool_registry.names()
        )

        resume_answers = query if is_ask_user_question_resume_query(query) else None
        # Typing instead of tapping answers the open card, as when a chat asks in text. A message
        # that carries files answers it too: every later reader of the history counts it so.
        typed_reply = resume_answers is None and is_open_card_reply(context.previous_conversations, query)
        if typed_reply:
            resume_answers = query
        # An answer to a tool approval card carries on the turn that stopped on it.
        approval_resume = bool(context.previous_conversations) and begin_resume(context, query)
        goal_query = (
            last_real_user_query(
                context.previous_conversations, query, answers="" if typed_reply else resume_answers,
            )
            if resume_answers or approval_resume
            else query
        )
        if resume_answers:
            context.tool_state["ask_user_question_resume"] = resume_answers

        loop, goal, clarifying_questions, mode = await select_loop_and_goal(
            chat_mode=chat_mode,
            query=goal_query,
            llm=llm,
            context=context,
            tool_names=composed_tool_names,
            sandbox_has_network=network_enabled,
            opik_active=opik_active,
            opik_project_name=opik_project_name,
            transport_registry=transport_registry,
            resume=bool(resume_answers or approval_resume),
        )

        _mark("f:select_loop_and_goal")
        if resume_answers:
            # The card is answered: no new clarification, and this message's files still count.
            clarifying_questions = []
        # Stash model_name on context so ensure_fetch_full_record_available()
        # (called from attachment_resolver outside _build_hooks) and the
        # fetch-tool block cap can both reach it without the TransportRegistry.
        context.model_name = model_name

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
        # here so all three are CANDIDATES for the grant regardless of
        # `tool_disclosure` (`tool_schemas_for_turn` binds exactly
        # `spec.tool_names`, so a name has to be in this list to ever be
        # bound at all). `list_toolsets`/`fetch_tools` get pruned back out
        # below once `tool_disclosure` is actually decided, if it's eager —
        # see the prune near `top_level_lazy_tools`. EXCEPT deep mode's
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

        _mark("f:attachments")
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
            # Stop Generation (Phase 3a): `None` for callers that never
            # registered a `runId` (background/test runs) — `Agent.__init__`
            # only installs the `check_not_cancelled` PRE_TURN guard (and
            # `step()`'s per-tool-call check) when this is set, so those
            # callers are unaffected.
            cancellation_token=context.cancellation_token,
        )
        if skill_manager is not None:
            # `hooks` here is the SAME HookRegistry instance now held by
            # `runtime.hooks` — appending the learning-loop middleware post
            # construction is equivalent to having registered it earlier;
            # it just needs `runtime` itself for `run_child()`, which
            # doesn't exist until this line.
            register_skill_learning(
                hooks, skill_manager, runtime, provider=_transport_provider(), model_name=model_name,
                context_window=configured_window,
            )

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
                uncounted_names=_SWITCHED_TOOL_NAMES,
            )
            composed_names = register_domain_agents(
                composition_plan, tool_registry, runtime, context,
                provider=_transport_provider(), model_name=model_name, context_window=configured_window,
                lazy_tools=domain_lazy_tools,
                shared_tool_names=(
                    (DOMAIN_SHARED_SKILL_TOOL_NAMES if skill_manager is not None else frozenset())
                    | _DOMAIN_SHARED_NAV_TOOL_NAMES
                ),
                code_tool_names=_DOMAIN_SHARED_CODE_TOOL_NAMES,
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
                    default_tool_names=_initial_entity_tool_grant(composed_names, context),
                    context=context, context_window=configured_window,
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
                default_tool_names=_initial_entity_tool_grant(
                    [n for n in tool_registry.names() if n not in COORDINATION_TOOL_NAMES],
                    context,
                ),
                context=context, context_window=configured_window,
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
            uncounted_names=_SWITCHED_TOOL_NAMES,
        )
        tool_names, tool_disclosure = top_level_lazy_tools(tool_registry, tool_names)
        # `list_toolsets`/`fetch_tools` were appended unconditionally above
        # (before this decision existed) so they'd be available to append
        # again — a no-op — if disclosure went lazy. If it didn't, they have
        # nothing to reveal (see `_render_toolset_overview`'s prompt-side
        # gating in `agent_loop_lib/agent/prompt.py`, which now stays silent
        # about them in eager mode too) but were still being bound as two
        # extra schemas on every turn regardless. Prune them from the final
        # eager-mode grant; `search_tools` stays — it's the one meta-tool
        # that does real work in eager mode (auth-aware global discovery,
        # including now the MCP `mcp_unavailable` hits from step 5).
        if tool_disclosure != "lazy":
            tool_names = [n for n in tool_names if n not in ("list_toolsets", "fetch_tools")]
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

        tool_names = _initial_entity_tool_grant(tool_names, context)

        spec = AgentSpec(
            name="pipeshub-agent",
            system_prompt=prompt_builder,
            tool_names=tool_names,
            tool_disclosure=tool_disclosure,
            pinned_toolsets=pinned_toolsets,
            model=ModelSpec(provider=_transport_provider(), model=model_name, context_window=configured_window),
            loop=loop,
            max_turns=_DEEP_RESEARCH_MAX_TURNS if context.deep_research else _MAX_TURNS,
        )
        # Let `hooks/citations.py` grant `dynamic_fetch_full_record` to this
        # spec too once any nested search populates it — see
        # `AgentContext.root_agent_spec`'s docstring.
        context.root_agent_spec = spec

        # Stop Generation (Phase 3a): `context.run_id`, when already set by
        # `stream_bridge.py`/`bridge.py` from the client-supplied `runId`,
        # becomes the root run's OWN `RunContext.run_id` — so every AG-UI
        # frame this run emits already carries the id the client used to
        # register the cancel-able run, with no separate mapping to keep
        # in sync. `None` (callers that never set one) falls through to
        # `Agent.__init__`'s own `RunContext` default (a fresh uuid4).
        agent = Agent(spec, runtime, session_id=session_id, run_id=context.run_id)
        # `AGUIFormatter` (direct EventSink writers — see protocol/formatter.py)
        # has no Agent/RunContext reference of its own; stash the top-level
        # run_id here, the one place both `context` and the freshly-built
        # `agent` are in scope.
        context.run_id = agent.run_ctx.run_id

        _mark("f:runtime+compose")
        message_budget = _message_budget_tokens(spec, runtime, goal)
        if not clarifying_questions:
            await _attach_current_files(goal, context, runtime, message_budget)
            _mark("f:current_attachments")

        if context.previous_conversations:
            await self._seed_conversation_history(
                agent, context.previous_conversations, context, message_budget_tokens=message_budget,
            )
            _mark("f:seed_history")
            hold_history(context, agent)

        if resume_answers:
            # The turn that asked, as the model had it (its history copy has only result summaries).
            await parked_turn.restore_question(context, agent.context)
            # A resumed run adds no goal message (`_skip_start`), so files sent with the reply ride on it.
            await inject_ask_user_question_resume(
                agent, resume_answers, typed=typed_reply, files=goal.attachment_text if typed_reply else "",
            )

        return agent, runtime, goal, clarifying_questions

    @staticmethod
    def _build_event_emitter(context: "AgentContext") -> Any:  # noqa: ANN401
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
            collector = TranscriptCollector(ref_to_url=context.citation_ref_to_url)
            context.transcript_collector = collector
            return CompositeEmitter([emitter, collector])
        return SSEEventEmitter(context.event_sink)

    @staticmethod
    def _build_hooks(
        context: "AgentContext", sandbox_manager: Any = None, *, allow_network: bool = False,  # noqa: ANN401
        artifact_store: Any = None, tool_registry: Any = None,  # noqa: ANN401
        transport_registry: Any = None, model_name: str = "",  # noqa: ANN401
    ) -> HookRegistry:
        """Phase 5's hooks, wired onto a fresh `HookRegistry` (never a
        shared/global one — see that phase's hook docstrings for why
        per-request instances matter for `ToolErrorTracker`/`CitationCollector`
        state isolation across concurrent requests)."""
        hooks = HookRegistry()

        # --- POST_TOOL_USE: artifact registration (Phase 1 of two-phase compaction) ---
        # Large tool results (>2K tokens) are persisted in the artifact store
        # and annotated with ToolMessageMeta.  Full content stays for the
        # current turn; PRE_MODEL shapers below compact it on later turns.
        if artifact_store is not None:
            def _resolve_schema(tool_name: str):  # noqa: ANN202
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

        # --- PRE_MODEL context-shaping pipeline ---
        # One ordered definition shared with ControlPlane.start()
        # (context/pipeline.py); this profile is what chat changes. L0 puts
        # image attachments on a copy of the current request (no-op when
        # context.attachment_image_blocks is empty — deferred check).
        for layer in build_context_pipeline(_chat_context_profile(context, transport_registry, model_name)):
            hooks.on(HookEvent.PRE_MODEL).use(layer)

        # LLM transport retry (429/5xx/network) with SSE "retrying..." status
        # feedback — see retry_with_status.py's docstring for why this lives
        # here instead of agent_loop_lib's own RetryHook (which ControlPlane
        # wires by default but this adapter path never goes through).
        hooks.wrapper(HookEvent.PRE_MODEL_CALL).use(retry_with_status(context.event_sink))

        error_tracker = ToolErrorTracker()
        hooks.on(HookEvent.PRE_TOOL_USE).use(error_tracker.pre_tool_use)
        hooks.on(HookEvent.POST_TOOL_USE).use(error_tracker.post_tool_use)
        # Successful repeats aren't failures: without this an identical MCP read can fill every turn.
        repeated_reads = RepeatedReadGuard(context)
        hooks.on(HookEvent.PRE_TOOL_USE).use(repeated_reads.pre_tool_use)
        hooks.on(HookEvent.POST_TOOL_USE).use(repeated_reads.post_tool_use)

        collector = CitationCollector(context)
        hooks.on(HookEvent.POST_TOOL_USE).use(citation_tracking(context, collector))
        hooks.on(HookEvent.POST_TOOL_USE).use(progressive_entity_tools(context))
        hooks.on(HookEvent.POST_TOOL_USE).use(code_graph_unlock_after_tools(context))
        # hooks.on(HookEvent.POST_TOOL_USE).use(repo_instructions_after_tools(context))

        hooks.on(HookEvent.PRE_TOOL_USE).use(stash_tool_call_metadata)
        hooks.on(HookEvent.POST_TOOL_USE).use(result_accumulation(context))
        if context.include_retrieval_context:
            # Opt-in: diffs the retrieval accumulators after every tool call
            # and streams only what is new. Off by default, so a normal
            # request pays nothing for it.
            hooks.on(HookEvent.POST_TOOL_USE).use(retrieval_context_emission(context))

        hooks.on(HookEvent.PRE_TOOL_USE).use(ask_user_question_gate(context))
        hooks.on(HookEvent.POST_TOOL_USE).use(ask_user_question_sse(context))

        # Answers an approval card: settles the stopped call before the loop starts.
        hooks.on(HookEvent.PRE_AGENT).use(resume_parked_call(context))
        hooks.on(HookEvent.PRE_AGENT).use(selected_nodes_prompt(context))
        hooks.on(HookEvent.PRE_AGENT).use(period_summary_hint(context))
        hooks.on(HookEvent.PRE_TURN).use(attachment_rehydration(context))
        hooks.on(HookEvent.PRE_TURN).use(artifact_context_reminder(context))
        hooks.on(HookEvent.PRE_TURN).use(seed_visible_tools_from_history(context))
        hooks.on(HookEvent.PRE_TURN).use(code_graph_unlock_on_turn(context))
        # hooks.on(HookEvent.PRE_TURN).use(repo_instructions_on_turn(context))
        # After visibility mutations — prompt builder reads bound_tool_names.
        hooks.on(HookEvent.PRE_TURN).use(sync_visible_tools_for_prompt(context))

        # Recovers from empty model responses (no text, no tool calls).
        hooks.on(HookEvent.POST_MODEL).use(completion_gate(context))
        # Last: a reply the gates above already sent back is not checked for a text question.
        hooks.on(HookEvent.POST_MODEL).use(ask_in_text_recovery(context))

        # This adapter path builds its own HookRegistry directly (never
        # goes through ControlPlane.start()), so the coding_sandbox_safety
        # auto-add there does not apply — it, and the artifact/package-policy
        # bridge hooks, must be wired explicitly here.
        if sandbox_manager is not None:
            register_coding_sandbox_hooks(
                hooks, context, sandbox_manager,
                allow_network=allow_network, artifact_store=artifact_store,
            )

        # Per-tool approvals. Last, so no later middleware can still deny a call it leaves
        # waiting for a person (the card would show for a call that was refused anyway).
        hooks.on(HookEvent.PRE_TOOL_USE).use(approval_gate(context))

        return hooks

    @staticmethod
    async def _seed_conversation_history(
        agent: Agent,
        previous_conversations: list[dict[str, Any]],
        context: "AgentContext",
        *,
        message_budget_tokens: int | None = None,
    ) -> int:
        """Loads prior turns into the agent's `ContextManager` so the model
        sees them as ordinary conversation history on turn 0 — the
        agent-loop equivalent of `nodes.py::_build_conversation_messages`.

        What is replayed fits the history share of `message_budget_tokens`
        (`history_window.py`): the newest exchanges whole, older ones reduced
        to question, answer opening and tool names, the oldest left out with a
        note saying how many. Document attachments of exchanges kept whole are
        rendered from blob storage within the history's attachment share;
        others are named with how to read them. ``virtual_record_id_to_result``
        and ``citation_ref_mapper`` are populated as a side-effect so
        ``dynamic_fetch_full_record`` remains available.

        `message_budget_tokens` is the budget the run's model calls will have
        (the factory passes it); without it the model's window alone sizes it.
        Returns the estimated tokens replayed.
        """
        from app.agent_loop_lib.context.manager import ContextManager
        from app.agent_loop_lib.core.tokens import count_tokens
        from app.utils.render_budget import RenderBudget

        if message_budget_tokens is None:
            message_budget_tokens = default_message_budget(context)
        history_tokens = share_tokens(message_budget_tokens, HISTORY_SHARE)
        exchanges = split_exchanges(previous_conversations, _opens_an_exchange)

        attachments = _HistoryAttachments(context, RenderBudget(max_chars=earlier_attachment_chars(history_tokens)))
        # Newest first, and only as far back as exchanges could still be kept
        # whole: when the share runs out it is the oldest files that are named,
        # and an exchange that will be reduced costs no blob read or image slot.
        spent = 0
        for index in range(len(exchanges) - 1, -1, -1):
            spent += exchange_tokens(exchanges[index])
            if spent > history_tokens:
                break
            await attachments.render(index, exchanges[index])
            spent += attachments.tokens(index)
        plan = plan_history(exchanges, history_tokens, [attachments.tokens(i) for i in range(len(exchanges))])

        ctx = ContextManager()
        history: list[Message] = []
        if plan.omitted:
            history.append(UserMessage(content=omitted_note(plan.omitted), injected=True))
        tool_results_at: dict[str, int] = {}
        after_answer = False
        unanswered = 0
        for index in plan.kept:
            whole = index in plan.whole
            for position, turn in enumerate(exchanges[index] if whole else reduce_exchange(exchanges[index])):
                if turn.get("role") == "user_query" and is_tool_approval_resume_query(str(turn.get("content") or "")):
                    _drop_closing_text(history)
                    after_answer = True
                    continue
                messages = _convert_conversation_turn(turn)
                if messages and _opens_a_new_question(turn) and _ends_on_open_question(history):
                    history.append(AssistantMessage(content=UNANSWERED_ANSWER))
                    unanswered += 1
                if turn.get("role") == "user_query" and messages and turn.get("attachments"):
                    attachments.attach(messages[0], index, turn, whole=whole and position == 0)
                _append_turn(history, messages, tool_results_at, fold=after_answer and turn.get("role") == "bot_response")
                after_answer = after_answer and turn.get("role") != "bot_response"

        # The run adds the current request next.
        if _ends_on_open_question(history):
            history.append(AssistantMessage(content=UNANSWERED_ANSWER))
            unanswered += 1

        # After the fold: folding an approval answer relies on its repeated id.
        dedupe_tool_call_ids(history, set())
        for message in history:
            await ctx.add(message)
        agent.seed_context(ctx)
        replayed = count_tokens(history)
        statuses = [turn.get("status") for turn in previous_conversations if turn.get("role") == "bot_response"]
        logger.info(
            "history_seeded run_id=%s conversation_id=%s turns=%d exchanges=%d whole=%d reduced=%d omitted=%d "
            "messages=%d tokens=%d share=%d failed=%d stopped=%d unanswered=%d "
            "attachment_chars=%d attachments_truncated=%d attachments_named=%d",
            context.run_id, context.conversation_id, len(previous_conversations), len(exchanges),
            len(plan.whole), plan.reduced, plan.omitted, len(history), replayed, history_tokens,
            statuses.count(FAILED), statuses.count(STOPPED), unanswered,
            attachments.chars_rendered(plan.whole), attachments.truncated(plan.whole),
            attachments.named,
        )
        return replayed


def _message_budget_tokens(spec: AgentSpec, runtime: AgentRuntime, goal: Any) -> int:  # noqa: ANN401
    """The message budget the run's model calls will have, as far as it is
    known before the run: the model's window less the system prompt and the
    granted tools' schemas (`Agent.step` derives it the same way per call).
    Every granted tool is counted, so a lazily disclosed set errs small."""
    from app.agent_loop_lib.agent.prompt import build_system_prompt
    from app.agent_loop_lib.context.base import ContextBudget
    from app.agent_loop_lib.core.tokens import count_schema_tokens, count_text_tokens

    registry = runtime.tool_registry
    schemas = registry.schemas(spec.tool_names or None) if registry is not None else []
    prompt_tokens = count_text_tokens(build_system_prompt(spec, runtime, goal)) + count_schema_tokens(schemas)
    return ContextBudget.for_model(
        spec.model.model, context_window=spec.model.context_window, reserved_prompt_tokens=prompt_tokens,
    ).effective_max_tokens


async def _attach_current_files(
    goal: Any,  # noqa: ANN401
    context: "AgentContext",
    runtime: AgentRuntime,
    message_budget_tokens: int,
) -> None:
    """Renders this turn's attachments into the current request within their
    share of the budget. One cut short names `knowledgegraph__fetch_record`,
    so the tool is granted for the run."""
    from app.agent_loop_lib.core.tokens import count_text_tokens
    from app.agents.agent_loop.history_window import current_attachment_chars

    words = goal.user_message if goal.user_message is not None else goal.description
    outcomes: list[Any] = []
    attachment_text, image_blocks = await resolve_attachments_for_goal(
        context, logger,
        max_chars=current_attachment_chars(message_budget_tokens, count_text_tokens(words)),
        outcomes=outcomes,
    )
    if attachment_text:
        goal.attachment_text = (
            f"{attachment_text}"
            "\n\nWhen citing facts from attached documents, use the "
            "Citation ID as a markdown link, e.g. [source](ref2). "
            "Do NOT use record URLs or block indices as citation links."
        )
        goal.description = f"{goal.description}\n\n{goal.attachment_text}"
    if image_blocks:
        context.attachment_image_blocks = image_blocks
    if any(not outcome.complete for outcome in outcomes):
        from app.agents.agent_loop.hooks.citations import (
            ensure_fetch_full_record_available,
        )

        ensure_fetch_full_record_available(context, registry=runtime.tool_registry)


class _HistoryAttachments:
    """Earlier turns' attachments, rendered once per seeding within one
    character budget and put on the replayed question they came with."""

    def __init__(self, context: "AgentContext", render_budget: Any) -> None:  # noqa: ANN401
        state = context.tool_state
        if state.get("citation_ref_mapper") is None:
            from app.utils.chat_helpers import CitationRefMapper

            state["citation_ref_mapper"] = CitationRefMapper()
        vrmap = state.get("virtual_record_id_to_result")
        if not isinstance(vrmap, dict):
            vrmap = {}
        state["virtual_record_id_to_result"] = vrmap
        from app.utils.chat_helpers import ImageBudget

        state.setdefault("image_budget", ImageBudget())
        self._context = context
        self._budget = render_budget
        self._rendered: dict[int, tuple[str, list[dict[str, Any]], list[Any]]] = {}
        self.named = 0

    async def render(self, index: int, exchange: list[dict[str, Any]]) -> None:
        context = self._context
        question = exchange[0]
        attachments = question.get("attachments") or []
        if question.get("role") != "user_query" or not attachments or not context.blob_store:
            return
        from app.utils.image_admission import admission_from_state

        state = context.tool_state
        outcomes: list[Any] = []
        text, image_blocks = await resolve_history_attachments(
            attachments, context.blob_store, context.org_id, state["citation_ref_mapper"],
            state["virtual_record_id_to_result"],
            is_multimodal_llm=context.is_multimodal_llm,
            image_budget=state["image_budget"],
            image_admission=admission_from_state(state),
            user_id=context.user_id,
            graph_provider=context.graph_provider,
            is_service_account=context.is_service_account,
            render_budget=self._budget,
            outcomes=outcomes,
        )
        self._rendered[index] = (text, image_blocks, outcomes)

    def tokens(self, index: int) -> int:
        from app.agent_loop_lib.core.tokens import count_image_tokens, count_text_tokens
        from app.utils.chat_helpers import image_dict_to_part

        text, image_blocks, _ = self._rendered.get(index, ("", [], []))
        parts = [part for block in image_blocks if (part := image_dict_to_part(block)) is not None]
        return count_text_tokens(_attached_documents(text)) + sum(count_image_tokens(part) for part in parts)

    def chars_rendered(self, whole: frozenset[int]) -> int:
        return sum(len(self._rendered[i][0]) for i in whole if i in self._rendered)

    def truncated(self, whole: frozenset[int]) -> int:
        return sum(
            1 for i in whole if i in self._rendered for outcome in self._rendered[i][2] if not outcome.complete
        )

    def attach(self, message: UserMessage, index: int, turn: dict[str, Any], *, whole: bool) -> None:
        """Puts the rendered attachments of exchange `index` on its replayed
        question, or, for a reduced exchange, names them."""
        if not whole:
            names = [str(a.get("recordName") or "a file") for a in turn.get("attachments") or [] if isinstance(a, dict)]
            message.content = f"{message.content}\n\n{attachment_stub(names)}"
            self.named += len(names)
            return
        if index not in self._rendered:
            return
        text, image_blocks, _ = self._rendered[index]
        if text:
            message.content = f"{message.content}{_attached_documents(text)}"
        if image_blocks and self._context.is_multimodal_llm:
            _inject_images_into_message(message, image_blocks)
        elif image_blocks:
            names = [
                a.get("recordName", "image")
                for a in turn.get("attachments") or []
                if (a.get("mimeType") or "").lower().startswith("image/")
            ]
            message.content = f"{message.content}\n\n" + "\n".join(f"[Image attached: {n}]" for n in names)


def _attached_documents(text: str) -> str:
    if not text:
        return ""
    return (
        f"\n\nAttached documents:\n{text}"
        "\n\nWhen citing facts from these documents, use the "
        "Citation ID as a markdown link, e.g. [source](ref2). "
        "Do NOT use record URLs or block indices as citation links."
    )


def _opens_an_exchange(turn: dict[str, Any]) -> bool:
    """A question the person asked: not an answer to a card or an approval."""
    return _opens_a_new_question(turn) and not is_tool_approval_resume_query(str(turn.get("content") or ""))


def _inject_images_into_message(
    msg: UserMessage,
    image_blocks: list[dict[str, Any]],
) -> None:
    """Convert ``image_url`` dicts to ``ImagePart`` and set multipart content."""
    from app.agent_loop_lib.core.messages import TextPart
    from app.agents.agent_loop.hooks.attachment_resolver import (
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

_EMPTY_ANSWER_FALLBACK = "I wasn't able to generate a response. Please try rephrasing."


def is_ask_user_question_resume_query(text: str | None) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(ASK_USER_QUESTION_RESUME_PREFIX)


def _is_empty_fallback_assistant(msg: Message) -> bool:
    return (
        isinstance(msg, AssistantMessage)
        and not msg.tool_calls
        and (msg.text or "").strip() == _EMPTY_ANSWER_FALLBACK
    )


def _question_key(text: str) -> str:
    """Two askings of one question differ in case, spacing or the closing mark (as the card compares them)."""
    return re.sub(r"[\s?!.:]+$", "", re.sub(r"\s+", " ", text.lower())).strip()


_ANSWER_LINE = re.compile(r'^\s*\d+\.\s*"(.*)"\s*→', re.MULTILINE)


def _answered_questions(answers: str | None) -> set[str]:
    """The questions a `User selections:` reply answers: one `N. "question" → answer` line each."""
    return {_question_key(q) for q in _ANSWER_LINE.findall(answers or "")}


def _asked_questions(*values: Any) -> set[str]:  # noqa: ANN401
    """Question texts in an ask_user_question call's arguments or result, parsed or as JSON."""
    found: set[str] = set()
    stack = list(values)
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            question = value.get("question")
            if isinstance(question, str):
                found.add(_question_key(question))
            stack.extend(v for v in value.values() if isinstance(v, (dict, list)))
        elif isinstance(value, list):
            stack.extend(value)
    return found


def _turn_asked_questions(turn: dict[str, Any]) -> set[str] | None:
    """What a saved bot turn asked with ask_user_question; None when it asked nothing."""
    rows = [
        row for row in turn.get("tool_results") or []
        if isinstance(row, dict) and "ask_user_question" in str(row.get("tool_name") or "")
    ]
    if not rows:
        return None
    return _asked_questions(*(v for row in rows for v in (row.get("args"), row.get("result"))))


def _turn_showed_card(turn: Any) -> bool:  # noqa: ANN401
    """A saved bot turn that put a question card in front of the user; a failed call showed none."""
    return isinstance(turn, dict) and turn.get("role") == "bot_response" and any(
        isinstance(row, dict)
        and "ask_user_question" in str(row.get("tool_name") or "")
        and row.get("status") != "error"
        for row in turn.get("tool_results") or []
    )


def _is_card_reply(turns: list[dict[str, Any]], index: int) -> bool:
    """A user message that answers a card: tapped answers, or text typed while the card was open."""
    content = str(turns[index].get("content") or "")
    return is_ask_user_question_resume_query(content) or (index > 0 and _turn_showed_card(turns[index - 1]))


def is_open_card_reply(previous_conversations: list[dict[str, Any]] | None, query: str | None) -> bool:
    """Whether `query`, typed rather than tapped, answers the card the last reply ended on."""
    if not isinstance(query, str) or not query.strip():
        return False
    if is_ask_user_question_resume_query(query) or is_tool_approval_resume_query(query):
        return False
    return bool(previous_conversations) and _turn_showed_card(previous_conversations[-1])


def _request_cards(previous_conversations: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The cards shown since the person's last request: the one this run answers, and any before it."""
    turns = previous_conversations or []
    cards: list[dict[str, Any]] = []
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if turn.get("role") == "bot_response":
            if _turn_showed_card(turn):
                cards.append(turn)
        elif turn.get("role") == "user_query" and not _is_card_reply(turns, index):
            content = str(turn.get("content") or "")
            if not is_tool_approval_resume_query(content):
                break
    return cards


def cards_in_request(previous_conversations: list[dict[str, Any]] | None) -> int:
    """How many cards the person's current request has shown."""
    return len(_request_cards(previous_conversations))


def questions_asked_in_request(previous_conversations: list[dict[str, Any]] | None) -> set[str]:
    """The questions the current request's cards already put to the user (`_question_key` form).
    A later request may need the same kind of answer again (a second bug, a new project), so only
    the current request's questions are off limits, as a form's slots are scoped to its intent."""
    asked: set[str] = set()
    for turn in _request_cards(previous_conversations):
        asked |= _turn_asked_questions(turn) or set()
    return asked


def card_already_answered(previous_conversations: list[dict[str, Any]] | None, answers: str | None) -> bool:
    """Whether the card these `User selections:` answers are for has already had its reply, so
    running them would repeat that work: a second tab, or a stale one. A failed answer leaves no
    reply (errors are not in the history) and a stopped one did not finish, so either can be
    sent again."""
    if not is_ask_user_question_resume_query(answers):
        return False
    turns = previous_conversations or []
    answered = _answered_questions(answers)
    card_at: int | None = None
    latest_card: int | None = None
    for index, turn in enumerate(turns):
        if not _turn_showed_card(turn):
            continue
        latest_card = index
        if answered & (_turn_asked_questions(turn) or set()):
            card_at = index
    card_at = card_at if card_at is not None else latest_card
    if card_at is None:
        return False
    card_questions = _turn_asked_questions(turns[card_at]) or set()
    replied = False
    for index in range(card_at + 1, len(turns)):
        turn = turns[index]
        if turn.get("role") == "user_query":
            content = str(turn.get("content") or "")
            # Only this card's own answers count, or text typed while it was the open card: the
            # answers to another card, or a later request, leave it unanswered.
            if is_ask_user_question_resume_query(content):
                replied = bool(_answered_questions(content) & card_questions)
            elif not is_tool_approval_resume_query(content):
                replied = index == card_at + 1
        elif turn.get("role") == "bot_response" and replied and turn.get("status") != "stopped" and (
            str(turn.get("content") or "").strip() or _turn_showed_card(turn)
        ):
            return True
    return False


def last_real_user_query(
    previous_conversations: list[dict[str, Any]] | None,
    fallback: str,
    *,
    answers: str | None = None,
) -> str:
    """Original user goal when this request answers a card (ask_user_question or a tool approval).

    `answers` (an ask_user_question reply) picks the user text that opened the card it answers, not
    the latest chat: resuming from a later chat made the model redo that reply and the UI painted it
    onto the card. A card is matched by its question text, so an older card left open while a newer
    one was answered keeps its own goal; without a match the latest card is the one.
    """
    turns = previous_conversations or []
    if answers is not None:
        answered = _answered_questions(answers)
        latest_card: str | None = None
        matching_card: str | None = None
        last_user: str | None = None
        for index, turn in enumerate(turns):
            role = turn.get("role")
            content = str(turn.get("content") or "").strip()
            if role == "user_query":
                if content and not _is_card_reply(turns, index) and not is_tool_approval_resume_query(content):
                    last_user = content
            elif role == "bot_response" and last_user:
                asked = _turn_asked_questions(turn)
                if asked is None:
                    continue
                latest_card = last_user
                if answered & asked:
                    matching_card = last_user
        goal = matching_card or latest_card
        if goal:
            return goal
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if turn.get("role") != "user_query":
            continue
        content = str(turn.get("content") or "").strip()
        if content and not _is_card_reply(turns, index) and not is_tool_approval_resume_query(content):
            return content
    return fallback


def _find_ask_user_question_call(
    messages: list[Message],
    start: int,
    end: int,
    answered: set[str] | None = None,
) -> tuple[int | None, str | None]:
    """The last ask_user_question call in `messages[start:end]`; given `answered`, the last one
    that asked any of those questions."""
    results = {m.tool_call_id: m.text for m in messages if isinstance(m, ToolMessage)}
    keep_through: int | None = None
    tool_call_id: str | None = None
    for i in range(start, end):
        msg = messages[i]
        if not isinstance(msg, AssistantMessage) or not msg.tool_calls:
            continue
        for call in reversed(msg.tool_calls):
            if "ask_user_question" not in (call.name or ""):
                continue
            if answered and not answered & _asked_questions(call.arguments, results.get(call.id)):
                continue
            keep_through = i
            tool_call_id = call.id
            break
    return keep_through, tool_call_id


def _ends_on_open_question(history: list[Message]) -> bool:
    """Whether the last message is the person's, with no answer after it."""
    return bool(history) and isinstance(history[-1], UserMessage) and not history[-1].injected


def _opens_a_new_question(turn: dict[str, Any]) -> bool:
    """A user turn that asks something, as opposed to one that answers the card before it."""
    return turn.get("role") == "user_query" and not is_ask_user_question_resume_query(str(turn.get("content") or ""))


def _drop_closing_text(history: list[Message]) -> None:
    """A turn that stopped on an approval card closes with "Waiting for your approval…"; the
    answer carried that turn on from the call, so those words go (and, with them, two assistant
    messages in a row, which no transport merges)."""
    while history and isinstance(history[-1], AssistantMessage) and not history[-1].tool_calls:
        history.pop()


_SHARED_CALL_IDS = ("ask_user_question", "ask_user_question_resume")


def _append_turn(history: list[Message], messages: list[Message], tool_results_at: dict[str, int], *, fold: bool = False) -> None:
    """`fold`: this is the reply that answered an approval card. It has the stopped call again,
    settled, under the same id: its result replaces the waiting one in place and the repeat goes.
    One call, one result, and no id twice (providers refuse a repeated tool-call id). Nothing else
    is folded: some providers number their call ids per response, so a repeated id elsewhere can be
    a different call."""
    def _foldable(call_id: str | None) -> bool:
        if not fold or not call_id or call_id not in tool_results_at or call_id in _SHARED_CALL_IDS:
            return False
        earlier = history[tool_results_at[call_id]]
        return isinstance(earlier, ToolMessage) and isinstance(earlier.content, str) and earlier.content.startswith(WAITING_PREFIX)

    repeated = {m.tool_call_id for m in messages if isinstance(m, ToolMessage) and _foldable(m.tool_call_id)}
    for message in messages:
        kept = message
        if isinstance(message, ToolMessage) and message.tool_call_id in repeated:
            history[tool_results_at[message.tool_call_id]] = message
            continue
        if isinstance(message, AssistantMessage) and repeated and any(call.id in repeated for call in message.tool_calls or []):
            calls = [call for call in message.tool_calls if call.id not in repeated]
            if not calls and not message.text:
                continue
            kept = message.model_copy(update={"tool_calls": calls})
        if isinstance(kept, ToolMessage) and kept.tool_call_id:
            tool_results_at[kept.tool_call_id] = len(history)
        history.append(kept)


_NO_PREFERENCE = "[No preference]"


def _resume_result(answers: str, *, typed: bool, files: str = "") -> dict[str, str]:
    """The paused call's result. "next" is said plainly: without it a model may reply
    "Got it, I'll create it" and stop there."""
    if typed:
        result = {"status": "replied", "reply": answers}
        if files.strip():
            result["files"] = files.strip()
        attached = " They attached files with it, in \"files\"." if "files" in result else ""
        result["next"] = (
            f"The user typed this in the chat instead of choosing an option.{attached} If it answers your "
            "question, carry on with the request now. If it asks for something else, do that instead."
        )
        return result
    next_step = (
        "Carry on with the request using these answers: do the next step now instead of saying what you'll do. "
        "Ask any further question with internaltools__ask_user_question, not in your reply."
    )
    if _NO_PREFERENCE in answers:
        next_step += (
            f" Where the answer is {_NO_PREFERENCE}, the user skipped it: use your best guess "
            "and say in one line which you chose."
        )
    return {"status": "answered", "answers": answers, "next": next_step}


async def inject_ask_user_question_resume(
    agent: Agent, answers: str, *, typed: bool = False, files: str = "",
) -> None:
    """Bind the user's card answers to the parked ask_user_question tool call.

    Web workers cannot keep the first HTTP request blocked (no hil_store).
    The first turn stops after the terminal tool; this injects the answer
    as a ToolMessage so the next request continues that tool_use, not a
    new user goal.

    History replay already has the first request's tool result (the
    questions payload) plus the empty-answer fallback AssistantMessage.
    Appending a second ToolMessage after that text is invalid, so the
    questions payload is replaced by the answers and the fallback tail is
    dropped. A turn replays as one AssistantMessage carrying every call it
    made, so the results of the other calls stay right after it.

    A card left unanswered before later chat is still that older call.
    Continuing from the later turn made the model repeat that chat; the
    follow-up belongs under the card, so later turns stay out of this run.
    """
    ctx = agent.context
    if ctx is None:
        return
    messages = await ctx.messages()
    turn_start = 0
    for i, msg in enumerate(messages):
        if isinstance(msg, UserMessage):
            turn_start = i
    # The card is the one whose questions these answers name, wherever it is: an older card answered
    # after a newer one must not take the newer call's place. Typed text names none: it is the last card.
    answered = set() if typed else _answered_questions(answers)
    keep_through, tool_call_id = (
        _find_ask_user_question_call(messages, 0, len(messages), answered) if answered else (None, None)
    )
    if tool_call_id is None:
        keep_through, tool_call_id = _find_ask_user_question_call(
            messages, turn_start, len(messages),
        )
    if tool_call_id is None:
        keep_through, tool_call_id = _find_ask_user_question_call(
            messages, 0, turn_start,
        )
    if tool_call_id is None:
        trimmed = list(messages)
        while trimmed and _is_empty_fallback_assistant(trimmed[-1]):
            trimmed.pop()
        tool_call_id = "ask_user_question_resume"
        await ctx.clear()
        for msg in trimmed:
            await ctx.add(msg)
        await ctx.add(AssistantMessage(
            content=[],
            tool_calls=[ToolCall(
                id=tool_call_id,
                name="internaltools__ask_user_question",
                arguments={},
            )],
        ))
    else:
        # Calls made alongside the question ran; their results stay with them.
        alongside = {call.id for call in messages[keep_through].tool_calls} - {tool_call_id}
        tool_block_end = keep_through + 1
        while tool_block_end < len(messages) and isinstance(messages[tool_block_end], ToolMessage):
            tool_block_end += 1
        await ctx.clear()
        for msg in messages[: keep_through + 1]:
            await ctx.add(msg)
        for msg in messages[keep_through + 1 : tool_block_end]:
            if msg.tool_call_id in alongside:
                await ctx.add(msg)
    await ctx.add(ToolMessage(
        content=json.dumps(_resume_result(answers, typed=typed, files=files), ensure_ascii=False),
        tool_call_id=tool_call_id,
    ))


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
    content = replayed_answer(content, turn.get("status"))

    messages: list[Message] = []
    tool_results = turn.get("tool_results") or []
    tool_calls: list[ToolCall] = []
    tool_messages: list[ToolMessage] = []
    earlier_refs = 0
    for i, entry in enumerate(tool_results):
        if not isinstance(entry, dict):
            continue
        call_id = str(entry.get("tool_id") or f"history_{i}")
        args = entry.get("args")
        if isinstance(args, dict):
            args_json, _, found = rewrite_request_refs(json.dumps(args, default=str, ensure_ascii=False), None)
            args = json.loads(args_json)
            earlier_refs += found
        result = entry.get("result", "")
        tool_name = str(entry.get("tool_name") or "unknown_tool")
        tool_calls.append(ToolCall(
            id=call_id,
            name=tool_name,
            arguments=args if isinstance(args, dict) else {},
        ))
        result_str = result if isinstance(result, str) else json.dumps(result, default=str)
        result_str, _, found = rewrite_request_refs(result_str, None)
        summary_str, _, found_in_summary = rewrite_request_refs(entry.get("result_summary") or "", None)
        earlier_refs += found + found_in_summary

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

    if earlier_refs:
        # Saved before refs were stored as URLs. Left in, an old `ref1` would
        # resolve to whatever this request numbers `ref1`.
        logger.info(
            "history: made %d citation ref(s) from an earlier request uncitable in %d replayed tool result(s)",
            earlier_refs, len(tool_messages),
        )
    if tool_calls:
        messages.append(AssistantMessage(content=[], tool_calls=tool_calls))
        messages.extend(tool_messages)
    if content:
        messages.append(AssistantMessage(content=_scrub_legacy_system_note(content)))
    return messages


__all__ = ["PipesHubAgentFactory"]
