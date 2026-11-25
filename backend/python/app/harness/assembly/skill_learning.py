"""Skill learning on a durable run (DUR2, DUR1 open item 5): today's ``SkillLearning`` as a post-run step.

Today's loop runs ``SkillLearning`` as its POST_AGENT hook. The durable kernel dispatches no POST_AGENT, and the
learning writes outside the journal (the skill store, a writer sub-agent), so it runs as a ``PostRunStep``: the
kernel journals ``post_run.skill_learning`` as started before it runs and never runs it again for the run, so a
re-driven attempt can neither count nor write twice. The learning itself is the legacy middleware's ``settle``
with the legacy skill writer; this module only turns the run's ending into the ``AgentResult`` it reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.contracts.generated.enums import COMPLETED_OUTCOMES

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.agent_loop_lib.core.types import AgentResult
    from app.agent_loop_lib.hooks.middleware.builtin.skill_learning import SkillLearning
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
    from app.harness.assembly.context import RunContext
    from harness_core.kernel.durable.ports import PostRunStep, RunEnding
    from harness_core.models.transports.registry import TransportRegistry
    from harness_core.tools.registry import ToolRegistry

SKILL_LEARNING_STEP = "skill_learning"

# Today's loop completes a run that asked the user (needs_input) as a success, and fails a stopped one.
_SUCCEEDED = frozenset(COMPLETED_OUTCOMES)


def agent_result(ending: RunEnding, goal: str) -> AgentResult:
    """What today's loop hands POST_AGENT, from what the durable run journaled."""
    from app.agent_loop_lib.core.types import AgentResult, AgentTurn, Goal

    answer = ending.answer if ending.answer is not None else (ending.texts[-1] if ending.texts else "")
    return AgentResult(
        goal=Goal(description=goal),
        output=answer,
        turns=[AgentTurn(tool_calls=list(ending.tool_calls))],
        success=ending.outcome in _SUCCEEDED,
        error=ending.error_type,
        cancelled=ending.outcome == "stopped",
    )


class SkillLearningStep:
    """``PostRunStep``: the legacy learning, once per run."""

    key = SKILL_LEARNING_STEP

    def __init__(self, learning: SkillLearning, *, goal: str, run_id: str, session_id: str | None) -> None:
        self._learning = learning
        self._goal = goal
        self._run_id = run_id
        # Today's factory gives its agent no session id, so legacy records no activation counts; the
        # durable run passes the same, for parity (DUR2 open item).
        self._session_id = session_id

    async def run(self, ending: RunEnding) -> JsonValue:
        result = agent_result(ending, self._goal)
        await self._learning.settle(result, session_id=self._session_id, run_id=self._run_id)
        return {"success": result.success, "toolCalls": len(ending.tool_calls)}


def skill_learning_steps(
    manager: SkillManager | None,
    run: RunContext,
    *,
    run_kind: str,
    registry: ToolRegistry,
    transports: TransportRegistry,
    hooks: HookRegistry,
    provider: str,
    context_window: int | None = None,
) -> list[PostRunStep]:
    """The run's post-run learning, as today's factory registers it: for a run with skills, not for a child
    (legacy learns from the top-level agent's run only). The skill writer runs on the run's model and registry."""
    if manager is None or run_kind == "child" or run.sub_agent is not None:
        return []
    from app.agent_loop_lib.hooks.middleware.builtin.skill_learning import SkillLearning
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agents.agent_loop.skills_wiring import skill_writer
    from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer
    from app.harness.assembly.loop import model_name

    writers = AgentRuntime(
        transport_registry=transports,
        tool_registry=registry,
        hooks=hooks,
        summarizer=PipesHubToolSummarizer(),
        streamed_answer_argument=None,
        spawn_scheduler=None,
        derive_events=None,
    )
    learning = SkillLearning(
        manager=manager,
        spawn_skill_writer=skill_writer(
            writers, provider=provider, model_name=model_name(run), context_window=context_window,
        ),
        source_session_id=run.session_id or None,
        source_agent_key=run.agent.agent_key if run.agent is not None else None,
    )
    return [SkillLearningStep(learning, goal=run.query, run_id=run.run_id, session_id=None)]


__all__ = ["SKILL_LEARNING_STEP", "SkillLearningStep", "agent_result", "skill_learning_steps"]
