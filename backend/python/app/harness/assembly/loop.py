"""The legacy agent loop's per-run decisions, as durable pre-run steps and a turn shaper.

Legacy ``PipesHubAgentFactory.create`` decides, once per request and before
the first turn: the domain-agent composition (``plan_domain_agents``), the
mode and the goal (``select_loop_and_goal``: the intent rewrite for react,
the raw query and inferred criteria for quick) and the attachment text the
goal carries. A durable run makes the same decisions with the same functions,
once per run: each is a ``PreRunStep`` whose result the kernel journals as a
slot (K-11), so a resumed attempt reads the decision back instead of making
it again (the intent call is a model call).

``PipesHubTurnShaper`` then builds every model call from the journaled
decisions the way ``Agent.step`` does: the grant (composed delegates plus
residual tools, or the flat set), the tool schemas (``tool_schemas_for_turn``)
and the system prompt (``PipesHubPromptBuilder`` over that spec and goal).
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any, cast

from app.harness.assembly.code_task import (
    CODE_TASK,
    CODE_TASK_GUIDANCE,
    offers_code_task,
)
from app.harness.assembly.code_task import PROMPT_SECTION as CODE_TASK_SECTION
from app.harness.assembly.deep_route import ROUTE_KEY
from app.harness.assembly.prompt_shown import PROMPT_SHOWN_SLOT, prompt_shown
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from app.agent_loop_lib.core.messages import AssistantMessage, Message, ToolCall
    from app.agent_loop_lib.core.types import Goal
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.domain_agents import DomainAgentPlan
    from app.agents.agent_loop.mcp_lazy import LazyMcpServers
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.deep_route import RouteDecision
    from app.harness.assembly.disclosure import Disclosure
    from app.harness.assembly.hooks import DurableHooks
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.context.types import HostBands
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.fold import RunState
    from harness_core.kernel.task_ledger import TaskLedger
    from harness_core.logs import HarnessLog
    from harness_core.models.transports.base import LLMTransport
    from harness_core.models.transports.registry import TransportRegistry

GOAL_STEP = "goal"
COMPOSITION_STEP = "composition"
GOAL_SLOT = PRE_RUN_SLOT_PREFIX + GOAL_STEP
# The words above a pre-run card that are not the goal's (a grounding question's, ADR-RUN-03 D1).
CLARIFYING_INTENT_KEY = "clarifyingIntent"
COMPOSITION_SLOT = PRE_RUN_SLOT_PREFIX + COMPOSITION_STEP
AGENT_NAME = "pipeshub-agent"
# Registered and granted by a hook mid-run once the model holds record ids
# (`citation_tracking`, `attachment_rehydration`).
LATE_TOOLS = ("knowledgegraph__fetch_record",)
# `factory.create` appends the citation hint to the attachment text it adds to the goal.
ATTACHMENT_HINT = (
    "\n\nWhen citing facts from attached documents, use the "
    "Citation ID as a markdown link, e.g. [source](ref2). "
    "Do NOT use record URLs or block indices as citation links."
)


def _quiet_logger() -> logging.Logger:
    """The legacy intent call logs the rewritten query; a durable run logs no content."""
    log = logging.getLogger("app.harness.prerun.intent")
    log.propagate = False
    log.addHandler(logging.NullHandler())
    log.setLevel(logging.CRITICAL + 1)
    return log


def opik_tracing() -> tuple[bool, str | None]:
    """Whether the deployment traces model calls to Opik, and its project:
    legacy's gate (`resolve_opik_gate`) over the Opik settings it already reads."""
    from harness_core.models.transports.opik_tracing import opik_tracing_settings

    return opik_tracing_settings()


def run_transports(
    model: ResolvedModel, run: RunContext, usage_sink: Callable[[str, Any], None] | None = None
) -> TransportRegistry:
    """The run's model under the provider names the legacy helpers resolve
    (the intent call asks for ``langchain``, child agents for the deployment's
    transport), so every model call of the run goes to the run's model; traced
    to Opik as legacy's factory traces them, when the deployment does. Each transport
    is wrapped in ``UsageReportingTransport`` (its ``llm_call.end`` line); with ``usage_sink``
    (an opted-in ``run_usage``), each call's usage is also reported as legacy's factory reports it."""
    from app.agents.agent_loop.factory import DIRECT_TRANSPORT, LANGCHAIN_TRANSPORT
    from app.agents.agent_loop.usage_transport import with_usage_reporting
    from app.harness.assembly.model import build_transport
    from harness_core.models.transports.opik_tracing import traced_transport_factory
    from harness_core.models.transports.registry import TransportRegistry

    traced, project = opik_tracing()
    # `resolve_model` already traced and capped the run's transport; only one built here needs it.
    transport: LLMTransport = model.transport or traced_transport_factory(
        lambda: build_transport(
            model.raw_llm, model_name=model_name(run), model_key=run.model.model_key,
            cache_scope=(run.org_id, run.session_id),
        )[0],
        opik_active=traced,
        project_name=project,
    )()
    registry = TransportRegistry()
    # Always wrapped, as legacy's factory is: each call logs its `llm_call.end`; usage is reported only when opted in.
    sink = usage_sink or _no_usage_report
    for provider in (LANGCHAIN_TRANSPORT, DIRECT_TRANSPORT):
        registry.register(provider, with_usage_reporting(lambda transport=transport: transport, sink))
    return registry


def _no_usage_report(_source: str, _usage: Any) -> None:  # noqa: ANN401
    return None


