"""`SkillLearning` (POST_AGENT middleware) — the two responsibilities it
owns per finished run: (1) outcome feedback for every `load_skill` call
observed in the turns, and (2) routing `SkillManager.learn_from_execution`
candidates — spawning the `skill_writer` sub-agent for `approved`
candidates, leaving `pending` ones untouched (the manager already queued
those for human review)."""

from __future__ import annotations

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import AgentResult, AgentTurn, Goal
from app.agent_loop_lib.hooks.middleware.builtin.skill_learning import SkillLearning
from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate


class _FakeSkillManager:
    """Test double for `SkillManager`: only the surface `SkillLearning`
    actually calls (`record_activation`, `record_outcome`,
    `learn_from_execution`)."""

    def __init__(
        self,
        candidates: list[SkillCandidate] | None = None,
        *,
        learn_raises: bool = False,
    ) -> None:
        self._candidates = candidates or []
        self._learn_raises = learn_raises
        self.activations: list[tuple[str, str]] = []
        self.outcomes: list[tuple[str, str, bool]] = []
        self.learn_calls: list[str | None] = []

    async def record_activation(self, name: str, session_id: str) -> None:
        self.activations.append((name, session_id))

    async def record_outcome(self, name: str, session_id: str, success: bool, notes: str = "") -> None:
        self.outcomes.append((name, session_id, success))

    async def learn_from_execution(self, result, trajectory, decision_trace, session_id=None) -> list[SkillCandidate]:
        self.learn_calls.append(session_id)
        if self._learn_raises:
            raise RuntimeError("extraction backend unavailable")
        return self._candidates


def _candidate(candidate_id: str, status: str) -> SkillCandidate:
    return SkillCandidate(
        candidate_id=candidate_id,
        name=f"skill-{candidate_id}",
        description="when to use it",
        body="do the thing",
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _result(*, success: bool = True, loaded_skills: list[str] | None = None) -> AgentResult:
    tool_calls = [
        ToolCall(id=f"call-{i}", name="load_skill", arguments={"name": name})
        for i, name in enumerate(loaded_skills or [])
    ]
    turns = [AgentTurn(tool_calls=tool_calls)] if tool_calls else []
    return AgentResult(goal=Goal(description="deploy the service"), success=success, turns=turns)


async def _run(manager: _FakeSkillManager, result: AgentResult | None, session_id: str | None = "session-1") -> None:
    spawned: list[str] = []

    async def spawn(goal_description: str) -> AgentResult:
        spawned.append(goal_description)
        return _result()

    middleware = SkillLearning(manager, spawn)
    ctx = AgentLifecycleContext(result=result, session_id=session_id)

    called = {"next": False}

    async def next_fn() -> None:
        called["next"] = True

    await middleware(ctx, next_fn)
    assert called["next"], "middleware must always call next_fn(), even on no-op paths"
    return spawned


class TestOutcomeFeedback:
    async def test_records_activation_and_outcome_for_each_loaded_skill(self) -> None:
        manager = _FakeSkillManager()
        result = _result(loaded_skills=["deploy-service", "rollback-service"])

        await _run(manager, result)

        assert set(manager.activations) == {
            ("deploy-service", "session-1"),
            ("rollback-service", "session-1"),
        }
        assert set(manager.outcomes) == {
            ("deploy-service", "session-1", True),
            ("rollback-service", "session-1", True),
        }

    async def test_no_load_skill_calls_records_nothing(self) -> None:
        manager = _FakeSkillManager()
        result = _result(loaded_skills=[])

        await _run(manager, result)

        assert manager.activations == []
        assert manager.outcomes == []

    async def test_no_session_id_skips_outcome_recording(self) -> None:
        manager = _FakeSkillManager()
        result = _result(loaded_skills=["deploy-service"])

        await _run(manager, result, session_id=None)

        assert manager.activations == []
        assert manager.outcomes == []

    async def test_result_none_is_a_no_op(self) -> None:
        manager = _FakeSkillManager()
        spawned = await _run(manager, None)

        assert manager.activations == []
        assert manager.learn_calls == []
        assert spawned == []


class TestLearningLoopCandidateRouting:
    async def test_approved_candidate_spawns_skill_writer(self) -> None:
        manager = _FakeSkillManager([_candidate("cand-1", "approved")])
        result = _result(success=True)

        spawned = await _run(manager, result)

        assert manager.learn_calls == ["session-1"]
        assert len(spawned) == 1
        assert "skill-cand-1" in spawned[0]
        assert "skill_manage(action='create'" in spawned[0]

    async def test_pending_candidate_does_not_spawn_writer(self) -> None:
        """A `pending` candidate was already queued by the manager for
        human review (see `SkillManager.learn_from_execution`) — the
        middleware must not also spawn a writer for it."""
        manager = _FakeSkillManager([_candidate("cand-1", "pending")])
        result = _result(success=True)

        spawned = await _run(manager, result)

        assert spawned == []

    async def test_mixed_candidates_only_spawn_for_approved(self) -> None:
        manager = _FakeSkillManager([_candidate("cand-1", "approved"), _candidate("cand-2", "pending")])
        result = _result(success=True)

        spawned = await _run(manager, result)

        assert len(spawned) == 1
        assert "skill-cand-1" in spawned[0]

    async def test_unsuccessful_run_skips_learning_entirely(self) -> None:
        manager = _FakeSkillManager([_candidate("cand-1", "approved")])
        result = _result(success=False)

        spawned = await _run(manager, result)

        assert manager.learn_calls == []
        assert spawned == []

    async def test_extraction_failure_is_swallowed(self) -> None:
        manager = _FakeSkillManager(learn_raises=True)
        result = _result(success=True)

        spawned = await _run(manager, result)  # must not raise

        assert spawned == []

    async def test_spawn_failure_for_one_candidate_is_swallowed_and_others_still_spawn(self) -> None:
        manager = _FakeSkillManager([_candidate("cand-1", "approved"), _candidate("cand-2", "approved")])
        result = _result(success=True)

        spawned: list[str] = []
        raised = {"count": 0}

        async def spawn(goal_description: str) -> AgentResult:
            if raised["count"] == 0:
                raised["count"] += 1
                raise RuntimeError("writer sub-agent failed")
            spawned.append(goal_description)
            return _result()

        middleware = SkillLearning(manager, spawn)
        ctx = AgentLifecycleContext(result=result, session_id="session-1")

        async def next_fn() -> None:
            return None

        await middleware(ctx, next_fn)  # must not raise

        assert len(spawned) == 1
        assert "skill-cand-2" in spawned[0]
