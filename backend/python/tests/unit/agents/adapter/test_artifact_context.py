"""Tests for `app.agents.agent_loop.hooks.artifact_context.artifact_context_reminder`
— the PRE_TURN hook that reminds the model about artifacts already
registered earlier in this conversation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.artifact_context import artifact_context_reminder
from app.models.entities import ArtifactType, LifecycleStatus
from app.services.artifact_registry import ArtifactMetadata


async def _noop_next() -> None:
    return None


def _make_context(**overrides) -> AgentContext:
    defaults = {
        "org_id": "org-1", "user_id": "user-1", "user_email": "u@example.com",
        "conversation_id": "conv-1", "previous_conversations": [{"turn": 1}],
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


def _make_turn_ctx(*, turn_index: int = 0, with_scope: bool = True):
    goal = SimpleNamespace(constraints=[])
    turn_scope = SimpleNamespace(run=SimpleNamespace(goal=goal)) if with_scope else None
    ctx = SimpleNamespace(turn_index=turn_index, scope=turn_scope)
    return ctx, goal


def _make_metadata(**overrides) -> ArtifactMetadata:
    defaults = {
        "artifact_id": "art-1", "org_id": "org-1", "conversation_id": "conv-1",
        "name": "chart.png", "logical_name": "chart.png", "artifact_type": ArtifactType.IMAGE,
        "mime_type": "image/png", "lifecycle_status": LifecycleStatus.PUBLISHED,
        "version": 1, "size_bytes": 9,
    }
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)


class TestArtifactContextReminder:
    async def test_appends_reminder_when_artifacts_exist(self, monkeypatch) -> None:
        context = _make_context(graph_provider=MagicMock(), blob_store=MagicMock())
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[_make_metadata()])
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx, goal = _make_turn_ctx()

        await artifact_context_reminder(context)(ctx, _noop_next)

        assert len(goal.constraints) == 1
        assert "chart.png" in goal.constraints[0]
        assert "art-1" in goal.constraints[0]

    async def test_includes_args_and_summary_from_conversation(self, monkeypatch) -> None:
        previous_conversations = [
            {
                "role": "bot_response",
                "content": "Found issues.",
                "tool_results": [
                    {
                        "tool_id": "c1",
                        "tool_name": "jira__search_issues",
                        "args": {"jql": "assignee = currentUser()", "maxResults": 50},
                        "result": "Found 47 issues",
                        "result_summary": "Found 47 issues",
                        "status": "success",
                        "artifact_id": "art-1",
                    },
                ],
            },
        ]
        context = _make_context(
            graph_provider=MagicMock(), blob_store=MagicMock(),
            previous_conversations=previous_conversations,
        )
        metadata = _make_metadata(
            artifact_id="art-1", name="tool_result_jira__search_issues.json",
            artifact_type=ArtifactType.OTHER, source_tool="jira__search_issues",
        )
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[metadata])
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx, goal = _make_turn_ctx()

        await artifact_context_reminder(context)(ctx, _noop_next)

        reminder = goal.constraints[0]
        assert "assignee = currentUser()" in reminder
        assert "maxResults" in reminder
        assert "Found 47 issues" in reminder

    async def test_includes_lineage_when_present(self, monkeypatch) -> None:
        context = _make_context(graph_provider=MagicMock(), blob_store=MagicMock())
        metadata = _make_metadata(derived_from_code_artifact_id="code-1")
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[metadata])
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx, goal = _make_turn_ctx()

        await artifact_context_reminder(context)(ctx, _noop_next)

        assert "derived_from_code_artifact_id=code-1" in goal.constraints[0]

    async def test_skips_when_not_first_turn(self, monkeypatch) -> None:
        context = _make_context(graph_provider=MagicMock(), blob_store=MagicMock())
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[_make_metadata()])
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx, goal = _make_turn_ctx(turn_index=1)

        await artifact_context_reminder(context)(ctx, _noop_next)

        assert goal.constraints == []
        registry.list_for_conversation.assert_not_awaited()

    async def test_skips_when_no_previous_conversations(self, monkeypatch) -> None:
        context = _make_context(
            graph_provider=MagicMock(), blob_store=MagicMock(), previous_conversations=[],
        )
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[_make_metadata()])
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx, goal = _make_turn_ctx()

        await artifact_context_reminder(context)(ctx, _noop_next)

        assert goal.constraints == []

    async def test_skips_when_registry_unavailable(self) -> None:
        context = _make_context()  # no graph_provider/blob_store -> artifact_registry is None
        ctx, goal = _make_turn_ctx()

        await artifact_context_reminder(context)(ctx, _noop_next)

        assert goal.constraints == []

    async def test_no_artifacts_leaves_constraints_untouched(self, monkeypatch) -> None:
        context = _make_context(graph_provider=MagicMock(), blob_store=MagicMock())
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[])
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx, goal = _make_turn_ctx()

        await artifact_context_reminder(context)(ctx, _noop_next)

        assert goal.constraints == []

    async def test_registry_failure_does_not_raise(self, monkeypatch) -> None:
        context = _make_context(graph_provider=MagicMock(), blob_store=MagicMock())
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx, goal = _make_turn_ctx()

        # Must not raise — a listing failure should never break the turn.
        await artifact_context_reminder(context)(ctx, _noop_next)
        assert goal.constraints == []