def model_name(run: RunContext) -> str:
    return run.model.model_name or run.model.model


# Tools a durable run calls itself rather than through a domain agent: the kernel asks before a call that needs a
# person, at that call and with its arguments, which it cannot do for a call inside a delegate's own loop. A
# package install reaches the network (LONGTASK-F2); the delegate keeps run_code, which runs in the run's sandbox.
DURABLE_UNDELEGATED = frozenset({"install_packages"})


def durable_domain_definitions() -> tuple[Any, ...]:
    """The domain agents a durable run composes: the factory's, without ``DURABLE_UNDELEGATED``."""
    from app.agents.agent_loop.domain_agents import DOMAIN_AGENT_DEFINITIONS

    return tuple(
        dataclasses.replace(d, tool_names=frozenset(d.tool_names - DURABLE_UNDELEGATED))
        for d in DOMAIN_AGENT_DEFINITIONS
    )


@dataclasses.dataclass(frozen=True)
class Composition:
    """What ``plan_domain_agents`` + ``register_domain_agents`` built for the run."""

    delegates: tuple[str, ...]
    top_level: tuple[str, ...]
    claims: dict[str, list[str]]

    def slot(self) -> dict[str, JsonValue]:
        return {
            "delegates": list(self.delegates),
            "topLevel": list(self.top_level),
            "claims": cast("JsonValue", {k: list(v) for k, v in sorted(self.claims.items())}),
        }


class CompositionStep:
    """Journals the domain-agent composition the run was built with (PR-3)."""

    key = COMPOSITION_STEP

    def __init__(self, composition: Composition, log: HarnessLog) -> None:
        self._composition = composition
        self._log = log

    async def run(self, input_text: str) -> JsonValue:
        self._log.info(
            "prerun.composition",
            delegates=list(self._composition.delegates),
            topLevel=len(self._composition.top_level),
        )
        return self._composition.slot()


class GoalStep:
    """``select_loop_and_goal`` plus the attachment text, as legacy builds the goal (PR-1, PR-2)."""

    key = GOAL_STEP

    def __init__(
        self,
        env: ToolEnvironment,
        run: RunContext,
        model: ResolvedModel,
        transports: TransportRegistry,
        shaper: PipesHubTurnShaper,
        *,
        tool_names: list[str],
        attachment_text: str,
        registry: ToolRegistry,
        deep: bool = False,
        routing: bool = False,
        lazy: LazyMcpServers | None = None,
        log: HarnessLog,
    ) -> None:
        self._env = env
        self._registry = registry
        self._lazy = lazy
        self._deep = deep
        self._routing = routing
        self._run = run
        self._model = model
        self._transports = transports
        self._shaper = shaper
        self._tool_names = tool_names
        self._attachment_text = attachment_text
        self._log = log

    async def run(self, input_text: str) -> JsonValue:
        from app.agents.agent_loop.router import select_loop_and_goal
        from app.harness.assembly.tool_sources import code_network

        context = self._env.context
        traced, project = opik_tracing()
        _, goal, questions, mode = await select_loop_and_goal(
            chat_mode=self._run.loop_mode,
            query=self._run.query,
            llm=self._model.chat_model(),
            context=context,
            tool_names=list(self._tool_names),
            sandbox_has_network=code_network(self._run),
            opik_active=traced,
            opik_project_name=project,
            transport_registry=self._transports,
            intent_logger=_quiet_logger(),
        )
        if not questions and self._attachment_text:
            # As `factory._attach_current_files`: the files follow the person's words in the
            # current message (`Goal.request_text`) and the task the planners read.
            goal.attachment_text = f"{self._attachment_text}{ATTACHMENT_HINT}"
            goal.description = f"{goal.description}\n\n{goal.attachment_text}"
        prefetched = None
        if self._env.chat is not None:
            from app.harness.assembly.chat import shape_chat_goal

            prefetched = await shape_chat_goal(self._env.chat, context, goal, self._registry, self._log)
        slot: dict[str, JsonValue] = {
            "description": goal.description,
            **_request_fields(goal),
            "requirements": list(goal.requirements),
            "successCriteria": list(goal.success_criteria),
            "constraints": list(goal.constraints),
            "gaps": list(goal.gaps),
            "mode": mode.name,
            "loopKind": mode.loop_kind,
            "composeDomainAgents": mode.compose_domain_agents,
            "wholeDocument": bool(context.needs_whole_document),
            "clarifyingQuestions": [q.model_dump(mode="json") for q in questions],
        }
        route = self._route(goal, mode, questions=bool(questions)) if self._routing else None
        if not questions:
            route = await self._ground(slot, route, prefetched)
        if route is not None:
            slot[ROUTE_KEY] = route.slot()
        # Turn 0's PRE_TURN hooks add their notes to the goal's constraints.
        await self._shaper.begin(goal, slot)
        slot["constraints"] = list(goal.constraints)
        self._log.info(
            "prerun.goal",
            mode=mode.name,
            loopKind=mode.loop_kind,
            rewritten=not mode.skip_intent,
            requirements=len(goal.requirements),
            successCriteria=len(goal.success_criteria),
            constraints=len(goal.constraints),
            clarifyingQuestions=len(questions),
            attachments=bool(self._attachment_text),
            wholeDocument=bool(context.needs_whole_document),
            **({"route": route.to, "routeReason": route.reason, "routeSignals": list(route.signals)} if route else {}),
        )
        if mode.loop_kind != "react" and not (self._deep and mode.loop_kind == "orchestrator"):
            # `auto`'s planExecute/deep verdict: deep runs durably only when the
            # run was admitted as deep (K-L); either verdict runs as react here.
            self._log.warning(
                "mode.downgraded", mode=mode.name, loopKind=mode.loop_kind, ranAs="react"
            )
        return slot

    async def _ground(
        self, slot: dict[str, JsonValue], route: RouteDecision | None, prefetched: Any  # noqa: ANN401 - PrefetchResult
    ) -> RouteDecision | None:
        """ADR-RUN-03 D1: before a hand-off, whether the subject the request names is in what the person can reach.
        Not found, the run asks first (the pre-run card) and stays on the loop: no job starts until they answer."""
        from app.harness.assembly.code_task import offers_code_task
        from app.harness.assembly.deep_route import (
            ENGINE,
            GROUNDED_SIGNAL,
            RouteDecision,
        )
        from app.harness.assembly.grounding_gate import (
            PENDING_QUESTION_KEY,
            RESEARCH_SIGNALS,
            GroundingGate,
            answers_card,
        )
        from app.harness.grounding import GROUNDING_KEY

        if route is not None and RESEARCH_SIGNALS & set(route.signals) and not answers_card(self._run.query):
            # Research goes to the web: a subject missing from the person's knowledge says nothing about it there.
            return route
        delegating = route is not None and route.to == ENGINE
        gate = GroundingGate(
            self._run, self._env.context, self._env.chat, self._log,
            registry=self._registry, offered=self._tool_names, scope=self._env.tool_call_scope, lazy=self._lazy,
        )
        # A request with a deep-work signal may well be handed off by the model: its subject is searched for too.
        checked = await gate.check(
            prefetched,
            delegating=delegating,
            may_delegate=offers_code_task(self._run),
            probe=delegating or (route is not None and bool(route.signals)),
        )
        if checked is None:
            return route
        slot[GROUNDING_KEY] = checked.grounding.to_json()
        if checked.answered and route is not None:
            # The person answered the question asked before a hand-off: it goes ahead now, without the model's say.
            reason = route.reason if route.to == ENGINE else "many_steps"
            return RouteDecision(ENGINE, reason, (*route.signals, GROUNDED_SIGNAL))
        pending = checked.pending()
        if pending is None:
            return route
        if not delegating or route is None:
            # Asked only if the model hands off itself (``code_task`` refuses, and the director asks).
            slot[PENDING_QUESTION_KEY] = pending
            return route
        slot["clarifyingQuestions"] = [checked.question]
        slot[CLARIFYING_INTENT_KEY] = checked.intent
        return RouteDecision("loop", "grounding_question", route.signals)

    def _route(self, goal: Goal, mode: Any, *, questions: bool) -> RouteDecision:  # noqa: ANN401 - ModeDefinition
        """DEEPROUTE's up-front decision from what this step already knows: no model call of its own. ``auto``'s
        tier is the model's verdict only when the run's mode left the choice to it. A run that answers a card is
        routed on the request the card asked about, with the answer (``hand_off_request``)."""
        from app.agents.agent_loop.modes import resolve_mode
        from app.harness.assembly.deep_route import classify
        from app.harness.assembly.grounding_gate import hand_off_request

        return classify(
            hand_off_request(self._run),
            tier=mode.name if resolve_mode(self._run.loop_mode) is None else None,
            requirements=len(goal.requirements),
            deep_research=self._run.deep_research,
            answering_approval=self._env.context.tool_approval is not None,
            asked_question=questions,
        )


