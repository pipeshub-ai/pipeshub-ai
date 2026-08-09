"""`TaskSpecAssembler`: `TaskDefinition` -> a ready-to-run `Agent`. The
anti-corruption layer -- per `domain/models.py`'s module docstring, this is
the ONLY file in the task engine allowed to import both
`app.services.tasks.domain` and `agent_loop_lib`/`app.agents.agent_loop`.
Every other task-engine module either stays pure domain or stays a thin
port/adapter; this one is deliberately impure so that boundary is
enforceable by code review (a domain or port file that starts importing
`agent_loop_lib` is always wrong; this file doing so is not).

Does not itself decide WHEN a task runs (`runtime/scheduler_loop.py`,
Phase 3) or how a run's lifecycle is tracked (`runtime/executor.py`, Phase
4) -- `assemble()` is a pure translation + one-time I/O step (resolve the
LLM, load tools), returning an `Agent` the caller drives with
`agent.run(goal)` (first attempt) or `agent.resume(checkpoint_id)`
(recovery), then reads `agent.run_ctx.run_id` off to persist as
`TaskRun.agent_run_id` for the next resume.
"""

from __future__ import annotations

import contextlib
import difflib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import (
    IncrementalLoop,
    LoopStrategy,
    PlanCritiqueExecuteLoop,
    PlanExecuteLoop,
    ReActLoop,
    ReflexionLoop,
    SingleShotLoop,
)
from app.agent_loop_lib.agent.spawn_scheduler import (
    completed_spawn_results,
    schedule_spawn_batch,
)
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agents.agent_loop.loops.orchestrator import domain_spec_factory
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.config.constants.service import config_node_constants
from app.services.tasks.domain.errors import ToolResolutionError
from app.services.tasks.runtime.headless_context import (
    build_headless_context,
    build_transport_registry,
)
from app.utils.llm import get_llm
from app.utils.web_search_config import resolve_default_web_search_config

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agent_loop_lib.modules.stores.checkpoint.base import CheckpointStore
    from app.agent_loop_lib.modules.stores.timeline.base import TimelineStore
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext
    from app.config.configuration_service import ConfigurationService
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.tasks.domain.models import TaskDefinition, TaskStep

__all__ = ["TaskDagLoop", "TaskSpecAssembler", "TaskStepReport", "compute_step_report"]

logger = logging.getLogger(__name__)

# Task-engine name -> agent_loop_lib LoopStrategy, keyed off
# `TaskDefinition.loop_strategy_name` (persisted at creation time -- see
# Part A2 of the plan). Unknown/legacy names fall back to `ReActLoop`
# rather than raising: a task created before a loop name was renamed (or
# with a typo that slipped past creation-time validation) should still run,
# just with the default shape, not hard-fail at every scheduled fire.
_LOOP_STRATEGIES: dict[str, type[LoopStrategy]] = {
    "react": ReActLoop,
    "single_shot": SingleShotLoop,
    "reflexion": ReflexionLoop,
    "plan_execute": PlanExecuteLoop,
    "plan_critique_execute": PlanCritiqueExecuteLoop,
    "incremental": IncrementalLoop,
}

_SYSTEM_PROMPT = (
    "You are an autonomous task-execution agent running on a schedule -- "
    "there is no human watching this run in real time. Your final text "
    "answer is automatically captured and delivered to the user as a "
    "notification; you do NOT need a messaging, email, or chat tool to "
    "communicate your result -- just write it as your final answer. "
    "Complete the task described below using the tools available to you. "
    "If you cannot proceed without information the task doesn't provide, "
    "say so clearly in your final answer rather than guessing or "
    "fabricating results."
)


def _resolve_loop(name: str) -> LoopStrategy:
    return _LOOP_STRATEGIES.get(name, ReActLoop)()


