"""`PipesHubAgentFactory`: Phase 7 — assembles an agent-loop `Agent` +
`AgentRuntime` from PipesHub's per-request `AgentContext`, wiring together
every adapter-layer piece built in Phases 2-6:

- `LangChainTransport` (Phase 2) registered under the `"langchain"` provider
- `PipesHubToolLoader` (Phase 3) for the per-request `ToolRegistry`
- `PipesHubPromptBuilder` + `ToolGuidanceProvider` (Phase 3/4) for the system prompt
- Phase 5's hook middleware (tool blocking, citation tracking, conversation
  memory, result accumulation, `ask_user_question` SSE)
- `SSEEventEmitter` (this phase) for real-time tool-orchestration events
- `router.select_loop()` (this phase) for `chatMode` -> `LoopStrategy`
- Phase 10: when `select_loop()` resolves to `OrchestratorLoop`, the deep
  agent's top-level `spec.tool_names` is narrowed to the four coordination
  tools (`create_plan`/`critique_plan`/`spawn_agent`/`verify_result` —
  registered onto the request's `tool_registry` here) and
  `AgentRuntime.spec_factory` is wired so `spawn_agent` can resolve a
  domain "role" string into a scoped child `AgentSpec` — see
  `loops/orchestrator.py` for the full Decompose/Critique/Dispatch/Verify
  design rationale.

Deliberately minimal on the `AgentRuntime` side (`budget`/stores/etc. all
left `None`) — this migration keeps PipesHub's existing storage backends
(no SQLite, no new databases; see the plan's Design Constraints), so none
of agent-loop's optional persistence stores apply here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
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
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.loops.orchestrator import (
    COORDINATION_TOOL_NAMES,
    OrchestratorLoop,
    domain_spec_factory,
    register_coordination_tools,
)
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.router import select_loop
from app.agents.agent_loop.sandbox_bridge import (
    build_coding_sandbox_manager,
    register_coding_sandbox_hooks,
    register_coding_sandbox_tools,
)
from app.agents.agent_loop.sse_emitter import SSEEventEmitter
from app.agents.agent_loop.tool_guidance import ToolGuidanceProvider
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.modules.agents.qna.tool_system import code_execution_enabled

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agent_loop_lib.core.messages import Message
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Matches nodes.py's ReAct/planner loop cap (`MAX_ITERATIONS` — see
# tool_system.py / react_agent_node's `recursion_limit`); kept as one named
# constant here rather than a magic number in `create()`.
_MAX_TURNS = 15


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
    ) -> tuple[Agent, AgentRuntime]:
        """Builds a fully-wired `Agent`. Async because `chatMode == "auto"`
        (the default) resolves its `LoopStrategy` via an LLM classification
        call (`router.select_loop`) before the `Agent` can be constructed.
        """
        transport_registry = TransportRegistry()
        transport_registry.register(
            "langchain", lambda: LangChainTransport(llm, model_name=model_name)
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

        sandbox_manager = None
        if code_exec_enabled:
            sandbox_manager = build_coding_sandbox_manager()
            register_coding_sandbox_tools(tool_registry, sandbox_manager)
            # Stashed on the context (not returned from create()) so
            # stream_bridge.py's finally block can tear it down without
            # widening this method's (Agent, AgentRuntime) return contract.
            context.sandbox_manager = sandbox_manager
            logger.info(
                "PipesHubAgentFactory.create: registered coding-sandbox tools: %s",
                [n for n in tool_registry.names() if n in ("run_code", "install_packages", "read_sandbox_file")],
            )
        else:
            logger.info(
                "PipesHubAgentFactory.create: code execution disabled — coding-sandbox tools "
                "(run_code/install_packages/read_sandbox_file) will NOT be available this turn"
            )

        hooks = self._build_hooks(context, sandbox_manager)

        loop = await select_loop(chat_mode=chat_mode, query=query, llm=llm, context=context)

        # Phase 10: the deep agent's top-level `Agent` is a pure
        # planner/dispatcher — it must never call connector tools directly
        # (that's what spawned, domain-scoped sub-agents are for), so its
        # own `tool_names` is narrowed to the four coordination tools while
        # every OTHER loop keeps the full, unrestricted registry.
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
            tool_names = tool_registry.names()

        logger.info(
            "PipesHubAgentFactory.create: loop=%s | %d tool(s) granted to agent spec "
            "(run_code available=%s)",
            loop.name if hasattr(loop, "name") else type(loop).__name__,
            len(tool_names), "run_code" in tool_names,
        )

        runtime = AgentRuntime(
            transport_registry=transport_registry,
            tool_registry=tool_registry,
            hooks=hooks,
            event_emitter=SSEEventEmitter(context.event_sink) if context.event_sink is not None else None,
            spec_factory=spec_factory,
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

        return agent, runtime

    @staticmethod
    def _build_hooks(
        context: "AgentContext", sandbox_manager: Any = None,
    ) -> HookRegistry:
        """Phase 5's five hooks, wired onto a fresh `HookRegistry` (never a
        shared/global one — see that phase's hook docstrings for why
        per-request instances matter for `ToolErrorTracker`/`CitationCollector`
        state isolation across concurrent requests)."""
        hooks = HookRegistry()

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
            register_coding_sandbox_hooks(hooks, context, sandbox_manager)

        return hooks

    @staticmethod
    async def _seed_conversation_history(agent: Agent, previous_conversations: list[dict[str, Any]]) -> None:
        """Loads prior turns into the agent's `ContextManager` so the model
        sees them as ordinary conversation history on turn 0 — the
        agent-loop equivalent of `nodes.py::_build_conversation_messages`
        (called from `_build_planner_messages`, which both the legacy
        planner AND `react_agent_node` use to seed multi-turn context).

        Text-only: unlike `_build_conversation_messages`, this does not
        re-fetch/interleave historical PDF or image attachment blocks —
        `conversation_enrichment` (Phase 5, PRE_TURN) already covers the
        practical "reuse what the previous turn fetched" signal via
        `goal.constraints`, and the current turn's OWN attachments are
        still resolved in full by `RespondPipeline`/the tool adapters.
        """
        from app.agent_loop_lib.context.manager import ContextManager

        ctx = ContextManager()
        for turn in previous_conversations:
            message = _convert_conversation_turn(turn)
            if message is not None:
                await ctx.add(message)
        # `Agent.run()` only builds its own `ContextManager` when `self.
        # _context is None` (see agent/__init__.py) — pre-seeding here,
        # before `run()` is ever called, is the documented extension point
        # for exactly this (`run_child()` does the same thing).
        agent._context = ctx


def _convert_conversation_turn(turn: dict[str, Any]) -> "Message | None":
    role = turn.get("role", "")
    content = str(turn.get("content", "")).strip()
    if not content:
        return None
    if role == "user_query":
        return UserMessage(content=content)
    if role == "bot_response":
        return AssistantMessage(content=content)
    return None


__all__ = ["PipesHubAgentFactory"]
