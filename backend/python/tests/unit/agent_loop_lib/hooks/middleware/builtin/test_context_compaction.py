"""Spike tests for the context compaction pipeline.

Validates:
1. Compacted message sequences preserve tool_use/tool_result pairing.
2. Artifact compaction correctly handles turn-aware priority.
3. Deterministic compaction produces identical output for identical input.
4. Synthesis guard enforces hard budget limits.
"""

from __future__ import annotations

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
from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
    shape_artifact_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.deterministic_compact import (
    shape_deterministic_compact,
)
from app.agent_loop_lib.hooks.middleware.builtin.synthesis_guard import (
    ContextBudgetExceeded,
    shape_synthesis_guard,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext


def _budget(max_tokens: int = 10_000) -> ContextBudget:
    return ContextBudget(max_tokens=max_tokens, model="test")


def _tool_msg(
    content: str,
    call_id: str = "tc_1",
    meta: ToolMessageMeta | None = None,
) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, artifact_meta=meta)


def _assistant_with_calls(*tool_names: str) -> AssistantMessage:
    calls = [ToolCall(id=f"tc_{i}", name=name, arguments={}) for i, name in enumerate(tool_names, 1)]
    return AssistantMessage(content=[TextPart(text="Calling tools")], tool_calls=calls)


async def _run_middleware(middleware, messages, budget=None, turn_index=0):
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


# ---------- Tool use / tool result pairing ----------