def _model_name_from_config(config: dict[str, Any], fallback: str) -> str:
    """Mirrors `api/routes/agent.py`'s own resolution
    (`config_data.get("model", matching_config.get("modelName", model_key))`)
    so a task's traced LLM calls show the same model name a chat request's
    would for the identical config entry."""
    configuration = config.get("configuration") or {}
    raw = configuration.get("model") or config.get("modelName") or config.get("modelKey") or fallback
    if isinstance(raw, str) and "," in raw:
        return raw.split(",")[0].strip()
    return str(raw)


@dataclass
class TaskStepReport:
    """Per-run rollup of a `TaskDagLoop` dispatch, in the shape
    `TaskExecutor` persists onto `TaskRun.completed_steps`/`failed_step_id`/
    `skipped_steps` (Part B4's domain model — `failed_step_id` is
    singular, so any additional independently-failed branch beyond the
    first is carried in `additional_failed_step_ids` for the caller to
    fold into `TaskRun.error` text rather than silently drop)."""

    completed_steps: list[str] = field(default_factory=list)
    failed_step_id: str | None = None
    skipped_steps: list[str] = field(default_factory=list)
    additional_failed_step_ids: list[str] = field(default_factory=list)

    @property
    def all_failed_step_ids(self) -> list[str]:
        return ([self.failed_step_id] if self.failed_step_id else []) + self.additional_failed_step_ids

    @property
    def is_clean(self) -> bool:
        return not self.failed_step_id and not self.skipped_steps


def compute_step_report(steps: "list[TaskStep]", results: dict[str, AgentResult]) -> TaskStepReport:
    """Classify every `TaskStep` as completed/failed/skipped from the raw
    per-step `AgentResult`s a `TaskDagLoop` dispatch produced.

    Deliberately uses ONLY the static `depends_on` graph (never string-
    matching a `SpawnDependencyError` message) to decide "skipped" vs.
    "failed": a step whose prerequisite failed or was itself skipped is
    always reported as skipped, regardless of what `results` happens to
    hold for it (per `schedule_spawn_batch`'s own contract, a step whose
    prerequisite failed never actually runs its child — see
    `spawn_scheduler._run_dependent_spawn`). A step with no dependency
    issue is "completed" iff its own result reports success; a step
    missing from `results` entirely (e.g. batch-validation rejected it —
    fan-out cap, cycle, bad tool name) is conservatively "failed", never
    silently dropped from the report.

    Pure and synchronous — no I/O, safe to unit test directly against
    canned `AgentResult`s (mirrors `validate_spawn_batch`'s own split of
    "pure classification" from "async dispatch").
    """
    step_by_id = {s.id: s for s in steps}
    status: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for step in steps:
            if step.id in status:
                continue
            deps = step.depends_on
            if any(d in step_by_id and d not in status for d in deps):
                continue  # a dependency hasn't been classified yet -- wait for a later pass
            if any(status.get(d) in ("failed", "skipped") for d in deps):
                status[step.id] = "skipped"
            else:
                result = results.get(step.id)
                status[step.id] = "completed" if (result is not None and result.success) else "failed"
            changed = True
    for step in steps:
        # Only reachable via a cyclic depends_on that slipped past
        # creation-time validation (see task engine plan Part A2) -- the
        # fixed-point loop above can never resolve a genuine cycle.
        # Defensive, not silent: every step ends up in exactly one bucket.
        status.setdefault(step.id, "failed")

    return TaskStepReport(
        completed_steps=[s.id for s in steps if status[s.id] == "completed"],
        failed_step_id=next((s.id for s in steps if status[s.id] == "failed"), None),
        skipped_steps=[s.id for s in steps if status[s.id] == "skipped"],
        additional_failed_step_ids=[s.id for s in steps if status[s.id] == "failed"][1:],
    )


def _task_step_goal_text(step: "TaskStep") -> str:
    """Mirrors `orchestrator.py`'s `_step_goal_text` exactly -- `TaskStep`
    deliberately carries the same `boundaries`/`output_format` fields as
    `PlanStep` for this reason (see `domain/models.py`'s docstring)."""
    parts = [step.description]
    if step.boundaries:
        parts.append(
            "Do NOT do the following — other steps cover this:\n"
            + "\n".join(f"- {b}" for b in step.boundaries)
        )
    if step.output_format:
        parts.append(f"Required output format: {step.output_format}")
    return "\n\n".join(parts)


