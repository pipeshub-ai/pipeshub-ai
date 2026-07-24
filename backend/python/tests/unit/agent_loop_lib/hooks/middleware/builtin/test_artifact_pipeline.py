"""Comprehensive tests for the context engineering artifact pipeline.

Covers:
1. InMemoryArtifactStore — LRU eviction, TTL, store/get
2. shape_artifact_registration — threshold, metadata enrichment, schema
3. Compact reference format — tool_name, tool_args, dual hints
4. Sandbox bridge InMemoryArtifactStore resolution
5. Budget reduction artifact skip
6. Synthesis guard artifact-aware clearing
7. Loop compaction artifact index preservation
8. Integration: registration → compaction → retrieval
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    SystemMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_tokens
from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
    _compact_reference,
    shape_artifact_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.artifact_registration import (
    InMemoryArtifactStore,
    shape_artifact_registration,
)
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    shape_budget_reduction,
)
from app.agent_loop_lib.hooks.middleware.builtin.loop_compaction import (
    shape_loop_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.synthesis_guard import (
    shape_synthesis_guard,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
from app.agent_loop_lib.tools.builtin.data.retrieve_artifact import (
    RetrieveArtifactContentTool,
)


# ─── Helpers ───


def _budget(max_tokens: int = 10_000) -> ContextBudget:
    return ContextBudget(max_tokens=max_tokens, model="test")


def _tool_msg(
    content: str,
    call_id: str = "tc_1",
    meta: ToolMessageMeta | None = None,
) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, artifact_meta=meta)


def _meta(
    artifact_id: str = "artifact_1",
    summary: str = "data...",
    tool_name: str = "internal_search",
    tool_args: dict | None = None,
    result_schema: dict | None = None,
    original_token_count: int = 5000,
    turn_index: int = 0,
) -> ToolMessageMeta:
    return ToolMessageMeta(
        artifact_id=artifact_id,
        summary=summary,
        tool_name=tool_name,
        tool_args=tool_args,
        result_schema=result_schema,
        original_token_count=original_token_count,
        turn_index=turn_index,
    )


async def _run_pre_model(middleware, messages, budget=None, turn_index=0):
    ctx = ModelCallContext(
        messages=list(messages),
        budget=budget or _budget(),
        turn_index=turn_index,
    )
    called = []

    async def next_fn():
        called.append(True)

    await middleware(ctx, next_fn)
    assert called, "next_fn was not called"
    return ctx.messages


@dataclass
class FakeToolResultContext:
    tool_path: str = "/toolsets/search/internal_search"
    tool_use_id: Any = field(default_factory=uuid4)
    tool_response: Any = None
    caller: str = "agent"
    session_id: str | None = None
    tags: tuple = ()
    scope: Any = None
    metadata: dict = field(default_factory=dict)

    @dataclass
    class _Response:
        success: bool = True
        data: Any = ""

    @classmethod
    def ok(cls, data: str, tool_path: str = "/toolsets/search/internal_search", **meta_kwargs):
        ctx = cls(tool_path=tool_path)
        ctx.tool_response = cls._Response(success=True, data=data)
        ctx.metadata = meta_kwargs
        return ctx

    @classmethod
    def error(cls, data: str = "error"):
        ctx = cls()
        ctx.tool_response = cls._Response(success=False, data=data)
        return ctx


# ═══════════════════════════════════════════════════════
# 1. InMemoryArtifactStore
# ═══════════════════════════════════════════════════════


class TestInMemoryArtifactStore:
    @pytest.mark.asyncio
    async def test_store_and_get(self):
        store = InMemoryArtifactStore()
        aid = await store.store("hello world", tool_name="test")
        assert "-" in aid and len(aid) == 36  # UUID4 format
        assert await store.get(aid) == "hello world"

    @pytest.mark.asyncio
    async def test_unique_ids(self):
        store = InMemoryArtifactStore()
        a1 = await store.store("one", tool_name="t")
        a2 = await store.store("two", tool_name="t")
        a3 = await store.store("three", tool_name="t")
        assert len({a1, a2, a3}) == 3  # all unique

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self):
        store = InMemoryArtifactStore()
        assert await store.get("00000000-0000-0000-0000-000000000000") is None

    @pytest.mark.asyncio
    async def test_lru_eviction(self):
        store = InMemoryArtifactStore(maxsize=2)
        a1 = await store.store("a", tool_name="t")
        a2 = await store.store("b", tool_name="t")
        a3 = await store.store("c", tool_name="t")
        assert await store.get(a1) is None
        assert await store.get(a2) == "b"
        assert await store.get(a3) == "c"

    @pytest.mark.asyncio
    async def test_ttl_expiry(self):
        store = InMemoryArtifactStore(ttl_seconds=0.01)
        aid = await store.store("data", tool_name="t")
        time.sleep(0.02)
        assert await store.get(aid) is None

    @pytest.mark.asyncio
    async def test_schema_storage_and_retrieval(self):
        store = InMemoryArtifactStore()
        schema = {"type": "array", "items": {"type": "object"}}
        aid = await store.store("data", tool_name="search", result_schema=schema)
        assert store.get_schema(aid) == schema
        assert store.get_tool_name(aid) == "search"

    @pytest.mark.asyncio
    async def test_schema_none_when_not_stored(self):
        store = InMemoryArtifactStore()
        aid = await store.store("data", tool_name="t")
        assert store.get_schema(aid) is None

    @pytest.mark.asyncio
    async def test_schema_evicted_with_data(self):
        store = InMemoryArtifactStore(maxsize=1)
        a1 = await store.store("a", tool_name="t", result_schema={"type": "string"})
        await store.store("b", tool_name="t2")
        assert store.get_schema(a1) is None
        assert store.get_tool_name(a1) is None

    @pytest.mark.asyncio
    async def test_get_refreshes_lru(self):
        store = InMemoryArtifactStore(maxsize=2)
        a1 = await store.store("a", tool_name="t")
        a2 = await store.store("b", tool_name="t")
        await store.get(a1)
        await store.store("c", tool_name="t")
        assert await store.get(a1) == "a"
        assert await store.get(a2) is None


# ═══════════════════════════════════════════════════════
# 2. shape_artifact_registration
# ═══════════════════════════════════════════════════════


class TestArtifactRegistration:
    @pytest.mark.asyncio
    async def test_below_threshold_no_artifact(self):
        store = InMemoryArtifactStore()
        middleware = shape_artifact_registration(store=store, threshold_tokens=4000)
        ctx = FakeToolResultContext.ok("short result")

        async def noop():
            pass

        await middleware(ctx, noop)
        assert "artifact_meta" not in ctx.metadata

    @pytest.mark.asyncio
    async def test_above_threshold_creates_artifact(self):
        store = InMemoryArtifactStore()
        middleware = shape_artifact_registration(store=store, threshold_tokens=10)
        large_content = "x" * 500
        ctx = FakeToolResultContext.ok(large_content)

        async def noop():
            pass

        await middleware(ctx, noop)
        assert "artifact_meta" in ctx.metadata
        meta = ctx.metadata["artifact_meta"]
        assert "-" in meta.artifact_id and len(meta.artifact_id) == 36
        assert await store.get(meta.artifact_id) == large_content

    @pytest.mark.asyncio
    async def test_metadata_includes_tool_name(self):
        store = InMemoryArtifactStore()
        middleware = shape_artifact_registration(store=store, threshold_tokens=10)
        ctx = FakeToolResultContext.ok(
            "x" * 500,
            tool_path="/toolsets/jira/search_issues",
        )

        async def noop():
            pass

        await middleware(ctx, noop)
        meta = ctx.metadata["artifact_meta"]
        assert meta.tool_name == "jira__search_issues"

    @pytest.mark.asyncio
    async def test_metadata_includes_tool_args(self):
        store = InMemoryArtifactStore()
        middleware = shape_artifact_registration(store=store, threshold_tokens=10)
        ctx = FakeToolResultContext.ok(
            "x" * 500,
            _result_accum_args={"query": "open bugs", "project": "PIPE"},
        )

        async def noop():
            pass

        await middleware(ctx, noop)
        meta = ctx.metadata["artifact_meta"]
        assert meta.tool_args == {"query": "open bugs", "project": "PIPE"}

    @pytest.mark.asyncio
    async def test_error_response_skipped(self):
        store = InMemoryArtifactStore()
        middleware = shape_artifact_registration(store=store, threshold_tokens=10)
        ctx = FakeToolResultContext.error("x" * 500)

        async def noop():
            pass

        await middleware(ctx, noop)
        assert "artifact_meta" not in ctx.metadata

    @pytest.mark.asyncio
    async def test_summary_truncated_at_preview_chars(self):
        store = InMemoryArtifactStore()
        middleware = shape_artifact_registration(
            store=store, threshold_tokens=10, preview_chars=50
        )
        ctx = FakeToolResultContext.ok("a" * 500)

        async def noop():
            pass

        await middleware(ctx, noop)
        meta = ctx.metadata["artifact_meta"]
        assert len(meta.summary) <= 54  # 50 chars + "..."

    @pytest.mark.asyncio
    async def test_schema_resolution(self):
        store = InMemoryArtifactStore()

        def resolver(name):
            if name == "internal_search":
                return {"type": "array", "items": {"type": "object"}}
            return None

        middleware = shape_artifact_registration(
            store=store, threshold_tokens=10, resolve_schema=resolver
        )
        ctx = FakeToolResultContext.ok("x" * 500)

        async def noop():
            pass

        await middleware(ctx, noop)
        meta = ctx.metadata["artifact_meta"]
        assert meta.result_schema == {"type": "array", "items": {"type": "object"}}

    @pytest.mark.asyncio
    async def test_retrieve_artifact_content_exempt_from_registration(self):
        """retrieve_artifact_content results must not be re-registered as new artifacts."""
        store = InMemoryArtifactStore()
        middleware = shape_artifact_registration(store=store, threshold_tokens=10)
        ctx = FakeToolResultContext.ok(
            "x" * 500,
            tool_path="/tools/data/retrieve_artifact_content",
        )

        async def noop():
            pass

        await middleware(ctx, noop)
        assert "artifact_meta" not in ctx.metadata

    @pytest.mark.asyncio
    async def test_4k_threshold_filters_small_results(self):
        """Results between 2000-4000 tokens should NOT create artifacts at 4K threshold."""
        store = InMemoryArtifactStore()
        middleware = shape_artifact_registration(store=store, threshold_tokens=4000)
        medium_content = "word " * 2500  # ~2500 tokens, under 4K
        ctx = FakeToolResultContext.ok(medium_content)

        async def noop():
            pass

        await middleware(ctx, noop)
        assert "artifact_meta" not in ctx.metadata


# ═══════════════════════════════════════════════════════
# 3. Compact Reference Format
# ═══════════════════════════════════════════════════════


class TestCompactReferenceFormat:
    def test_includes_artifact_id(self):
        msg = _tool_msg("data", meta=_meta(artifact_id="artifact_5"))
        ref = _compact_reference(msg)
        assert "[artifact:artifact_5]" in ref

    def test_includes_tool_name(self):
        msg = _tool_msg("data", meta=_meta(tool_name="search_issues"))
        ref = _compact_reference(msg)
        assert "tool: search_issues" in ref

    def test_includes_tool_args(self):
        msg = _tool_msg(
            "data",
            meta=_meta(tool_args={"query": "open bugs"}),
        )
        ref = _compact_reference(msg)
        assert "args:" in ref
        assert "open bugs" in ref

    def test_truncates_long_args(self):
        long_args = {"query": "x" * 300}
        msg = _tool_msg("data", meta=_meta(tool_args=long_args))
        ref = _compact_reference(msg)
        assert "..." in ref
        args_line = [l for l in ref.split("\n") if l.startswith("args:")][0]
        assert len(args_line) < 220

    def test_hint_without_schema_suggests_retrieve(self):
        msg = _tool_msg("data", meta=_meta(artifact_id="artifact_3"))
        ref = _compact_reference(msg)
        assert "retrieve_artifact_content" in ref
        assert '"artifact_3"' in ref

    def test_hint_with_schema_suggests_retrieve(self):
        msg = _tool_msg(
            "data",
            meta=_meta(artifact_id="artifact_4", result_schema={"items": [{"id": "string"}]}),
        )
        ref = _compact_reference(msg)
        assert "retrieve_artifact_content" in ref
        assert '"artifact_4"' in ref

    def test_includes_schema_when_present(self):
        msg = _tool_msg(
            "data",
            meta=_meta(result_schema={"type": "array"}),
        )
        ref = _compact_reference(msg)
        assert '"type": "array"' in ref

    def test_no_schema_line_when_absent(self):
        msg = _tool_msg("data", meta=_meta(result_schema=None))
        ref = _compact_reference(msg)
        assert "schema:" not in ref

    def test_includes_original_token_count(self):
        msg = _tool_msg("data", meta=_meta(original_token_count=8500))
        ref = _compact_reference(msg)
        assert "original_tokens: 8500" in ref

    def test_includes_tool_call_id(self):
        msg = _tool_msg("data", call_id="toolu_abc123", meta=_meta())
        ref = _compact_reference(msg)
        assert "tool_call_id: toolu_abc123" in ref

    def test_no_tool_line_when_empty(self):
        msg = _tool_msg("data", meta=_meta(tool_name=""))
        ref = _compact_reference(msg)
        assert "tool:" not in ref

    def test_no_args_line_when_none(self):
        msg = _tool_msg("data", meta=_meta(tool_args=None))
        ref = _compact_reference(msg)
        assert "args:" not in ref

    def test_returns_content_when_no_meta(self):
        msg = _tool_msg("original content")
        ref = _compact_reference(msg)
        assert ref == "original content"


# ═══════════════════════════════════════════════════════
# 4. Sandbox Bridge InMemoryArtifactStore Resolution
# ═══════════════════════════════════════════════════════


class TestSandboxBridgeResolution:
    @pytest.mark.asyncio
    async def test_inmemory_store_resolves_artifact(self):
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        store = InMemoryArtifactStore()
        aid = await store.store('{"results": [1, 2, 3]}', tool_name="search")

        @dataclass
        class FakeContext:
            org_id: str = "org1"
            user_id: str = "user1"
            conversation_id: str = "conv1"

        ctx = FakeContext()
        files, resolved, missing = await _resolve_input_artifacts(
            ctx, None, [aid], inmemory_store=store,
        )
        assert len(resolved) == 1
        assert resolved[0]["ref"] == aid
        assert resolved[0]["artifact_id"] == aid
        assert f"input/artifacts/{aid}.json" in files
        content = files[f"input/artifacts/{aid}.json"]
        assert json.loads(content.decode("utf-8")) == {"results": [1, 2, 3]}
        assert len(missing) == 0

    @pytest.mark.asyncio
    async def test_inmemory_miss_falls_through_to_missing(self):
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        store = InMemoryArtifactStore()

        @dataclass
        class FakeContext:
            org_id: str = "org1"
            user_id: str = "user1"
            conversation_id: str = "conv1"

        ctx = FakeContext()
        fake_id = "00000000-0000-0000-0000-000000000099"
        files, resolved, missing = await _resolve_input_artifacts(
            ctx, None, [fake_id], inmemory_store=store,
        )
        assert len(resolved) == 0
        assert missing == [fake_id]

    @pytest.mark.asyncio
    async def test_mixed_resolution(self):
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        store = InMemoryArtifactStore()
        aid = await store.store("found data", tool_name="search")

        @dataclass
        class FakeContext:
            org_id: str = "org1"
            user_id: str = "user1"
            conversation_id: str = "conv1"

        fake_missing = "00000000-0000-0000-0000-missing00000"
        ctx = FakeContext()
        files, resolved, missing = await _resolve_input_artifacts(
            ctx, None, [aid, fake_missing], inmemory_store=store,
        )
        assert len(resolved) == 1
        assert resolved[0]["ref"] == aid
        assert missing == [fake_missing]

    @pytest.mark.asyncio
    async def test_empty_refs_skipped(self):
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        store = InMemoryArtifactStore()

        @dataclass
        class FakeContext:
            org_id: str = "org1"
            user_id: str = "user1"
            conversation_id: str = "conv1"

        ctx = FakeContext()
        files, resolved, missing = await _resolve_input_artifacts(
            ctx, None, ["", "   ", None], inmemory_store=store,
        )
        assert len(resolved) == 0
        assert len(missing) == 0

    @pytest.mark.asyncio
    async def test_inmemory_store_stages_schema_file(self):
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        store = InMemoryArtifactStore()
        schema = {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}}}}
        aid = await store.store('{"results": []}', tool_name="search", result_schema=schema)

        @dataclass
        class FakeContext:
            org_id: str = "org1"
            user_id: str = "user1"
            conversation_id: str = "conv1"

        ctx = FakeContext()
        files, resolved, missing = await _resolve_input_artifacts(
            ctx, None, [aid], inmemory_store=store,
        )
        assert f"input/artifacts/{aid}.json" in files
        assert f"input/artifacts/{aid}.schema.json" in files
        schema_content = json.loads(files[f"input/artifacts/{aid}.schema.json"].decode("utf-8"))
        assert schema_content["schema"] == schema
        assert resolved[0].get("schema_path") == f"input/artifacts/{aid}.schema.json"

    @pytest.mark.asyncio
    async def test_content_staged_as_utf8_bytes(self):
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        store = InMemoryArtifactStore()
        aid = await store.store("unicode: café ñ 日本語", tool_name="search")

        @dataclass
        class FakeContext:
            org_id: str = "org1"
            user_id: str = "user1"
            conversation_id: str = "conv1"

        ctx = FakeContext()
        files, resolved, missing = await _resolve_input_artifacts(
            ctx, None, [aid], inmemory_store=store,
        )
        content = files[f"input/artifacts/{aid}.json"]
        assert isinstance(content, bytes)
        assert content.decode("utf-8") == "unicode: café ñ 日本語"


# ═══════════════════════════════════════════════════════
# 5. Budget Reduction — Artifact Skip
# ═══════════════════════════════════════════════════════


class TestBudgetReductionArtifactHandling:
    @pytest.mark.asyncio
    async def test_artifact_messages_truncated_like_others(self):
        """L1 budget_reduction caps artifact-bearing messages — full content
        is safely in the artifact store, so the model gets a preview."""
        large_content = "x" * 100_000
        meta = _meta(original_token_count=20000)
        messages = [
            SystemMessage(content="sys"),
            _tool_msg(large_content, meta=meta),
        ]
        shaper = shape_budget_reduction(max_result_chars=1000)
        result = await _run_pre_model(shaper, messages)

        tool_msg = [m for m in result if isinstance(m, ToolMessage)][0]
        assert len(tool_msg.content) < 2000
        assert tool_msg.artifact_meta is not None

    @pytest.mark.asyncio
    async def test_small_artifact_messages_untouched(self):
        """Artifact messages under the cap are not truncated."""
        small_content = "x" * 500
        meta = _meta(original_token_count=100)
        messages = [
            SystemMessage(content="sys"),
            _tool_msg(small_content, meta=meta),
        ]
        shaper = shape_budget_reduction(max_result_chars=1000)
        result = await _run_pre_model(shaper, messages)

        tool_msg = [m for m in result if isinstance(m, ToolMessage)][0]
        assert len(tool_msg.content) == 500
        assert tool_msg.artifact_meta is not None

    @pytest.mark.asyncio
    async def test_non_artifact_messages_truncated(self):
        """L1 budget_reduction truncates non-artifact messages."""
        large_content = "x" * 100_000
        messages = [
            SystemMessage(content="sys"),
            _tool_msg(large_content),
        ]
        shaper = shape_budget_reduction(max_result_chars=1000)
        result = await _run_pre_model(shaper, messages)

        tool_msg = [m for m in result if isinstance(m, ToolMessage)][0]
        assert len(tool_msg.content) < 2000


# ═══════════════════════════════════════════════════════
# 6. Synthesis Guard — Artifact-Aware Clearing
# ═══════════════════════════════════════════════════════


class TestSynthesisGuardArtifactAware:
    @pytest.mark.asyncio
    async def test_artifact_messages_get_compact_ref_not_generic_cleared(self):
        """Synthesis guard uses compact_reference for artifact messages, not generic [cleared]."""
        meta = _meta(artifact_id="artifact_7", tool_name="web_search")
        messages = [
            SystemMessage(content="sys"),
            _tool_msg("x" * 50_000, call_id="tc_1", meta=meta),
            _tool_msg("y" * 50_000, call_id="tc_2", meta=_meta(artifact_id="artifact_8")),
            _tool_msg("recent", call_id="tc_3"),
        ]
        shaper = shape_synthesis_guard(keep_last_n_tool_results=1)
        result = await _run_pre_model(shaper, messages, _budget(3_000))

        tool_msgs = [m for m in result if isinstance(m, ToolMessage)]
        cleared_msgs = [m for m in tool_msgs if "[artifact:" in m.content]
        generic_cleared = [m for m in tool_msgs if m.content == "[cleared by synthesis_guard]"]
        assert len(cleared_msgs) == 2
        assert "artifact_7" in cleared_msgs[0].content
        assert len(generic_cleared) == 0


# ═══════════════════════════════════════════════════════
# 7. Loop Compaction — Artifact Index Preservation
# ═══════════════════════════════════════════════════════


class TestLoopCompactionArtifactIndex:
    @pytest.mark.asyncio
    async def test_artifact_ids_in_summary(self):
        """Loop compaction preserves artifact IDs in the summary message."""
        meta1 = _meta(artifact_id="artifact_1", turn_index=0)
        meta2 = _meta(artifact_id="artifact_2", turn_index=1)
        messages = [
            SystemMessage(content="sys"),
            UserMessage(content="query 1"),
            AssistantMessage(
                content=[TextPart(text="calling")],
                tool_calls=[ToolCall(id="tc_1", name="search", arguments={})],
            ),
            _tool_msg("x" * 5000, call_id="tc_1", meta=meta1),
            UserMessage(content="query 2"),
            AssistantMessage(
                content=[TextPart(text="calling")],
                tool_calls=[ToolCall(id="tc_2", name="web", arguments={})],
            ),
            _tool_msg("y" * 5000, call_id="tc_2", meta=meta2),
            # recent messages (kept)
            UserMessage(content="recent 1"),
            AssistantMessage(content="recent response 1"),
            UserMessage(content="recent 2"),
            AssistantMessage(content="recent response 2"),
            UserMessage(content="recent 3"),
            AssistantMessage(content="recent response 3"),
        ]
        shaper = shape_loop_compaction(
            compact_every_n_turns=5, keep_recent=6, trigger_ratio=0.0
        )
        result = await _run_pre_model(shaper, messages, _budget(3_000), turn_index=5)

        compaction_msgs = [
            m for m in result
            if isinstance(m, UserMessage)
            and isinstance(m.content, str)
            and "[Loop compaction:" in m.content
        ]
        assert len(compaction_msgs) == 1
        summary = compaction_msgs[0].content
        assert "artifact_1" in summary
        assert "artifact_2" in summary


# ═══════════════════════════════════════════════════════
# 8. RetrieveArtifactContentTool
# ═══════════════════════════════════════════════════════


class TestRetrieveArtifactContentTool:
    @pytest.mark.asyncio
    async def test_retrieve_existing_artifact(self):
        store = InMemoryArtifactStore()
        aid = await store.store("full data content here", tool_name="search")

        tool = RetrieveArtifactContentTool(store=store)
        output = await tool.execute(artifact_id=aid)
        assert output.success is True
        assert output.data == "full data content here"

    @pytest.mark.asyncio
    async def test_retrieve_nonexistent_artifact(self):
        store = InMemoryArtifactStore()
        tool = RetrieveArtifactContentTool(store=store)
        output = await tool.execute(artifact_id="00000000-0000-0000-0000-000000000000")
        assert output.success is False
        assert "not found" in output.error.lower()

    @pytest.mark.asyncio
    async def test_max_lines_limit(self):
        store = InMemoryArtifactStore()
        aid = await store.store("line1\nline2\nline3\nline4\nline5", tool_name="t")

        tool = RetrieveArtifactContentTool(store=store)
        output = await tool.execute(artifact_id=aid, max_lines=2)
        assert output.success is True
        assert output.data.count("\n") <= 2

    @pytest.mark.asyncio
    async def test_truncation_hint(self):
        store = InMemoryArtifactStore()
        aid = await store.store("x" * 200_000, tool_name="t")

        tool = RetrieveArtifactContentTool(store=store, max_content_tokens=100)
        output = await tool.execute(artifact_id=aid)
        assert output.success is True
        assert "truncated" in output.data
        assert "Filter and curate" in output.data


# ═══════════════════════════════════════════════════════
# 9. Integration: Registration → Compaction → Retrieval
# ═══════════════════════════════════════════════════════


class TestEndToEndArtifactFlow:
    @pytest.mark.asyncio
    async def test_register_compact_then_retrieve(self):
        """Full flow: large tool result → artifact registration → compaction → retrieve."""
        store = InMemoryArtifactStore()
        large_data = json.dumps({"results": [{"title": f"item_{i}"} for i in range(100)]})

        # Phase 1: Registration (POST_TOOL_USE)
        reg_middleware = shape_artifact_registration(store=store, threshold_tokens=10)
        ctx = FakeToolResultContext.ok(
            large_data,
            _result_accum_args={"query": "test"},
        )

        async def noop():
            pass

        await reg_middleware(ctx, noop)
        assert "artifact_meta" in ctx.metadata
        meta = ctx.metadata["artifact_meta"]
        artifact_id = meta.artifact_id

        # Phase 2: Compaction (PRE_MODEL, 2 turns later — past keep window)
        tool_msg = ToolMessage(
            content=large_data,
            tool_call_id="tc_1",
            artifact_meta=meta,
        )
        messages = [
            SystemMessage(content="sys"),
            UserMessage(content="original query"),
            AssistantMessage(
                content=[TextPart(text="calling")],
                tool_calls=[ToolCall(id="tc_1", name="search", arguments={})],
            ),
            tool_msg,
            UserMessage(content="follow-up"),
        ]
        compact_middleware = shape_artifact_compaction(trigger_ratio=0.0)
        result = await _run_pre_model(compact_middleware, messages, _budget(2_000), turn_index=2)

        compacted_msg = [m for m in result if isinstance(m, ToolMessage)][0]
        assert f"[artifact:{artifact_id}]" in compacted_msg.content
        assert "retrieve_artifact_content" in compacted_msg.content

        # Phase 3: Retrieval
        retrieve_tool = RetrieveArtifactContentTool(store=store)
        output = await retrieve_tool.execute(artifact_id=artifact_id)
        assert output.success is True
        recovered = json.loads(output.data)
        assert len(recovered["results"]) == 100

    @pytest.mark.asyncio
    async def test_register_compact_then_sandbox_resolve(self):
        """Full flow: large tool result → artifact → compaction → sandbox bridge resolution."""
        from app.agents.agent_loop.sandbox_bridge import _resolve_input_artifacts

        store = InMemoryArtifactStore()
        large_data = json.dumps({"sales": [i * 100 for i in range(200)]})

        # Phase 1: Registration
        reg_middleware = shape_artifact_registration(store=store, threshold_tokens=10)
        ctx = FakeToolResultContext.ok(large_data)

        async def noop():
            pass

        await reg_middleware(ctx, noop)
        artifact_id = ctx.metadata["artifact_meta"].artifact_id

        # Phase 2: Sandbox resolution
        @dataclass
        class FakeContext:
            org_id: str = "org1"
            user_id: str = "user1"
            conversation_id: str = "conv1"

        fake_ctx = FakeContext()
        files, resolved, missing = await _resolve_input_artifacts(
            fake_ctx, None, [artifact_id], inmemory_store=store,
        )
        assert len(resolved) == 1
        assert len(missing) == 0
        staged_content = json.loads(list(files.values())[0].decode("utf-8"))
        assert staged_content == {"sales": [i * 100 for i in range(200)]}

    @pytest.mark.asyncio
    async def test_compact_reference_enriched_metadata(self):
        """Compact reference includes tool_name and args from registration."""
        store = InMemoryArtifactStore()
        reg_middleware = shape_artifact_registration(store=store, threshold_tokens=10)

        ctx = FakeToolResultContext.ok(
            "x" * 500,
            tool_path="/toolsets/jira/search_issues",
            _result_accum_args={"project": "PIPE", "status": "open"},
        )

        async def noop():
            pass

        await reg_middleware(ctx, noop)
        meta = ctx.metadata["artifact_meta"]

        msg = ToolMessage(content="x" * 500, tool_call_id="tc_1", artifact_meta=meta)
        ref = _compact_reference(msg)
        assert "tool: jira__search_issues" in ref
        assert "PIPE" in ref
        assert meta.artifact_id in ref