class TestProviderPairingPreservation:
    """Verify that compaction never breaks tool_use/tool_result pairing."""

    @pytest.mark.asyncio
    async def test_artifact_compaction_preserves_pairing(self):
        """Each ToolMessage still has its tool_call_id after compaction."""
        meta = ToolMessageMeta(artifact_id="a1", summary="data", original_token_count=5000, turn_index=0)
        messages = [
            SystemMessage(content="system"),
            UserMessage(content="query"),
            _assistant_with_calls("search"),
            _tool_msg("x" * 20_000, call_id="tc_1", meta=meta),
        ]
        shaper = shape_artifact_compaction()
        result = await _run_middleware(shaper, messages, turn_index=1)

        tool_msgs = [m for m in result if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_call_id == "tc_1"

    @pytest.mark.asyncio
    async def test_deterministic_compact_preserves_pairing(self):
        """Deterministic compaction keeps tool_call_id and tool_calls."""
        messages = [
            SystemMessage(content="system"),
            UserMessage(content="query"),
            _assistant_with_calls("search"),
            _tool_msg("result " * 100, call_id="tc_1"),
            _assistant_with_calls("analyze"),
            _tool_msg("analysis " * 100, call_id="tc_1"),
            UserMessage(content="recent"),
            _assistant_with_calls("final"),
            _tool_msg("final result", call_id="tc_1"),
        ]
        shaper = shape_deterministic_compact(
            trigger_ratio=0.0, keep_last_n_messages=2, pin_first_n=1
        )
        result = await _run_middleware(shaper, messages, _budget(500))

        for msg in result:
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                assert all(tc.id for tc in msg.tool_calls)
            if isinstance(msg, ToolMessage):
                assert msg.tool_call_id is not None


# ---------- Turn-aware artifact compaction ----------

class TestArtifactCompaction:
    @pytest.mark.asyncio
    async def test_old_turn_compacted(self):
        """Results from 2+ turns ago are compacted (past keep_last_n_turns window)."""
        meta = ToolMessageMeta(
            artifact_id="a1", summary="old data",
            original_token_count=5000, turn_index=0,
        )
        messages = [
            SystemMessage(content="system"),
            _tool_msg("x" * 20_000, call_id="tc_1", meta=meta),
        ]
        shaper = shape_artifact_compaction()
        result = await _run_middleware(shaper, messages, turn_index=2)

        tool_msg = [m for m in result if isinstance(m, ToolMessage)][0]
        assert "[artifact:a1]" in tool_msg.content
        assert "x" * 1000 not in tool_msg.content

    @pytest.mark.asyncio
    async def test_last_turn_kept_when_under_budget(self):
        """Artifacts from the immediately preceding turn stay full so the
        model sees its tool results at least once — it never sees them
        inline on the turn it called the tool."""
        meta = ToolMessageMeta(
            artifact_id="a1", summary="fresh data",
            original_token_count=5000, turn_index=0,
        )
        messages = [
            SystemMessage(content="system"),
            _tool_msg("x" * 20_000, call_id="tc_1", meta=meta),
        ]
        shaper = shape_artifact_compaction()
        result = await _run_middleware(shaper, messages, _budget(10_000), turn_index=1)

        tool_msg = [m for m in result if isinstance(m, ToolMessage)][0]
        assert tool_msg.content == "x" * 20_000

    @pytest.mark.asyncio
    async def test_last_turn_compacted_when_over_budget(self):
        """Even recent-turn artifacts are compacted if context exceeds the
        absolute budget — the budget overflow fallback still applies."""
        meta = ToolMessageMeta(
            artifact_id="a1", summary="fresh data",
            original_token_count=5000, turn_index=0,
        )
        messages = [
            SystemMessage(content="system"),
            _tool_msg("x" * 20_000, call_id="tc_1", meta=meta),
        ]
        shaper = shape_artifact_compaction()
        result = await _run_middleware(shaper, messages, _budget(2_000), turn_index=1)

        tool_msg = [m for m in result if isinstance(m, ToolMessage)][0]
        assert "[artifact:a1]" in tool_msg.content
        assert "x" * 1000 not in tool_msg.content

    @pytest.mark.asyncio
    async def test_current_turn_kept_when_under_budget(self):
        """Current-turn results stay full when budget allows."""
        meta = ToolMessageMeta(
            artifact_id="a1", summary="data",
            original_token_count=100, turn_index=0,
        )
        messages = [
            SystemMessage(content="system"),
            _tool_msg("short result", call_id="tc_1", meta=meta),
        ]
        shaper = shape_artifact_compaction()
        result = await _run_middleware(shaper, messages, _budget(10_000), turn_index=0)

        tool_msg = [m for m in result if isinstance(m, ToolMessage)][0]
        assert tool_msg.content == "short result"

    @pytest.mark.asyncio
    async def test_current_turn_with_schema_compacted_first(self):
        """When over budget, schema-bearing results compact before non-schema."""
        schema_meta = ToolMessageMeta(
            artifact_id="a1", summary="jira", turn_index=0,
            result_schema={"type": "array"}, original_token_count=5000,
        )
        plain_meta = ToolMessageMeta(
            artifact_id="a2", summary="web", turn_index=0,
            original_token_count=5000,
        )
        messages = [
            SystemMessage(content="sys"),
            _tool_msg("x" * 20_000, call_id="tc_1", meta=schema_meta),
            _tool_msg("y" * 20_000, call_id="tc_2", meta=plain_meta),
        ]
        shaper = shape_artifact_compaction()
        result = await _run_middleware(shaper, messages, _budget(6_000), turn_index=0)

        tool_msgs = [m for m in result if isinstance(m, ToolMessage)]
        schema_msg = next(m for m in tool_msgs if m.artifact_meta and m.artifact_meta.artifact_id == "a1")
        plain_msg = next(m for m in tool_msgs if m.artifact_meta and m.artifact_meta.artifact_id == "a2")

        assert "[artifact:a1]" in schema_msg.content
        assert len(plain_msg.content) > len(schema_msg.content)


# ---------- Deterministic compaction ----------

class TestDeterministicCompaction:
    @pytest.mark.asyncio
    async def test_identical_input_identical_output(self):
        """Same input always produces same output."""
        messages = [
            SystemMessage(content="system"),
            UserMessage(content="query 1"),
            AssistantMessage(content="response 1. More text here."),
            _tool_msg("result data " * 50, call_id="tc_1"),
            UserMessage(content="query 2"),
            AssistantMessage(content="response 2"),
        ]
        shaper = shape_deterministic_compact(
            trigger_ratio=0.0, keep_last_n_messages=2, pin_first_n=1
        )
        result1 = await _run_middleware(shaper, messages, _budget(500))
        result2 = await _run_middleware(shaper, messages, _budget(500))

        assert len(result1) == len(result2)
        for m1, m2 in zip(result1, result2):
            if isinstance(m1, ToolMessage):
                assert m1.content == m2.content
            elif isinstance(m1, AssistantMessage):
                assert m1.text == m2.text


# ---------- Synthesis guard ----------

class TestSynthesisGuard:
    @pytest.mark.asyncio
    async def test_strips_thinking_first(self):
        """Thinking parts are stripped before aggressive clearing."""
        from app.agent_loop_lib.core.messages import ThinkingPart

        messages = [
            SystemMessage(content="sys"),
            AssistantMessage(content=[
                ThinkingPart(thinking="internal reasoning " * 500),
                TextPart(text="answer"),
            ]),
        ]
        shaper = shape_synthesis_guard()
        result = await _run_middleware(shaper, messages, _budget(1_100))

        assistant = [m for m in result if isinstance(m, AssistantMessage)][0]
        assert not any(isinstance(p, ThinkingPart) for p in assistant.content)

    @pytest.mark.asyncio
    async def test_raises_on_unresolvable_overflow(self):
        """Raises ContextBudgetExceeded when budget is impossibly small."""
        messages = [
            SystemMessage(content="x" * 10_000),
        ]
        shaper = shape_synthesis_guard()
        with pytest.raises(ContextBudgetExceeded):
            await _run_middleware(shaper, messages, _budget(1_100))

    @pytest.mark.asyncio
    async def test_passes_when_under_budget(self):
        """No-op when context fits."""
        messages = [SystemMessage(content="short")]
        shaper = shape_synthesis_guard()
        result = await _run_middleware(shaper, messages, _budget(10_000))
        assert len(result) == 1
        assert result[0].content == "short"