class SubAgentGoalStep:
    """A deep-mode child's start, as ``AgentRuntime.run_child`` starts one: its
    goal is the task it was given (no intent call), then PRE_AGENT and turn 0's
    PRE_TURN on it (K-L)."""

    key = GOAL_STEP

    def __init__(
        self, env: ToolEnvironment, run: RunContext, shaper: PipesHubTurnShaper, log: HarnessLog
    ) -> None:
        self._env = env
        self._run = run
        self._shaper = shaper
        self._log = log

    async def run(self, input_text: str) -> JsonValue:
        from app.agent_loop_lib.core.types import Goal

        goal = Goal(description=input_text)
        slot: dict[str, JsonValue] = {
            "description": goal.description,
            "requirements": [],
            "successCriteria": [],
            "constraints": [],
            "gaps": [],
            "mode": "subagent",
            "loopKind": "react",
            "composeDomainAgents": False,
            "wholeDocument": bool(self._env.context.needs_whole_document),
            "clarifyingQuestions": [],
        }
        await self._shaper.begin(goal, slot)
        slot["constraints"] = list(goal.constraints)
        self._log.info("prerun.goal", mode="subagent", loopKind="react", constraints=len(goal.constraints))
        return slot


def _request_fields(goal: Goal) -> dict[str, JsonValue]:
    """The request as the person sent it, apart from the task the intent step made of it (main #4534):
    the run sends their words plus the files (``Goal.request_text``) and quotes their words and the
    reading in the system prompt, as legacy does."""
    return {
        "userMessage": goal.user_message,
        "interpretation": goal.interpretation,
        "attachmentText": goal.attachment_text,
    }


