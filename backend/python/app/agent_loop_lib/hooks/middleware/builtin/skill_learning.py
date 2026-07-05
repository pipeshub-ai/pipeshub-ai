from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import AgentResult
from app.agent_loop_lib.eval.decision_trace import build_decision_trace
from app.agent_loop_lib.eval.trajectory import entries_to_trajectory
from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate

if TYPE_CHECKING:
    from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
    from app.agent_loop_lib.modules.stores.timeline.base import TimelineStore

"""Skill learning loop (Hermes/MUSE-Autoskill pattern) — a POST_AGENT
middleware, the direct replacement for the old `SkillCreation`
(hooks/middleware/builtin/skill_creation.py, now retired). Two
responsibilities per run:

1. **Outcome feedback** — for every `load_skill` call observed in the run's
   tool calls, record an activation + outcome against `SkillManager`'s
   usage tracker. Closes the loop on skills that already exist: repeated
   poor outcomes surface via `SkillManager.evaluate_skill_health` for a
   future governance/refinement pass.
2. **Extraction** — on a successful run, ask `SkillManager.learn_from_execution`
   (extractor -> evaluator -> governor, all inside the manager) for
   quality-gated candidates. Per the "everything via tool calls" principle,
   this middleware never persists a skill itself: an "approved" candidate
   is handed to the `skill_writer` sub-agent, which authors the final
   SKILL.md and persists it by calling `skill_manage`; a "pending" candidate
   was already queued by the manager for human review and needs no further
   action here.

`timeline_store`, when wired, lets extraction see the richer trajectory +
decision-trace signal (`eval/trajectory.py` / `eval/decision_trace.py`);
without one, `SkillManager.learn_from_execution` still runs off the bare
`AgentResult` (the default `LLMSkillExtractor` tolerates both).
"""

logger = logging.getLogger(__name__)

SpawnSkillWriter = Callable[[str], Awaitable[AgentResult]]


def _skill_names_loaded(result: AgentResult) -> set[str]:
    return {
        call.arguments.get("name")
        for turn in result.turns
        for call in turn.tool_calls
        if call.name == "load_skill" and call.arguments.get("name")
    }


def _writer_goal(candidate: SkillCandidate) -> str:
    location = f" under category {candidate.category!r}" if candidate.category else ""
    return (
        f"Author and persist a reusable skill distilled from a real agent run{location}. "
        f"Proposed name: {candidate.name!r}. "
        f"Proposed description (when to use it): {candidate.description!r}. "
        f"Proposed instructions body:\n{candidate.body}\n\n"
        f"Context this was distilled from: {candidate.source_trajectory_summary}. "
        "Refine the name/description/body as needed, then call "
        f"skill_manage(action='create', name=..., description=..., body=..., "
        f"category={candidate.category!r}, subcategory={candidate.subcategory!r}, tags={candidate.tags!r}) "
        "exactly once to persist it."
    )


class SkillLearning:
    """Callable POST_AGENT middleware."""

    def __init__(
        self,
        manager: "SkillManager",
        spawn_skill_writer: SpawnSkillWriter,
        timeline_store: "TimelineStore | None" = None,
    ) -> None:
        self._manager = manager
        self._spawn = spawn_skill_writer
        self._timeline = timeline_store

    async def __call__(self, ctx: AgentLifecycleContext, next_fn) -> None:
        result = ctx.result
        if result is None:
            await next_fn()
            return

        await self._record_outcomes(result, ctx.session_id)
        if result.success:
            await self._learn(ctx, result)

        await next_fn()

    async def _record_outcomes(self, result: AgentResult, session_id: str | None) -> None:
        if session_id is None:
            return
        for skill_name in _skill_names_loaded(result):
            try:
                await self._manager.record_activation(skill_name, session_id)
                await self._manager.record_outcome(skill_name, session_id, result.success)
            except Exception:
                logger.exception("skill_learning: failed to record outcome for %r", skill_name)

    async def _learn(self, ctx: AgentLifecycleContext, result: AgentResult) -> None:
        trajectory = None
        decision_trace = None
        run_id = ctx.scope.identity.run_id if ctx.scope is not None else None
        if self._timeline is not None and run_id is not None:
            try:
                entries = await self._timeline.get_by_run(run_id)
                if entries:
                    trajectory = entries_to_trajectory(entries)
                    decision_trace = build_decision_trace(entries)
            except Exception:
                logger.exception("skill_learning: failed to build trajectory for run %s", run_id)

        try:
            candidates = await self._manager.learn_from_execution(
                result, trajectory, decision_trace, session_id=ctx.session_id,
            )
        except Exception:
            logger.exception("skill_learning: extraction failed")
            return

        for candidate in candidates:
            if candidate.status != "approved":
                continue  # already queued for human review by the manager
            try:
                await self._spawn(_writer_goal(candidate))
            except Exception:
                logger.exception("skill_learning: writer sub-agent failed for candidate %r", candidate.name)