def _task_step_to_spawn_call(step: "TaskStep") -> ToolCall:
    return ToolCall(
        id=f"task_step__{step.id}",
        name="spawn_agent",
        arguments={
            "role": step.domain or "general",
            "goal": _task_step_goal_text(step),
            "task_id": step.id,
            "depends_on": step.depends_on,
            "tools": step.tool_names,
            "reasoning": f"Scheduled task step '{step.id}'",
        },
    )


def _summarize_dag(steps: "list[TaskStep]", report: TaskStepReport, results: dict[str, AgentResult]) -> str:
    lines = [f"{len(report.completed_steps)}/{len(steps)} step(s) completed."]
    for failed_id in report.all_failed_step_ids:
        failed_result = results.get(failed_id)
        err = failed_result.error if failed_result is not None else "no result was produced for this step"
        lines.append(f"Step '{failed_id}' failed: {err}")
    if report.skipped_steps:
        lines.append(f"Skipped (a dependency failed): {', '.join(report.skipped_steps)}")
    lines.extend(
        f"- {step.id}: {results[step.id].output}"
        for step in steps
        if step.id in report.completed_steps and step.id in results
    )
    return "\n".join(lines)


class TaskDagLoop(LoopStrategy):
    """`LoopStrategy` for a task whose `TaskDefinition.steps` is populated
    (task engine plan Part C2/A2, Phase 6 "sub-task DAG"). Selected by
    `TaskSpecAssembler.build_agent_spec` instead of the task's usual
    `loop_strategy_name` -- a structured DAG has no use for a model
    deciding step-by-step what to do next; the steps and their
    dependencies were already decided (and confirmed with the user) at
    creation time (Part A2).

    Deterministic and LLM-free for the DISPATCH itself: builds one
    synthetic `spawn_agent` `ToolCall` per `TaskStep` (mirroring
    `orchestrator.py`'s own `_programmatic_dispatch`, which this is the
    task-engine sibling of) and routes them through the SAME
    `schedule_spawn_batch` dependency scheduler the orchestrator uses --
    one dependency-resolution implementation, used identically whether
    the DAG came from an LLM's structured plan or a task's persisted
    `steps`. Each individual step's own child agent still calls its LLM
    normally; only the fan-out/ordering/dependency-injection is
    programmatic.

    Never calls `agent.step()` — a fully valid `LoopStrategy` shape (see
    `LoopStrategy.run`'s own docstring: "calling `agent.step()` as
    needed", not "at least once").

    DAG resume note: on every `run()` (fresh or resumed via
    `Agent.resume()`, which restores `SPAWN_RESULTS_SLOT` before re-
    entering the loop — see that slot's own docstring), steps already
    present in `SPAWN_RESULTS_SLOT` are not re-submitted to
    `schedule_spawn_batch`. A still-pending dependent of an already-
    completed step does not strictly need this filter to behave correctly
    — `validate_spawn_batch` only treats the resubmitted duplicate task_id
    itself as an error (`_run_dependent_spawn` resolves a dependency
    straight from `SPAWN_RESULTS_SLOT` before ever looking at in-batch
    siblings, so the dependent is never marked invalid by
    `_propagate_invalidity`) — but resubmitting a finished step is still
    pure waste: a guaranteed-rejected call, a duplicate-task_id error
    string with nothing useful to do with it, and one more no-op entry in
    `errors_by_call_id` on every resumed turn for the lifetime of the run.
    """

    name = "task_dag"

    def __init__(self, steps: "list[TaskStep]") -> None:
        self._steps = steps

    async def run(self, agent: "Agent", goal: Goal) -> AgentResult:
        known_task_ids = set(completed_spawn_results(agent.scope).keys())
        pending_steps = [step for step in self._steps if step.id not in known_task_ids]
        calls = [_task_step_to_spawn_call(step) for step in pending_steps]
        if calls:
            tasks = await schedule_spawn_batch(
                agent, agent.runtime, calls, agent.scope,
                goal=goal, turn_index=agent.start_turn_index, started_at=agent.started_at,
            )
            for call in calls:
                # Every outcome (success or failure) lands in
                # SPAWN_RESULTS_SLOT regardless of whether awaiting the
                # task itself raises (see `schedule_spawn_batch`'s own
                # contract) -- the exception itself carries nothing
                # `compute_step_report` needs that isn't already in the
                # slot, via `completed_spawn_results` below.
                with contextlib.suppress(Exception):
                    await tasks[call.id]

        results = completed_spawn_results(agent.scope)
        report = compute_step_report(self._steps, results)

        needs_input: str | None = None
        for step in self._steps:
            result = results.get(step.id)
            if result is not None and result.needs_input:
                needs_input = result.needs_input
                break

        summary = _summarize_dag(self._steps, report, results)
        detail = {
            "completed_steps": report.completed_steps,
            "failed_step_id": report.failed_step_id,
            "skipped_steps": report.skipped_steps,
        }
        if needs_input:
            return await agent.succeed(goal, summary, needs_input=needs_input, detail=detail)
        if report.is_clean:
            return await agent.succeed(goal, summary, detail=detail)
        return await agent.fail(goal, summary, detail=detail)


