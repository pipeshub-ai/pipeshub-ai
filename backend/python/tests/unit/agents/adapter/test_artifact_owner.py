"""Which identity an agent run makes and reads its artifacts as.

A service-account agent runs as its creator (``user_id``); the route sets
``artifact_owner_user_id`` to the person chatting so the files of the chat are
theirs. Every other run has no owner set and keeps using ``user_id``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agents.agent_loop.artifact_store import (
    RegistryBackedStore,
    build_artifact_store,
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.artifact_context import artifact_context_reminder
from app.agents.agent_loop.sandbox_bridge import (
    _capture_code_artifact,
    _register_and_deliver,
    _resolve_input_artifacts,
)
from app.models.entities import ArtifactType, LifecycleStatus
from app.services.artifact_registry import (
    ArtifactMetadata,
    artifact_owner,
    artifact_owner_id,
)

CREATOR = "u-alice"
CALLER = "u-bob"


def _context(**overrides: object) -> AgentContext:
    defaults: dict[str, Any] = {
        "org_id": "org-1", "user_id": CREATOR, "user_email": "a@example.com",
        "logger": MagicMock(), "conversation_id": "conv-1",
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


def _metadata() -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id="art-1", org_id="org-1", conversation_id="conv-1", name="out.csv",
        logical_name="out.csv", artifact_type=ArtifactType.OTHER, mime_type="text/csv",
        lifecycle_status=LifecycleStatus.PUBLISHED, version=1, size_bytes=3, document_id="doc-1",
    )


class TestArtifactOwner:
    def test_is_the_owner_when_one_is_set(self) -> None:
        actor = artifact_owner({"org_id": "org-1", "user_id": CREATOR, "artifact_owner_user_id": CALLER})
        assert (actor.org_id, actor.user_id) == ("org-1", CALLER)

    @pytest.mark.parametrize("state", [
        {"org_id": "org-1", "user_id": CREATOR},
        {"org_id": "org-1", "user_id": CREATOR, "artifact_owner_user_id": None},
        {"org_id": "org-1", "user_id": CREATOR, "artifact_owner_user_id": ""},
    ], ids=["absent", "none", "empty"])
    def test_is_the_run_user_otherwise(self, state: dict) -> None:
        assert artifact_owner(state).user_id == CREATOR


class TestArtifactOwnerId:
    def test_is_the_owner_when_one_is_set(self) -> None:
        assert artifact_owner_id({"user_id": CREATOR, "artifact_owner_user_id": CALLER}) == CALLER

    def test_is_the_run_user_otherwise(self) -> None:
        assert artifact_owner_id({"user_id": CREATOR, "artifact_owner_user_id": None}) == CREATOR

    def test_is_none_without_any_user_as_before(self) -> None:
        """The upload helpers were handed ``state.get("user_id")``; that stays exact."""
        assert artifact_owner_id({}) is None


class TestAgentContext:
    def test_carries_the_owner_from_chat_state(self) -> None:
        state = {"org_id": "org-1", "user_id": CREATOR, "artifact_owner_user_id": CALLER}

        context = AgentContext.from_chat_state(state)

        assert context.user_id == CREATOR
        assert context.artifact_owner_user_id == CALLER
        assert context.artifact_actor.user_id == CALLER
        assert context.tool_state["artifact_owner_user_id"] == CALLER

    def test_defaults_to_the_run_user_from_chat_state(self) -> None:
        context = AgentContext.from_chat_state({"org_id": "org-1", "user_id": CREATOR})

        assert context.artifact_actor.user_id == CREATOR

    def test_a_context_built_directly_seeds_the_run_user_as_owner(self) -> None:
        context = _context()

        assert context.artifact_actor.user_id == CREATOR
        assert context.tool_state["artifact_owner_user_id"] == CREATOR


class TestRunArtifacts:
    def test_the_tool_result_store_writes_as_the_owner(self) -> None:
        context = _context(artifact_owner_user_id=CALLER, graph_provider=MagicMock(), blob_store=MagicMock())

        store = build_artifact_store(context)

        assert isinstance(store, RegistryBackedStore)
        assert store._actor.user_id == CALLER

    async def test_the_reminder_lists_the_owners_artifacts(self, monkeypatch) -> None:
        context = _context(
            artifact_owner_user_id=CALLER, graph_provider=MagicMock(), blob_store=MagicMock(),
            previous_conversations=[{"turn": 1}],
        )
        registry = MagicMock()
        registry.list_for_conversation = AsyncMock(return_value=[])
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        turn = SimpleNamespace(turn_index=0, scope=SimpleNamespace(run=SimpleNamespace(goal=SimpleNamespace(constraints=[]))))

        async def _next() -> None:
            return None

        await artifact_context_reminder(context)(turn, _next)

        assert registry.list_for_conversation.await_args.kwargs["actor"].user_id == CALLER

    def test_captured_code_is_the_owners(self) -> None:
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(_metadata(), None))
        ctx = ToolCallContext(tool_path="/toolsets/coding_sandbox/run_code", tool_input={"code": "print(1)", "language": "python"})

        asyncio.run(_capture_code_artifact(_context(artifact_owner_user_id=CALLER), registry, ctx))

        assert registry.register_output.await_args.kwargs["actor"].user_id == CALLER

    def test_input_artifacts_are_read_as_the_owner(self) -> None:
        registry = MagicMock()
        registry.resolve = AsyncMock(return_value=_metadata())
        registry.get_content = AsyncMock(return_value=b"a,b")

        asyncio.run(_resolve_input_artifacts(_context(artifact_owner_user_id=CALLER), registry, ["art-1"]))

        assert registry.resolve.await_args.kwargs["actor"].user_id == CALLER

    def test_sandbox_outputs_are_the_owners(self) -> None:
        registry = AsyncMock()
        registry.register_output = AsyncMock(return_value=(_metadata(), None))
        registry.get_download_url = AsyncMock(return_value="https://signed/out.csv")

        asyncio.run(_register_and_deliver(
            _context(artifact_owner_user_id=CALLER), registry, [("out.csv", b"a,b")], {"out.csv": "out.csv"}, None,
            source_tool="coding_sandbox.run_code", code_artifact_id=None, code_artifact_version=None,
        ))

        assert registry.register_output.await_args.kwargs["actor"].user_id == CALLER


class TestToolLoader:
    def test_the_sql_tool_exports_for_the_owner_and_checks_access_as_the_run_user(self) -> None:
        from app.agents.agent_loop.tool_loader import _build_dynamic_tools

        context = AgentContext.from_chat_state({
            "org_id": "org-1", "user_id": CREATOR, "artifact_owner_user_id": CALLER,
            "config_service": MagicMock(), "has_sql_connector": True, "has_sql_knowledge": True,
            "allowed_sql_connector_ids": frozenset({"conn-1"}), "conversation_id": "conv-1",
        })
        with patch("app.utils.execute_query.create_execute_query_tool", side_effect=RuntimeError("built")) as factory:
            _build_dynamic_tools(context)

        kwargs = factory.call_args.kwargs
        assert kwargs["user_id"] == CREATOR
        assert kwargs["artifact_owner_user_id"] == CALLER