def _optional_text(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def goal_from_slot(slot: JsonValue, query: str) -> Goal:
    from app.agent_loop_lib.core.types import Goal

    data = slot if isinstance(slot, dict) else {}
    return Goal(
        description=str(data.get("description") or query),
        user_message=_optional_text(data.get("userMessage")),
        interpretation=_optional_text(data.get("interpretation")),
        attachment_text=_optional_text(data.get("attachmentText")) or "",
        requirements=[str(v) for v in cast("list[Any]", data.get("requirements") or [])],
        success_criteria=[str(v) for v in cast("list[Any]", data.get("successCriteria") or [])],
        constraints=[str(v) for v in cast("list[Any]", data.get("constraints") or [])],
        gaps=[str(v) for v in cast("list[Any]", data.get("gaps") or [])],
    )


def _with_delegation(schemas: list[Any] | None, offered: list[Any] | None) -> list[Any] | None:
    """The disclosed tools plus ``code_task``, which no registry holds: the kernel offers it beside them (CTL-6)."""
    extra = [s for s in offered or [] if s.name == CODE_TASK]
    return [*(schemas or []), *extra] if extra else schemas


class PipesHubTurnShaper:
    """Each turn's request from the run's journaled decisions, the way
    ``Agent.step`` builds it: the goal as the run's input message, the step
    footers on this run's tool results, the PRE_MODEL shapers, the disclosed
    tool schemas and the system prompt over the run scope's sections."""

    def __init__(
        self,
        env: ToolEnvironment,
        run: RunContext,
        registry: ToolRegistry,
        disclosure: Disclosure,
        hooks: DurableHooks,
        *,
        flat_names: list[str],
        composition: Composition | None,
        provider: str,
        max_turns: int,
        skills: Any = None,  # noqa: ANN401 - a SkillManager, read by the prompt builder
        transports: TransportRegistry | None = None,
        sub_agent: Any = None,  # noqa: ANN401 - the AgentSpec a child run is (K-L)
        deep: bool = False,
        plan: TaskLedger | None = None,
        log: HarnessLog,
    ) -> None:
        from app.agent_loop_lib.runtime.runtime import AgentRuntime
        from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder

        self._env = env
        self._run = run
        self._registry = registry
        self._disclosure = disclosure
        self._hooks = hooks
        self._flat = list(flat_names)
        if self._flat:
            from app.agents.agent_loop.hooks.citations import register_fetch_full_record

            # As legacy builds the agent: before anything lists the registry (deep mode's plan names its tools).
            register_fetch_full_record(env.context, registry)
        self._composition = composition
        self._provider = provider
        self._max_turns = max_turns
        self._sub_agent = sub_agent
        self._deep = deep
        self._plan = plan
        self._log = log
        self._builder = PipesHubPromptBuilder(env.context)
        # Deep mode's planning tools resolve the run's model through the runtime (K-L).
        self._runtime = AgentRuntime(
            transport_registry=transports,
            tool_registry=registry,
            hooks=hooks.registry,
            skills=skills,
            streamed_answer_argument=None,
            spawn_scheduler=None,
            derive_events=None,
        )
        self._scope: Any = None
        self._turn: Any = None
        self._opened: int | None = None
        self._shown_logged = False

    # ---- the run's decisions ----

    def tool_names(self, slots: Mapping[str, JsonValue]) -> list[str]:
        goal = slots.get(GOAL_SLOT)
        if self._deep and isinstance(goal, dict) and goal.get("loopKind") == "orchestrator":
            from app.agents.agent_loop.loops.orchestrator import (
                COORDINATION_TOOL_NAMES,
                COORDINATOR_QUESTION_TOOLS,
            )

            # The orchestrator holds the coordination tools and, where the run offers it, the person's question
            # (ADR-RUN-04 D1 amended, R36-COVER): a sub-agent cannot ask (TAG_UI_ONLY) and hands its need up as an
            # [ESCALATION], which only the root can put to the person; without the tool a deep run never asked.
            return [*COORDINATION_TOOL_NAMES, *(n for n in COORDINATOR_QUESTION_TOOLS if n in self._flat)]
        composes = isinstance(goal, dict) and bool(goal.get("composeDomainAgents"))
        composition = slots.get(COMPOSITION_SLOT)
        if composition is None and self._composition is not None:
            composition = self._composition.slot()
        if composes and isinstance(composition, dict) and composition.get("delegates"):
            names = [str(n) for n in cast("list[Any]", composition.get("topLevel") or [])]
        else:
            names = list(self._flat)
        return names

    def spec(self, slots: Mapping[str, JsonValue]) -> Any:  # noqa: ANN401 - AgentSpec
        from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec

        if self._sub_agent is not None:
            return self._sub_agent
        goal = slots.get(GOAL_SLOT)
        loop_kind = str(goal.get("loopKind") or "react") if isinstance(goal, dict) else "react"
        names, mode, pinned = self._disclosure.decide(self.tool_names(slots), loop_kind)
        names = self._session_grant(names)
        return AgentSpec(
            name=AGENT_NAME,
            system_prompt=self._builder,
            tool_names=names,
            tool_disclosure=mode,  # type: ignore[arg-type]
            pinned_toolsets=pinned,
            model=ModelSpec(
                provider=self._provider, model=model_name(self._run),
                context_window=self._env.context.configured_context_window,
            ),
            max_turns=self._max_turns,
        )

    def _session_grant(self, names: list[str]) -> list[str]:
        """Legacy's last step on the same list (``session_tool_grant``: the tools it used to grant mid-run are in
        the grant from the start, R4). Legacy decides disclosure once and a late grant joins the decided grant;
        deciding again on a grown grant would flip the mode (P4.10 FB-7)."""
        from app.agents.agent_loop.factory import session_tool_grant

        return session_tool_grant(names, self._env.context, self._registry)

    def tool_list_key(self, slots: Mapping[str, JsonValue]) -> str:
        """Which of the chat's tool lists a request takes (R4): one per loop kind, as a chat may change mode."""
        if self._sub_agent is not None:
            return f"agent:{self._sub_agent.name}"
        goal = slots.get(GOAL_SLOT)
        return str(goal.get("loopKind") or "react") if isinstance(goal, dict) else "react"

    # ---- the run scope (what an `Agent` holds for one run) ----

    async def begin(self, goal: Goal, goal_slot: Mapping[str, JsonValue]) -> None:
        """Legacy's run start: PRE_AGENT, then turn 0's PRE_TURN, on ``goal``
        (the goal step's own goal, whose constraints it then journals)."""
        from app.agent_loop_lib.agent import hook_dispatch
        from app.agent_loop_lib.core.context import RunContext as Identity
        from app.agent_loop_lib.core.scope import RunScope, TurnScope

        spec = self.spec({GOAL_SLOT: cast("JsonValue", dict(goal_slot))})
        scope = RunScope(
            identity=Identity(role_name=spec.name, model=model_name(self._run), run_id=self._run.run_id),
            spec=spec,
            runtime=self._runtime,
            goal=goal,
            session_id=self._run.session_id,
        )
        self._scope = scope
        self._disclosure.bind(scope)
        dispatched = ["pre_agent"]
        try:
            await hook_dispatch.dispatch_pre_agent(self._hooks.registry, goal, scope=scope)
            dispatched.append("pre_turn")
            await hook_dispatch.dispatch_pre_turn(
                self._hooks.registry, 0, scope=TurnScope(run=scope, turn_index=0)
            )
        except Exception as exc:
            self._log.warning("hook.failed", stage=dispatched[-1], errorType=type(exc).__name__)
        self._log.info(
            "hook.run_started",
            visibleTools=len(scope.visible_tools or ()),
            promptSections=sorted(scope.extra_prompt_sections),
        )

    async def _resume(self, state: RunState) -> None:
        """A resumed attempt re-runs the start's side effects on a scratch copy
        of the journaled goal (its constraints are already journaled), and
        rebuilds the hooks' run state from the journal."""
        goal_slot = state.slots.get(GOAL_SLOT)
        goal = goal_from_slot(goal_slot, self._run.query)
        await self.begin(goal.model_copy(deep=True), goal_slot if isinstance(goal_slot, dict) else {})
        self._scope.goal = goal
        from harness_core.tools.executor import ToolExecutor

        resolve = ToolExecutor(self._registry).resolve_tool_name
        tracked = 0
        for inv in sorted(state.invocations.values(), key=lambda i: (i.turn, i.index)):
            if inv.state != "completed" or inv.status == "cancelled":
                continue
            # The executor's path for a name it cannot resolve, as its hooks saw it.
            path = self._registry.path_for_name(resolve(inv.name)) or f"/unresolved/{inv.name}"
            self._hooks.tracker.record(path, is_error=inv.status != "ok", turn_index=inv.turn - 1)
            self._hooks.repeated_reads.replay(
                self._scope, path, inv.args_digest, inv.turn - 1, ok=inv.status == "ok"
            )
            tracked += 1
        # The completion gate's nudge count comes back with the journal's request state (CitationJournal.restore).
        continued = sum(1 for r in state.responses if not r.tool_calls and r.turn < state.turn)
        self._log.info("hook.state_rebuilt", outcomes=tracked, continuedTurns=continued)

    async def scope(self, state: RunState) -> Any:  # noqa: ANN401 - RunScope
        if self._scope is None:
            await self._resume(state)
        return self._scope

    def preloaded_skills(self) -> tuple[str, ...]:
        """The skills the run's start loaded in full (``skill_preloading``); none before the run scope is open."""
        from app.agent_loop_lib.hooks.middleware.builtin.skill_preloading import (
            PRELOADED_SKILLS,
        )

        return tuple(self._scope.get(PRELOADED_SKILLS)) if self._scope is not None else ()

    def tool_scope(self, call: ToolCall) -> Any:  # noqa: ANN401 - ToolScope
        from app.agent_loop_lib.core.scope import ToolScope

        if self._turn is None:
            return None
        return ToolScope(
            turn=self._turn,
            call=call,
            tool_path=self._registry.path_for_name(call.name) or "",
            tags=tuple(self._registry.tags_for_name(call.name)),
        )

    # ---- one turn ----

    def _restore(self, slots: Mapping[str, JsonValue]) -> None:
        goal = slots.get(GOAL_SLOT)
        if isinstance(goal, dict) and "wholeDocument" in goal:
            whole = bool(goal["wholeDocument"])
            self._env.context.needs_whole_document = whole
            self._env.context.tool_state["needs_whole_document"] = whole

    def _restore_tool_images(self, messages: list[Message]) -> None:
        """A provider that takes no images in tool results gets them in the
        first user message (`shape_retrieved_image_injection`), where legacy
        keeps every earlier call's for the rest of the run. The journaled tool
        results hold them, so each request is rebuilt with all of them, on
        whichever attempt shapes it (not only the batch this process ran)."""
        from app.agent_loop_lib.core.messages import (
            ImagePart,
            ToolMessage,
            image_data_url,
        )

        state = self._env.context.tool_state
        if state.get("supports_multipart_tool_result", True):
            return
        state["pending_tool_images"] = [
            {"image_url": {"url": image_data_url(part.source)}}
            for message in messages
            if isinstance(message, ToolMessage) and isinstance(message.content, list)
            for part in message.content
            if isinstance(part, ImagePart)
        ]

    def _footers(self, messages: list[Message], state: RunState, spec: Any) -> list[Message]:  # noqa: ANN401
        """``Agent.step``'s step footer on each of this run's tool results."""
        from app.agent_loop_lib.core.messages import ToolMessage

        footers = self._step_footers(messages, state, spec)
        return [
            m.model_copy(update={"step_footer": footers[m.tool_call_id]})
            if isinstance(m, ToolMessage) and m.tool_call_id in footers
            else m
            for m in messages
        ]

    def _step_footers(self, messages: list[Message], state: RunState, spec: Any) -> dict[str | None, str]:  # noqa: ANN401
        from app.agent_loop_lib.core.messages import ToolMessage
        from harness_core.kernel.core.turn_limit import next_stale_rounds, step_footer
        from harness_core.kernel.durable.director import is_synthetic, model_turn_index
        from harness_core.tools.tags import TAG_LIFECYCLE_TERMINAL

        results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
        footers: dict[str | None, str] = {}
        stale = 0
        for turn in sorted({i.turn for i in state.invocations.values()}):
            batch = [i for i in state.invocations_for(turn) if i.call_id in results]
            if not batch or any(i.state != "completed" for i in state.invocations_for(turn)):
                continue
            response = state.response_for(turn)
            if response is not None and is_synthetic(response):
                # Legacy adds a programmatic dispatch's results with no footer, outside any step.
                continue
            stale = next_stale_rounds(
                stale, [(results[i.call_id].is_error, results[i.call_id].content) for i in batch]
            )
            footer = step_footer(model_turn_index(state, turn), spec.max_turns, stale)
            for inv in batch:
                terminal = TAG_LIFECYCLE_TERMINAL in self._registry.tags_for_name(inv.name)
                footers[inv.call_id] = "" if terminal else footer
        return footers

    async def _open(
        self, turn: int, slots: Mapping[str, JsonValue], state: RunState
    ) -> tuple[Any, Any]:  # RunScope, AgentSpec
        """The run scope and ``turn``'s scope, from the journaled decisions."""
        from app.agent_loop_lib.core.scope import TurnScope
        from app.harness.assembly.deep import restore_plan
        from harness_core.kernel.durable.director import model_turn_index

        self._restore(slots)
        scope = await self.scope(state)
        if self._plan is not None:
            self._plan.restore(slots)
        restore_plan(scope, slots)
        spec = self.spec(slots)
        scope.spec = spec
        if offers_code_task(self._run):
            scope.extra_prompt_sections[CODE_TASK_SECTION] = CODE_TASK_GUIDANCE
        # Main #4296: a question over a span of time points the top-level run at the timeline tool, as legacy's
        # PRE_AGENT period_summary_hint does.
        from app.agents.agent_loop.period_summary import (
            PERIOD_SUMMARY_SECTION,
            period_summary_section,
        )

        hint = period_summary_section(scope, self._run.query)
        if hint is not None:
            scope.extra_prompt_sections[PERIOD_SUMMARY_SECTION] = hint
        # Legacy's turn index counts the model's turns (a strategy's own turn is none).
        self._turn = TurnScope(run=scope, turn_index=model_turn_index(state, turn))
        self._opened = turn
        return scope, spec

    async def enter(self, turn: int, state: RunState) -> None:
        """A resumed attempt that starts with ``turn``'s tools opens the scope
        ``shape`` would have, so the tool hooks see the calls (PA-12)."""
        if self._turn is not None and self._opened == turn:
            return
        _ = await self._open(turn, dict(state.slots), state)
        self._log.info("hook.scope_opened", turn=turn, reason="resume_dispatch")

    async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
        from app.agent_loop_lib.agent import hook_dispatch
        from app.agent_loop_lib.agent.prompt import build_system_prompt
        from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
        from app.agent_loop_lib.context.base import ContextBudget
        from app.agent_loop_lib.core.messages import UserMessage
        from harness_core.kernel.core.tokens import (
            count_schema_tokens,
            count_text_tokens,
        )

        slots = {**state.slots, **call.slots}
        scope, spec = await self._open(turn, slots, state)
        goal = scope.goal

        messages = list(call.messages)
        if call.input_index is not None and GOAL_SLOT in slots:
            # Legacy starts the run's context with the goal's request: the person's words and their files.
            messages[call.input_index] = UserMessage(content=goal.request_text())
        messages = self._footers(messages, state, spec)
        self._restore_tool_images(messages)
        schemas = tool_schemas_for_turn(self._disclosure, spec, self._runtime)
        schemas = _with_delegation(schemas, call.tools)
        system = build_system_prompt(
            spec, self._runtime, goal, scope.todos, scope.extra_prompt_sections
        )
        # As `Agent.step` (main #4083): the model's configured window, less the prompt and the tool schemas.
        budget = ContextBudget.for_model(
            spec.model.model,
            context_window=spec.model.context_window,
            reserved_prompt_tokens=count_text_tokens(system or "") + count_schema_tokens(schemas or []),
        )
        messages = await hook_dispatch.dispatch_pre_model(
            self._hooks.registry,
            messages,
            budget,
            turn_index=self._turn.turn_index,
            max_turns=spec.max_turns,
            scope=self._turn,
        )
        # As `Agent.step` does (DUR1-F5): the stable and volatile blocks apart, so an Anthropic or
        # Bedrock transport puts its cache breakpoint on the stable one.
        blocks = getattr(spec.system_prompt, "build_blocks", None)
        system_blocks = (
            list(blocks(spec, self._runtime, goal, scope.todos, scope.extra_prompt_sections))
            if callable(blocks)
            else None
        )
        self._log.debug(
            "run.turn_shaped",
            turn=turn,
            messages=len(messages),
            tools=len(schemas or []),
            systemChars=len(system),
        )
        shown = self._shown(turn, system, system_blocks)
        return dataclasses.replace(
            call,
            slots={**call.slots, PROMPT_SHOWN_SLOT: shown},
            system=system,
            tools=schemas or None,
            messages=messages,
            system_blocks=system_blocks,
            tool_list_key=self.tool_list_key(slots),
        )

    def _shown(self, turn: int, system: str, blocks: list[str] | None) -> dict[str, JsonValue]:
        """The ``prompt.shown`` slot for this request, the same on the shaped and the compiled path."""
        shown = prompt_shown(system, blocks)
        if not self._shown_logged:
            # The attempt's first request: the count `run.assembled` cannot give, as the prompt is built per turn.
            self._shown_logged = True
            self._log.info(
                "run.system_shown",
                turn=turn,
                systemChars=len(system),
                stableDigest=shown["stableDigest"],
                sources=[s["label"] for s in cast("list[dict[str, Any]]", shown["sources"])],
            )
        return shown

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        """The context compiler's host bands (P4.1, flag ``HARNESS_CONTEXT_COMPILER``): the
        prompt builder's stable block as the system band, the disclosed tools by registry
        path, as a note the volatile block (time included), and the shaper's step footer on
        each of this run's results, with the deadline warning on the newest (P4.8: fixed once
        its batch is complete, so it is history, not tail). What ``shape`` does to history the compiler
        does as steps (P4.5, P4.8): the run's input renders as the goal, attachment images
        join it (L0), tool-result images follow their results for a provider that drops them
        (L0.1), each result is capped (L1) and L3's tools are never cleared. The other
        PRE_MODEL shapers do not run here."""
        from app.agent_loop_lib.agent.prompt import build_system_prompt
        from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
        from app.agent_loop_lib.core.messages import ToolMessage
        from app.agents.agent_loop.factory import UNCLEARED_RESULT_TOOLS
        from harness_core.context.results import RESULT_CAP_STATE, loop_result_cap
        from harness_core.context.types import HostBands, ToolEntry
        from harness_core.kernel.durable.director import model_turn_index
        from harness_core.kernel.hooks.builtin.turn_guards import (
            DEADLINE_WARNING_TURNS_LEFT,
            deadline_note,
        )

        slots = {**state.slots, **call.slots}
        scope, spec = await self._open(turn, slots, state)
        schemas = _with_delegation(tool_schemas_for_turn(self._disclosure, spec, self._runtime), call.tools) or []
        blocks = getattr(spec.system_prompt, "build_blocks", None)
        if callable(blocks):
            stable, volatile = cast(
                "tuple[str, str]",
                blocks(spec, self._runtime, scope.goal, scope.todos, scope.extra_prompt_sections),
            )
        else:
            # A sub-agent's own prompt has no stable/volatile split: all of it is the system band.
            stable = build_system_prompt(spec, self._runtime, scope.goal, scope.todos, scope.extra_prompt_sections)
            volatile = ""
        footers = self._step_footers(list(call.messages), state, spec)
        # The PRE_MODEL deadline note (`install_turn_guards`), which the compiled path does not dispatch, on the
        # newest result's footer as legacy sends it (SYNC2-F2). A compiled footer is journaled once shown, so the
        # note stays on that result in later requests, where legacy's next request drops it.
        due = spec.max_turns is not None and spec.max_turns - model_turn_index(state, turn) == DEADLINE_WARNING_TURNS_LEFT
        last = call.messages[-1] if call.messages else None
        if due and isinstance(last, ToolMessage):
            footers[last.tool_call_id] = footers.get(last.tool_call_id, "") + deadline_note(scope)
        tool_state = self._env.context.tool_state
        system = "\n\n".join(part for part in (stable, volatile) if part)
        shown = self._shown(turn, system, [stable, volatile])
        return HostBands(
            system=stable,
            tools=tuple(
                ToolEntry(tool_id=self._registry.path_for_name(s.name) or s.name, schema=s) for s in schemas
            ),
            # Notes, not a tail: a message after the history that the next request drops leaves the provider
            # nothing to reuse past the tools (P4.11), so each text is journaled once, where it first applies.
            notes=(volatile,),
            input_text=scope.goal.request_text() if GOAL_SLOT in slots else None,
            input_parts=_attachment_images(self._env.context.attachment_image_blocks),
            tool_images_after_results=not tool_state.get("supports_multipart_tool_result", True),
            # L1 and L3's protection as compiler steps (P4.8): the same cap and the same tools.
            max_result_chars=loop_result_cap(self._env.context.tool_state.get(RESULT_CAP_STATE)),
            keep_results=UNCLEARED_RESULT_TOOLS,
            result_footers={call_id: footer for call_id, footer in footers.items() if call_id and footer},
            tool_list_key=self.tool_list_key(slots),
            slots={PROMPT_SHOWN_SLOT: shown},
        )


def _attachment_images(blocks: list[dict[str, Any]]) -> tuple[Any, ...]:
    """The attachment images legacy's L0 injects (``shape_image_injection``), as parts."""
    from app.utils.chat_helpers import image_dict_to_part

    return tuple(part for block in blocks if (part := image_dict_to_part(block)) is not None)


class ClarificationGate:
    """Legacy's pre-run clarification: a fatally ambiguous request (the intent
    call returned questions) is asked back without running the agent."""

    def needs_input(self, slots: Mapping[str, JsonValue]) -> bool:
        goal = slots.get(GOAL_SLOT)
        return isinstance(goal, dict) and bool(goal.get("clarifyingQuestions"))


class PipesHubResponseReview:
    """POST_MODEL (completion gate, truncation recovery) before a no-tool
    response becomes the answer."""

    def __init__(self, shaper: PipesHubTurnShaper, hooks: DurableHooks, log: HarnessLog) -> None:
        self._shaper = shaper
        self._hooks = hooks
        self._log = log

    async def review(self, message: AssistantMessage, turn: int, state: RunState) -> str | None:
        from app.agent_loop_lib.agent import hook_dispatch
        from app.agent_loop_lib.core.scope import TurnScope
        from harness_core.kernel.durable.director import model_turn_index
        from harness_core.kernel.durable.transcript import text_of

        scope = await self._shaper.scope(state)
        index = model_turn_index(state, turn)
        ctx = await hook_dispatch.dispatch_post_model(
            self._hooks.registry,
            message,
            [],
            index,
            scope=TurnScope(run=scope, turn_index=index),
        )
        recovery = ctx.recovery_message
        if recovery is None:
            return None
        self._log.info(
            "hook.nudged",
            turn=turn,
            reason="truncated" if message.truncated else "empty_response",
        )
        return text_of(recovery)


def build_composition(
    env: ToolEnvironment,
    run: RunContext,
    registry: ToolRegistry,
    transports: TransportRegistry,
    *,
    hooks: Any = None,  # noqa: ANN401 - HookRegistry
    allowed: set[str],
    skills: bool = False,
) -> tuple[Composition, DomainAgentPlan] | None:
    """``plan_domain_agents`` + ``register_domain_agents`` over the run's registry,
    for a mode that may compose (react, or auto until the intent call picks).
    ``skills``: the run has a skill manager, so domain agents share the read-only
    skill tools, as the factory gives them."""
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agents.agent_loop.domain_agents import (
        DomainAgentPlan,
        plan_domain_agents,
        register_domain_agents,
    )
    from app.agents.agent_loop.factory import (
        _DOMAIN_SHARED_NAV_TOOL_NAMES,
        _SWITCHED_TOOL_NAMES,  # pyright: ignore[reportPrivateUsage]
        _composed_agents_enabled,
        _transport_provider,
    )
    from app.agents.agent_loop.lazy_tools_wiring import (
        lazy_tools_enabled,
        lazy_tools_scope,
        make_lazy_tools_decider,
    )
    from app.agents.agent_loop.modes import resolve_mode
    from app.agents.agent_loop.skills_wiring import DOMAIN_SHARED_SKILL_TOOL_NAMES
    from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer

    mode = resolve_mode(run.loop_mode)
    if not _composed_agents_enabled() or (mode is not None and not mode.compose_domain_agents):
        return None
    planned = plan_domain_agents(registry, durable_domain_definitions())
    # Only what the catalog kept may be claimed (or offered as residual).
    plan = DomainAgentPlan(
        definitions=planned.definitions,
        registered_names=tuple(n for n in planned.registered_names if n in allowed),
        claims={
            agent: [n for n in names if n in allowed]
            for agent, names in planned.claims.items()
            if any(n in allowed for n in names)
        },
    )
    children = AgentRuntime(
        transport_registry=transports,
        tool_registry=registry,
        hooks=hooks,
        summarizer=PipesHubToolSummarizer(),
        streamed_answer_argument=None,
        spawn_scheduler=None,
        derive_events=None,
    )
    essential = (["skills"] if skills else []) + list(env.context.essential_toolset_names)
    top_level = register_domain_agents(
        plan,
        registry,
        children,
        env.context,
        provider=_transport_provider(),
        model_name=model_name(run),
        # The factory's inputs (`PipesHubAgentFactory.create`): each domain agent's own lazy
        # disclosure, and the skill tools shared with it when skills are wired (DUR1).
        lazy_tools=make_lazy_tools_decider(
            apply=lazy_tools_enabled() and lazy_tools_scope() in ("domain", "both"),
            essential_names=frozenset(essential),
            context=env.context,
            uncounted_names=_SWITCHED_TOOL_NAMES,
        ),
        shared_tool_names=(DOMAIN_SHARED_SKILL_TOOL_NAMES if skills else frozenset()) | _DOMAIN_SHARED_NAV_TOOL_NAMES,
    )
    delegates = tuple(n for n in plan.agent_names if n in top_level)
    return Composition(delegates, tuple(top_level), dict(plan.claims)), plan


__all__ = [
    "ATTACHMENT_HINT",
    "COMPOSITION_SLOT",
    "ClarificationGate",
    "GOAL_SLOT",
    "LATE_TOOLS",
    "Composition",
    "CompositionStep",
    "DURABLE_UNDELEGATED",
    "durable_domain_definitions",
    "GoalStep",
    "PipesHubResponseReview",
    "PipesHubTurnShaper",
    "SubAgentGoalStep",
    "build_composition",
    "goal_from_slot",
    "model_name",
    "run_transports",
]
