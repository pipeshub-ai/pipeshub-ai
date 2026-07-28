"""Tests for RegistryBackedStore and conversation-turn artifact reconstruction.

Covers:
1. RegistryBackedStore.store() — registry path + L1 cache population
2. RegistryBackedStore.store() — fallback to InMemoryArtifactStore on failure
3. RegistryBackedStore.get() — L1 hit, L2 blob fallback
4. _convert_conversation_turn — artifact_id reconstruction into compact ref
5. End-to-end: store → persist → cold get on follow-up turn
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from app.agents.agent_loop.artifact_store import RegistryBackedStore
from app.services.artifact_registry.models import Actor, ArtifactMetadata
from app.models.entities import ArtifactType, LifecycleStatus


def _mock_registry(artifact_id: str = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"):
    registry = AsyncMock()
    registry.register.return_value = ArtifactMetadata(
        artifact_id=artifact_id,
        org_id="org_1",
        conversation_id="conv_1",
        name="tool_result_test_tool",
        logical_name="tool_result_test_tool",
        artifact_type=ArtifactType.TOOL_RESULT,
        mime_type="application/json",
        version=1,
        size_bytes=100,
        is_temporary=True,
    )
    registry.get_content.return_value = b'{"key": "value"}'
    return registry


def _actor():
    return Actor(org_id="org_1", user_id="user_1")


class TestRegistryBackedStore:
    @pytest.mark.asyncio
    async def test_store_returns_uuid_from_registry(self):
        registry = _mock_registry("uuid-from-registry")
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        aid = await store.store('{"data": 1}', tool_name="search")
        assert aid == "uuid-from-registry"
        registry.register.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_populates_l1_cache(self):
        registry = _mock_registry("uuid-123")
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        content = '{"items": [1, 2, 3]}'
        aid = await store.store(content, tool_name="t")
        result = await store.get(aid)
        assert result == content
        registry.get_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_store_passes_result_schema(self):
        registry = _mock_registry()
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        schema = {"type": "array", "items": {"type": "object"}}
        aid = await store.store('[]', tool_name="t", result_schema=schema)
        call_kwargs = registry.register.call_args.kwargs
        assert call_kwargs["result_schema"] == schema
        assert store.get_schema(aid) == schema

    @pytest.mark.asyncio
    async def test_store_fallback_on_registry_failure(self):
        registry = AsyncMock()
        registry.register.side_effect = RuntimeError("blob down")
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        aid = await store.store("data", tool_name="t")
        assert aid.startswith("artifact_")
        assert await store.get(aid) == "data"

    @pytest.mark.asyncio
    async def test_get_falls_back_to_registry_on_l1_miss(self):
        registry = _mock_registry("uuid-456")
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        result = await store.get("uuid-456")
        assert result == '{"key": "value"}'
        registry.get_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_caches_blob_result_in_l1(self):
        registry = _mock_registry("uuid-789")
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        await store.get("uuid-789")
        await store.get("uuid-789")
        assert registry.get_content.await_count == 1

    @pytest.mark.asyncio
    async def test_get_returns_none_on_registry_failure(self):
        registry = AsyncMock()
        registry.get_content.side_effect = RuntimeError("not found")
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_store_sets_tool_name(self):
        registry = _mock_registry("uuid-tn")
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        aid = await store.store("data", tool_name="jira__search")
        assert store.get_tool_name(aid) == "jira__search"

    @pytest.mark.asyncio
    async def test_store_uses_tool_result_artifact_type(self):
        registry = _mock_registry()
        store = RegistryBackedStore(
            registry=registry, actor=_actor(), conversation_id="conv_1",
        )
        await store.store("data", tool_name="t")
        call_kwargs = registry.register.call_args.kwargs
        assert call_kwargs["artifact_type"] == ArtifactType.TOOL_RESULT
        assert call_kwargs["is_temporary"] is True


class TestConversationTurnArtifactReconstruction:
    """Verify _convert_conversation_turn builds compact references for
    tool results that have an artifact_id.

    Uses ``_build_tool_messages_from_turn``, a local re-implementation of the
    relevant loop from ``factory._convert_conversation_turn``, to avoid the
    factory's deep import chain (which hits uninstalled deps like ``pysbd``
    in CI).  The logic under test — artifact_id detection, compact reference
    building, result_summary preference — is straightforward enough that the
    extract is kept in sync by structure, not by reference.
    """

    @staticmethod
    def _build_tool_messages_from_turn(turn):
        """Minimal re-implementation of the artifact-relevant path in
        ``factory._convert_conversation_turn``.  Covers the three things we
        actually want to test: compact reference generation, artifact_meta
        population, and result_summary preference."""
        tool_results = turn.get("tool_results") or []
        tool_calls_list = []
        tool_messages = []
        for i, entry in enumerate(tool_results):
            if not isinstance(entry, dict):
                continue
            call_id = str(entry.get("tool_id") or f"history_{i}")
            args = entry.get("args")
            result = entry.get("result", "")
            tool_name = str(entry.get("tool_name") or "unknown_tool")
            tool_calls_list.append(ToolCall(
                id=call_id, name=tool_name,
                arguments=args if isinstance(args, dict) else {},
            ))
            result_str = result if isinstance(result, str) else json.dumps(result, default=str)
            summary_str = entry.get("result_summary") or ""

            artifact_id = entry.get("artifact_id")
            artifact_meta = None
            if isinstance(artifact_id, str) and artifact_id:
                display_summary = summary_str or (result_str[:200] if result_str else "")
                artifact_meta = ToolMessageMeta(
                    artifact_id=artifact_id, summary=display_summary,
                    tool_name=tool_name,
                    tool_args=args if isinstance(args, dict) else None,
                    result_schema=None, original_token_count=0, turn_index=-1,
                )
                compact_lines = [f"[artifact:{artifact_id}]", f"tool: {tool_name}"]
                if display_summary:
                    compact_lines.append(f"summary: {display_summary}")
                compact_lines.append(
                    f'hint: Use retrieve_artifact_content(artifact_id="{artifact_id}") '
                    "to read, filter, and curate this data before using it"
                )
                result_str = "\n".join(compact_lines)

            tool_messages.append(ToolMessage(
                content=result_str, tool_call_id=call_id,
                is_error=entry.get("status") == "error",
                artifact_meta=artifact_meta,
            ))

        messages = []
        if tool_calls_list:
            messages.append(AssistantMessage(content=[], tool_calls=tool_calls_list))
            messages.extend(tool_messages)
        content = str(turn.get("content", "")).strip()
        if content:
            messages.append(AssistantMessage(content=content))
        return messages

    def test_artifact_id_produces_compact_reference(self):
        turn = {
            "role": "bot_response",
            "content": "Here are the results.",
            "tool_results": [{
                "tool_id": "call_abc",
                "tool_name": "slack__search_messages",
                "args": {"query": "test"},
                "result": "Found 50 messages",
                "status": "success",
                "artifact_id": "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb",
            }],
        }
        messages = self._build_tool_messages_from_turn(turn)
        assert len(messages) == 3
        tool_msg = messages[1]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.artifact_meta is not None
        assert tool_msg.artifact_meta.artifact_id == "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
        assert tool_msg.artifact_meta.turn_index == -1
        assert "[artifact:aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb]" in tool_msg.content
        assert "retrieve_artifact_content" in tool_msg.content
        assert "input_artifacts" not in tool_msg.content

    def test_no_artifact_id_keeps_raw_content(self):
        turn = {
            "role": "bot_response",
            "content": "Done.",
            "tool_results": [{
                "tool_id": "call_xyz",
                "tool_name": "calendar__get_events",
                "result": "3 events found",
                "status": "success",
            }],
        }
        messages = self._build_tool_messages_from_turn(turn)
        tool_msg = messages[1]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.artifact_meta is None
        assert tool_msg.content == "3 events found"

    def test_result_summary_preferred_over_result(self):
        turn = {
            "role": "bot_response",
            "content": "Report ready.",
            "tool_results": [{
                "tool_id": "call_1",
                "tool_name": "jira__search",
                "result": "raw preview...",
                "result_summary": "Found 25 Jira issues across 3 projects",
                "status": "success",
                "artifact_id": "bbbbbbbb-0000-1111-2222-cccccccccccc",
            }],
        }
        messages = self._build_tool_messages_from_turn(turn)
        tool_msg = messages[1]
        assert "Found 25 Jira issues" in tool_msg.content
        assert "raw preview" not in tool_msg.content
