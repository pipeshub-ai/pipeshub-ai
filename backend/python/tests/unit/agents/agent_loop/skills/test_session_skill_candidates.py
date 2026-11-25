"""CLEANUP-G7: a skill candidate learned from a conversation carries it, and the conversation's erasure takes it.

A pending candidate holds a body and a trajectory summary drawn from the run, so it is the conversation's content.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.hooks.middleware.builtin.skill_learning import SkillLearning
from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
from app.agents.agent_loop.skills.graph_store import (
    GraphSkillStore,
    erase_session_skill_candidates,
)
from app.config.constants.arangodb import CollectionNames
from app.harness.assembly.skill_learning import SkillLearningStep, agent_result
from tests.unit.agent_loop_lib.hooks.middleware.builtin.test_skill_learning import (
    _FakeSkillManager,
)
from tests.unit.harness.assembly.test_durable_skill_learning import _ending
from tests.unit.services.artifact_registry.fakes import FakeGraphProvider

CANDIDATES = CollectionNames.AGENT_SKILL_CANDIDATES.value


def _candidate(candidate_id: str, session_id: str | None) -> SkillCandidate:
    return SkillCandidate(
        candidate_id=candidate_id, name=f"skill-{candidate_id}", description="when to use it", body="steps",
        source_session_id=session_id, source_trajectory_summary="what the run did", created_at="now",
    )


@pytest.mark.asyncio
async def test_cleanup_g7_a_queued_candidate_names_its_conversation_and_one_learned_elsewhere_names_none() -> None:
    graph = FakeGraphProvider()
    store = GraphSkillStore(graph, "org-1", "user-1")  # type: ignore[arg-type]
    await store.queue_candidate(_candidate("c1", "conv-1"))
    await store.queue_candidate(_candidate("c2", None))
    assert graph.nodes[CANDIDATES]["c1"]["sessionId"] == "conv-1"
    # Neo4j stores no nulls: no conversation, no field.
    assert "sessionId" not in graph.nodes[CANDIDATES]["c2"]


@pytest.mark.asyncio
async def test_cleanup_g7_a_conversations_erasure_takes_its_candidates_page_by_page_and_no_others() -> None:
    graph = FakeGraphProvider()
    mine = GraphSkillStore(graph, "org-1", "user-1")  # type: ignore[arg-type]
    other_org = GraphSkillStore(graph, "org-2", "user-2")  # type: ignore[arg-type]
    for i in range(3):
        await mine.queue_candidate(_candidate(f"c{i}", "conv-1"))
    await mine.queue_candidate(_candidate("other-chat", "conv-2"))
    await mine.queue_candidate(_candidate("no-chat", None))
    await other_org.queue_candidate(_candidate("other-org", "conv-1"))

    assert await erase_session_skill_candidates(graph, "org-1", "conv-1", page=2) == (2, True)  # type: ignore[arg-type]
    assert await erase_session_skill_candidates(graph, "org-1", "conv-1", page=2) == (1, False)  # type: ignore[arg-type]
    assert await erase_session_skill_candidates(graph, "org-1", "conv-1", page=2) == (0, False)  # type: ignore[arg-type]
    assert set(graph.nodes[CANDIDATES]) == {"other-chat", "no-chat", "other-org"}


@pytest.mark.asyncio
async def test_cleanup_g7_learning_names_the_conversation_without_counting_usage_against_it() -> None:
    manager = _FakeSkillManager()

    async def spawn(goal: str) -> Any:  # noqa: ANN401
        return agent_result(_ending(), goal)

    step = SkillLearningStep(
        SkillLearning(manager, spawn, source_session_id="conv-1"),  # type: ignore[arg-type]
        goal="deploy the service", run_id="run-1", session_id=None,
    )
    await step.run(_ending())
    assert manager.learn_calls == ["conv-1"]
    # Usage counts stay as the durable run counts them today (by the hook's session id: none here).
    assert manager.activations == []