class TaskSpecAssembler:
    """One instance per scheduler/executor process. Carries no per-task
    state -- `_tool_loader` is the only collaborator, and it is itself
    stateless (a fresh `ToolRegistry` per `load()` call)."""

    def __init__(self, tool_loader: PipesHubToolLoader | None = None) -> None:
        self._tool_loader = tool_loader or PipesHubToolLoader()

    @staticmethod
    def build_goal(task: "TaskDefinition") -> Goal:
        """`instructions`, not `description`: `TaskDefinition.instructions`
        is the assembled prompt that already folds in clarifications and
        reasoning (see that field's docstring); `description` is kept only
        as the original verbatim NL request for display purposes."""
        clarifications = {
            str(item.get("question", idx)): str(item.get("answer", ""))
            for idx, item in enumerate(task.clarifications)
        }
        return Goal(description=task.instructions, clarifications=clarifications)

    @staticmethod
    def build_agent_spec(task: "TaskDefinition", *, tool_names: list[str], model_name: str) -> AgentSpec:
        # Two independent caps exist on the domain model (`max_turns` is a
        # general execution ceiling; `budget.max_turns_per_run` is a cost
        # -governance knob an org admin can tighten without touching the
        # task itself) -- the stricter one always wins.
        max_turns = min(task.max_turns, task.budget.max_turns_per_run)
        # A structured `steps` DAG (Phase 6) always dispatches via
        # `TaskDagLoop`, regardless of `task.loop_strategy_name` -- that
        # field only makes sense for the single-goal path (it names a
        # MODEL-DRIVEN loop shape; a DAG's shape was already decided,
        # step-by-step, at creation time). `max_turns` still bounds the
        # DAG's own children (each gets its own `AgentSpec` via
        # `domain_spec_factory`, not this one).
        loop = TaskDagLoop(task.steps) if task.steps else _resolve_loop(task.loop_strategy_name)
        return AgentSpec(
            name=f"task:{task.task_id}",
            description=task.title,
            system_prompt=_SYSTEM_PROMPT,
            tool_names=list(tool_names),
            model=ModelSpec(provider="langchain", model=model_name),
            loop=loop,
            max_turns=max_turns,
        )

    @staticmethod
    def build_runtime(
        *,
        tool_registry: "ToolRegistry",
        checkpoint_store: "CheckpointStore | None" = None,
        timeline_store: "TimelineStore | None" = None,
        llm: "BaseChatModel",
        model_name: str,
        spec_factory: "Callable[..., AgentSpec] | None" = None,
        hooks: "HookRegistry | None" = None,
    ) -> AgentRuntime:
        return AgentRuntime(
            transport_registry=build_transport_registry(llm, model_name=model_name),
            tool_registry=tool_registry,
            checkpoint_store=checkpoint_store,
            timeline_store=timeline_store,
            spec_factory=spec_factory,
            # `AgentRuntime.hooks` defaults to None, and both hooks a task run
            # depends on register onto this object after the runtime is built:
            # the dry-run write block and the side-effect flag that decides
            # whether a crashed run is safe to retry. A None registry made both
            # silent no-ops.
            hooks=hooks if hooks is not None else HookRegistry(),
        )

    async def _resolve_llm(
        self, config_service: "ConfigurationService", model_ref: str | None,
    ) -> tuple["BaseChatModel", str]:
        """`model_ref` is treated as a `modelKey` filter over the org's
        configured `llm` models -- `None` (the common case: most tasks
        don't pin a model) or a stale/deleted key both fall back to
        `get_llm`'s own default-model selection rather than failing the
        run over a config-lookup quirk."""
        llm_configs = None
        if model_ref:
            ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value, use_cache=False)
            candidates = [c for c in (ai_models or {}).get("llm", []) if c.get("modelKey") == model_ref]
            llm_configs = candidates or None
        llm, config = await get_llm(config_service, llm_configs)
        return llm, _model_name_from_config(config, model_ref or "")

    async def _resolve_toolset_credentials(
        self,
        task: "TaskDefinition",
        *,
        config_service: "ConfigurationService",
        log: "Logger",
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Load toolset instance metadata and user credentials from etcd for
        a headless (scheduled/webhook) run.

        Mirrors the security-critical section in `api/routes/agent.py`
        (`chat_stream`), which reads the same etcd paths for interactive
        chat.  The credential_lookup_id follows the same rule: use `user_id`
        for normal accounts, `task_id` (as agent_key) for service accounts.

        Returns (agent_toolsets, toolset_configs) — both empty when
        `task.toolset_ids` is empty or none of the credentials exist yet.
        """
        from app.agents.constants.toolset_constants import get_toolset_config_path

        if not task.toolset_ids:
            return [], {}

        # Service accounts use the task_id as an agent key (the same
        # convention agent.py uses agent_id for agent service accounts).
        credential_lookup_id = (
            task.task_id if task.principal.is_service_account else task.principal.user_id
        )

        # Load all admin-created toolset instances to get metadata.
        all_instances: list[dict[str, Any]] = []
        try:
            all_instances = await config_service.get_config(
                "/services/toolset-instances", default=[]
            ) or []
        except Exception as exc:
            log.warning("Could not load toolset instances for headless run: %s", exc)
            return [], {}

        # Index instances by _id for O(1) lookup.
        instances_by_id = {inst.get("_id"): inst for inst in all_instances if inst.get("_id")}

        agent_toolsets: list[dict[str, Any]] = []
        toolset_configs: dict[str, dict[str, Any]] = {}

        for instance_id in task.toolset_ids:
            instance_meta = instances_by_id.get(instance_id)
            if instance_meta is None:
                log.warning("Toolset instance %s not found; skipping for headless run", instance_id)
                continue
            try:
                etcd_path = get_toolset_config_path(instance_id, credential_lookup_id)
                config = await config_service.get_config(etcd_path)
            except Exception as exc:
                log.warning("Could not load credentials for toolset %s: %s", instance_id, exc)
                continue

            # Admin-created instances (`toolsets.py::create_toolset_instance`)
            # are stored with `instanceName`/`toolsetType` -- never `name`,
            # `displayName`, `tools`, or `selectedTools`. Reading those here
            # always produced "", which `PipesHubToolLoader
            # ._build_configured_apps_set`/`ToolInstanceCreator
            # ._get_config_for_app` match against `name` -- an empty name
            # silently drops every external toolset the task declared
            # (see ToolResolutionError for its declared tools instead of
            # a clear "not authenticated" message).
            toolset_type = instance_meta.get("toolsetType", "")
            if not config or not config.get("isAuthenticated", False):
                log.info(
                    "Toolset %s (%s) not authenticated for headless run; skipping",
                    instance_id, toolset_type,
                )
                continue

            toolset_configs[instance_id] = config
            agent_toolsets.append({
                "instanceId": instance_id,
                "instanceName": instance_meta.get("instanceName") or toolset_type,
                "name": toolset_type,
                "displayName": instance_meta.get("instanceName") or toolset_type,
                "type": toolset_type,
                "tools": instance_meta.get("tools", []),
                "selectedTools": instance_meta.get("selectedTools", []),
            })

        log.info(
            "Headless toolset resolution for task %s: "
            "requested=%d, authenticated=%d, names=%s",
            task.task_id, len(task.toolset_ids),
            len(agent_toolsets),
            [t.get("name", "") for t in agent_toolsets],
        )
        return agent_toolsets, toolset_configs

    async def build_context_and_tools(
        self,
        task: "TaskDefinition",
        *,
        graph_provider: "IGraphDBProvider",
        config_service: "ConfigurationService",
        blob_store: "BlobStorage | None" = None,
        logger: "Logger | None" = None,
    ) -> tuple["AgentContext", "ToolRegistry", str]:
        log = logger or logging.getLogger(__name__)
        llm, model_name = await self._resolve_llm(config_service, task.model_ref)

        # Resolve toolset credentials so the agent can authenticate to
        # external services (Slack, Jira, etc.) during a headless run.
        # For chat runs this is done in `api/routes/agent.py`; here we
        # mirror that logic for the scheduler path.
        agent_toolsets, toolset_configs = await self._resolve_toolset_credentials(
            task, config_service=config_service, log=log,
        )

        # Same reason as the toolset credentials above: without this the
        # dynamic `web_search`/`fetch_url` tools cannot be built at all, so a
        # scheduled research task silently answers from the model's memory.
        web_search_config = await resolve_default_web_search_config(config_service, log)
        log.info(
            "Headless web search for task %s: provider=%s",
            task.task_id,
            (web_search_config or {}).get("provider") or "none (web tools will not be built)",
        )

        context = build_headless_context(
            org_id=task.principal.org_id,
            user_id=task.principal.user_id,
            user_email=task.principal.user_email,
            is_service_account=task.principal.is_service_account,
            graph_provider=graph_provider,
            config_service=config_service,
            blob_store=blob_store,
            logger=logger,
            llm=llm,
            kb=task.collection_ids or None,
            agent_skills=task.skill_names or None,
            instructions=task.instructions,
            conversation_id=task.created_from_conversation_id,
            agent_toolsets=agent_toolsets or None,
            toolset_configs=toolset_configs or None,
            web_search_config=web_search_config,
        )
        tool_registry = await self._tool_loader.load(context, skip_apps={"coding_sandbox", "database_sandbox"})
        return context, tool_registry, model_name

    @staticmethod
    def _resolve_tool_names(
        task: "TaskDefinition", tool_registry: "ToolRegistry", log: "Logger",
    ) -> list[str]:
        """Resolve the task's declared tool names against the headless
        registry. Tools that exist only in the interactive chat context
        (e.g. ``knowledgegraph__*``, ``run_code``) are dropped with a
        warning — these are registered by ``factory.py`` but not by the
        headless ``tool_loader``.

        Raises ``ToolResolutionError`` only when **every** requested tool
        is unresolvable — partial resolution lets the run proceed with
        whatever subset IS available, which is the right trade-off for
        headless execution: the alternative (hard-fail) blocks every run
        whose creation-time chat happened to include interactive-only
        tools in its tool list.
        """
        available = set(tool_registry.names())

        if not task.tool_names:
            tool_names = tool_registry.names()
            log.warning(
                "TaskSpecAssembler: task_id=%s declares no tool_names; "
                "granting all %d session-default tool(s)",
                task.task_id, len(tool_names),
            )
            return tool_names

        resolved = [name for name in task.tool_names if name in available]
        missing = [name for name in task.tool_names if name not in available]

        if missing:
            suggestions = {
                name: difflib.get_close_matches(name, available, n=3, cutoff=0.6)
                for name in missing
            }
            if not resolved:
                log.error(
                    "TaskSpecAssembler: task_id=%s ALL declared tool(s) %s are "
                    "unresolvable (registry has %d tool(s)); failing the run",
                    task.task_id, missing, len(available),
                )
                raise ToolResolutionError(missing, {k: v for k, v in suggestions.items() if v})
            log.warning(
                "TaskSpecAssembler: task_id=%s dropping %d unresolvable tool(s) "
                "%s from declared set (these are only available in interactive "
                "context); proceeding with %d resolved tool(s) %s",
                task.task_id, len(missing), missing, len(resolved), resolved,
            )

        log.info(
            "TaskSpecAssembler.assemble: task_id=%s title=%r, "
            "requested_tools=%d, available_tools=%d, resolved_tools=%d, "
            "toolset_ids=%s, instructions=%r",
            task.task_id, task.title,
            len(task.tool_names), len(available), len(resolved),
            task.toolset_ids,
            task.instructions[:200] if task.instructions else None,
        )
        return resolved

    async def assemble(
        self,
        task: "TaskDefinition",
        *,
        graph_provider: "IGraphDBProvider",
        config_service: "ConfigurationService",
        blob_store: "BlobStorage | None" = None,
        logger: "Logger | None" = None,
        checkpoint_store: "CheckpointStore | None" = None,
        timeline_store: "TimelineStore | None" = None,
        is_dry_run: bool = False,
    ) -> tuple[Agent, Goal]:
        """Resolves the task's model + tools, builds the headless
        `AgentContext`, and returns a ready-to-run `Agent` bound to an
        `AgentRuntime` wired with the given durable stores. The caller
        (`runtime/executor.py`, Phase 4) owns actually calling
        `agent.run(goal)` (first attempt) or `agent.resume(checkpoint_id)`
        (crash recovery) -- this method is called fresh for BOTH, since an
        `Agent`/`AgentRuntime` pair holds no state that survives a process
        boundary; only the stores do.
        """
        context, tool_registry, model_name = await self.build_context_and_tools(
            task, graph_provider=graph_provider, config_service=config_service,
            blob_store=blob_store, logger=logger,
        )
        log = logger or logging.getLogger(__name__)
        tool_names = self._resolve_tool_names(task, tool_registry, log)

        spec = self.build_agent_spec(task, tool_names=tool_names, model_name=model_name)
        # `spec_factory` resolves a `TaskStep.domain` role string into a
        # concrete child `AgentSpec` -- only needed when `TaskDagLoop`
        # will actually call `schedule_spawn_batch` (which calls
        # `AgentRuntime.spec_for_role`); reusing the orchestrator's own
        # `domain_spec_factory` here means a task-engine child and an
        # orchestrator-spawned child are built identically (same system
        # prompt shape, same `ReActLoop`), not a parallel implementation
        # that could silently drift from it.
        spec_factory = (
            domain_spec_factory(
                provider="langchain", model_name=model_name,
                default_tool_names=tool_names, context=context,
            )
            if task.steps
            else None
        )
        runtime = self.build_runtime(
            tool_registry=tool_registry, checkpoint_store=checkpoint_store,
            timeline_store=timeline_store, llm=context.llm, model_name=model_name,
            spec_factory=spec_factory,
        )
        # `session_id=task.task_id`: stable across every run of the same
        # recurring task, so session-scoped state (HIL, ASK_ONCE approval
        # caching) is scoped per-task rather than per-run. Not yet
        # meaningfully exercised — `runtime.hil_store`/`approval_store`
        # aren't wired here (no unattended-run HIL policy exists yet; see
        # the task engine plan's Part C3) — but matches the identifier a
        # future HIL policy would need without a later signature change.
        # A dry run must not mutate anything external. The code-workflow path
        # enforces this in the broker; agent-task runs need the hook.
        if is_dry_run:
            from app.agents.agent_loop.hooks.task_side_effect import block_writes_for_dry_run
            block_writes_for_dry_run(runtime.hooks)

        agent = Agent(spec, runtime, session_id=task.task_id)
        return agent, self.build_goal(task)
