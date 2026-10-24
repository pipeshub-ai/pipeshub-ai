"""Coverage-focused tests for the budget-aware auto-compact PRE_MODEL shaper.

Validates:
1. Trigger/guard logic — when compaction fires vs. no-ops.
2. Atomic group building (`_build_groups`) and its interaction with
   provider-mandated assistant/tool-result pairing.
3. Tail-selection (`_find_tail_start`) budget walking.
4. The actual message rewrite: head + summary + tail.
5. The LLM-backed summarizer (`make_llm_summarizer`) call shape and its
   naive-fallback sibling (`_naive_summary`).
6. Error propagation when the summarizer raises.
7. Edge cases: empty/single-message histories, all-tool-message histories.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Never

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
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import (
    _build_groups,
    _find_tail_start,
    _group_tokens,
    _naive_summary,
    make_llm_summarizer,
    shape_auto_compact,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext


def _budget(max_tokens: int = 1_000, reserved_artifact_tokens: int = 0) -> ContextBudget:
    return ContextBudget(
        max_tokens=max_tokens, model="test", reserved_artifact_tokens=reserved_artifact_tokens
    )


def _tokens(n: int) -> str:
    """Text whose estimated token count (per `count_message_tokens`) is
    exactly `n` — overhead(4) + chars//4, so chars = (n - 4) * 4."""
    return "a" * max((n - 4) * 4, 0)


def _user(n_tokens: int) -> UserMessage:
    return UserMessage(content=_tokens(n_tokens))


def _system(n_tokens: int) -> SystemMessage:
    return SystemMessage(content=_tokens(n_tokens))


def _tool_msg(content: str = "result", call_id: str = "tc_1", meta: ToolMessageMeta | None = None) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, artifact_meta=meta)


def _assistant_with_calls(*call_ids: str) -> AssistantMessage:
    calls = [ToolCall(id=cid, name=f"tool_{cid}", arguments={}) for cid in call_ids]
    return AssistantMessage(content="calling tools", tool_calls=calls)


async def _run_middleware(middleware, messages, budget=None, turn_index=0):
    ctx = ModelCallContext(
        messages=list(messages),
        budget=budget or _budget(),
        turn_index=turn_index,
    )
    calls = []

    async def next_fn() -> None:
        calls.append(True)

    await middleware(ctx, next_fn)
    return ctx, calls


class _FakeTransport:
    def __init__(self, text: str | None):
        self.text = text
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(text=self.text))


class _FakeRegistry:
    def __init__(self, transport: _FakeTransport):
        self.transport = transport
        self.resolved_with: str | None = None

    def resolve(self, provider: str):
        self.resolved_with = provider
        return self.transport


# ---------------------------------------------------------------------------
# _naive_summary
# ---------------------------------------------------------------------------


class TestNaiveSummary:
    def test_empty_messages_returns_placeholder(self) -> None:
        assert _naive_summary([]) == "(no content)"

    def test_tool_message_with_artifact_meta_formatted(self) -> None:
        meta = ToolMessageMeta(artifact_id="a1", summary="the summary text", tool_name="search")
        result = _naive_summary([_tool_msg("ignored body", meta=meta)])
        assert result == "[tool] artifact:a1 (search) — the summary text"

    def test_tool_message_without_meta_uses_call_id_preview(self) -> None:
        result = _naive_summary([_tool_msg("some tool output here", call_id="tc_9")])
        assert result == "[tool:tc_9] some tool output here"

    def test_tool_message_without_call_id_uses_placeholder(self) -> None:
        msg = ToolMessage(content="body", tool_call_id=None)
        result = _naive_summary([msg])
        assert result.startswith("[tool:?]")

    def test_other_roles_prefixed_with_role_and_truncated(self) -> None:
        result = _naive_summary([UserMessage(content="hello there")])
        assert result == "[user] hello there"

    def test_blank_text_message_dropped(self) -> None:
        result = _naive_summary([UserMessage(content=""), UserMessage(content="kept")])
        assert result == "[user] kept"

    def test_result_truncated_to_max_naive_chars(self) -> None:
        """Each part is capped at _NAIVE_TEXT_CHARS, so the overall
        _NAIVE_TOTAL_CHARS cap bites once enough messages accumulate."""
        messages = [UserMessage(content="y" * 600) for _ in range(30)]
        result = _naive_summary(messages)
        assert len(result) == 8_000
        assert result.endswith("...[truncated]")

    def test_multiple_messages_joined_by_newline(self) -> None:
        result = _naive_summary([UserMessage(content="one"), UserMessage(content="two")])
        assert result == "[user] one\n[user] two"


# ---------------------------------------------------------------------------
# make_llm_summarizer
# ---------------------------------------------------------------------------


class TestMakeLlmSummarizer:
    @pytest.mark.asyncio
    async def test_resolves_transport_lazily_with_provider(self) -> None:
        transport = _FakeTransport("summary")
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="openai", model="gpt-x")

        assert registry.resolved_with is None
        await summarizer([UserMessage(content="hi")])
        assert registry.resolved_with == "openai"

    @pytest.mark.asyncio
    async def test_calls_transport_with_model_and_system_prompt(self) -> None:
        transport = _FakeTransport("canned summary")
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="anthropic", model="claude-x")

        await summarizer([UserMessage(content="hi")])

        assert len(transport.calls) == 1
        call = transport.calls[0]
        assert call["model"] == "claude-x"
        assert "context compaction assistant" in call["system"]
        assert len(call["messages"]) == 1

    @pytest.mark.asyncio
    async def test_joined_text_preserves_artifact_ids_and_tool_names(self) -> None:
        transport = _FakeTransport("canned summary")
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="openai", model="gpt-x")
        meta = ToolMessageMeta(artifact_id="art_42", summary="fetched rows", tool_name="query_db")

        await summarizer([UserMessage(content="find rows"), _tool_msg(meta=meta)])

        prompt = transport.calls[0]["messages"][0].content
        assert "[user] find rows" in prompt
        assert "artifact:art_42" in prompt
        assert "tool:query_db" in prompt
        assert "fetched rows" in prompt

    @pytest.mark.asyncio
    async def test_orphan_tool_message_formatted_with_call_id(self) -> None:
        transport = _FakeTransport("canned summary")
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="openai", model="gpt-x")

        await summarizer([_tool_msg("raw output", call_id="tc_7")])

        prompt = transport.calls[0]["messages"][0].content
        assert "[tool:tc_7]" in prompt
        assert "raw output" in prompt

    @pytest.mark.asyncio
    async def test_returns_response_text_when_present(self) -> None:
        transport = _FakeTransport("the real summary")
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="openai", model="gpt-x")

        result = await summarizer([UserMessage(content="hi")])
        assert result == "the real summary"

    @pytest.mark.asyncio
    async def test_falls_back_to_joined_text_when_response_empty(self) -> None:
        transport = _FakeTransport("")
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="openai", model="gpt-x")

        result = await summarizer([UserMessage(content="hi there")])
        assert result == "[user] hi there"

    @pytest.mark.asyncio
    async def test_falls_back_to_joined_text_when_response_none(self) -> None:
        transport = _FakeTransport(None)
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="openai", model="gpt-x")

        result = await summarizer([UserMessage(content="hi there")])
        assert result == "[user] hi there"

    @pytest.mark.asyncio
    async def test_falls_back_to_naive_when_response_whitespace_only(self) -> None:
        transport = _FakeTransport("  \n  ")
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="openai", model="gpt-x")

        result = await summarizer([UserMessage(content="hi there")])
        assert result == "[user] hi there"

    @pytest.mark.asyncio
    async def test_input_budget_accounts_for_prompt_and_separators(self) -> None:
        """The total content sent to the LLM (prompt + separator-joined
        parts) must not exceed _MAX_SUMMARIZER_INPUT_CHARS."""
        from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import (
            _MAX_SUMMARIZER_INPUT_CHARS,
        )
        big_content = "x" * 20_000
        messages = [UserMessage(content=big_content) for _ in range(10)]
        transport = _FakeTransport("summary ok")
        registry = _FakeRegistry(transport)
        summarizer = make_llm_summarizer(registry, provider="openai", model="gpt-x")

        await summarizer(messages)

        sent_content = transport.calls[0]["messages"][0].content
        assert len(sent_content) <= _MAX_SUMMARIZER_INPUT_CHARS


# ---------------------------------------------------------------------------
# _build_groups
# ---------------------------------------------------------------------------


class TestBuildGroups:
    def test_standalone_messages_each_form_own_group(self) -> None:
        messages = [UserMessage(content="a"), UserMessage(content="b"), SystemMessage(content="c")]
        groups = _build_groups(messages)
        assert groups == [[0], [1], [2]]

    def test_assistant_with_tool_calls_merges_matching_tool_messages(self) -> None:
        messages = [
            _assistant_with_calls("tc_1", "tc_2"),
            _tool_msg(call_id="tc_1"),
            _tool_msg(call_id="tc_2"),
            UserMessage(content="next"),
        ]
        groups = _build_groups(messages)
        assert groups == [[0, 1, 2], [3]]

    def test_mismatched_tool_call_id_breaks_group_early(self) -> None:
        messages = [
            _assistant_with_calls("tc_1"),
            _tool_msg(call_id="tc_1"),
            _tool_msg(call_id="unrelated"),
        ]
        groups = _build_groups(messages)
        assert groups == [[0, 1], [2]]

    def test_assistant_with_tool_calls_but_no_following_tool_message(self) -> None:
        messages = [_assistant_with_calls("tc_1"), UserMessage(content="next")]
        groups = _build_groups(messages)
        assert groups == [[0], [1]]

    def test_orphaned_tool_messages_each_own_group(self) -> None:
        messages = [_tool_msg(call_id="tc_1"), _tool_msg(call_id="tc_2")]
        groups = _build_groups(messages)
        assert groups == [[0], [1]]

    def test_empty_messages_yields_no_groups(self) -> None:
        assert _build_groups([]) == []


# ---------------------------------------------------------------------------
# _group_tokens / _first_non_pinned_group / _find_tail_start
# ---------------------------------------------------------------------------


class TestGroupTokens:
    def test_sums_tokens_across_group_indices(self) -> None:
        messages = [_user(50), _user(30), _user(20)]
        assert _group_tokens(messages, [0, 1]) == 80
        assert _group_tokens(messages, [0, 1, 2]) == 100


class TestFindTailStart:
    def test_always_includes_last_group_even_over_budget(self) -> None:
        messages = [_user(50), _user(5_000)]
        groups = [[0], [1]]
        tail_start = _find_tail_start(messages, groups, budget=1_000, max_tail_ratio=0.01)
        assert tail_start == 1

    def test_stops_before_exceeding_tail_budget(self) -> None:
        messages = [_user(50), _user(100), _user(100), _user(100)]
        groups = [[0], [1], [2], [3]]
        tail_start = _find_tail_start(messages, groups, budget=1_000, max_tail_ratio=0.25)

        assert tail_start == 2
        tail_cost = sum(_group_tokens(messages, g) for g in groups[tail_start:])
        assert tail_cost <= 250
        # Adding the next-oldest group would have pushed the tail over budget.
        assert tail_cost + _group_tokens(messages, groups[tail_start - 1]) > 250

    def test_loop_exhausts_when_budget_huge(self) -> None:
        """A generous budget never exceeds the tail limit, so the loop runs
        to completion over every group and the whole history becomes tail."""
        messages = [_user(10), _user(10), _user(10)]
        groups = [[0], [1], [2]]
        tail_start = _find_tail_start(messages, groups, budget=10_000, max_tail_ratio=1.0)
        assert tail_start == 0


# ---------------------------------------------------------------------------
# shape_auto_compact — trigger / guard branches (no-ops)
# ---------------------------------------------------------------------------


class TestTriggerGuards:
    @pytest.mark.asyncio
    async def test_noop_when_under_trigger_ratio(self) -> None:
        messages = [_system(10), _user(10), _user(10)]
        shaper = shape_auto_compact(trigger_ratio=0.85, max_tail_ratio=0.6)
        ctx, calls = await _run_middleware(shaper, messages, _budget(1_000))

        assert calls == [True]
        assert ctx.messages == messages

    @pytest.mark.asyncio
    async def test_noop_on_empty_message_list(self) -> None:
        shaper = shape_auto_compact()
        ctx, calls = await _run_middleware(shaper, [], _budget(1_000))

        assert calls == [True]
        assert ctx.messages == []

    @pytest.mark.asyncio
    async def test_noop_when_only_a_single_atomic_group(self) -> None:
        """An assistant tool-call turn plus its two tool results is one
        atomic group even though it is 3 messages long, so `len(groups) <= 1`
        short-circuits."""
        messages = [
            _assistant_with_calls("tc_1", "tc_2"),
            _tool_msg(content="x" * 4_000, call_id="tc_1"),
            _tool_msg(content="y" * 4_000, call_id="tc_2"),
        ]
        shaper = shape_auto_compact(trigger_ratio=0.0)
        ctx, calls = await _run_middleware(shaper, messages, _budget(1_000))

        assert calls == [True]
        assert ctx.messages == messages

    @pytest.mark.asyncio
    async def test_noop_when_tail_absorbs_everything(self) -> None:
        """When `max_tail_ratio` is generous enough that the tail walk
        reaches all the way back to the first message, nothing is left to
        summarise and compaction is skipped even though the trigger fired."""
        messages = [_system(10), _user(10), _user(10)]
        shaper = shape_auto_compact(trigger_ratio=0.0, max_tail_ratio=1.0)
        ctx, calls = await _run_middleware(shaper, messages, _budget(1_000))

        assert calls == [True]
        assert ctx.messages == messages


# ---------------------------------------------------------------------------
# shape_auto_compact — actual compaction (protected + summaries + tail)
# ---------------------------------------------------------------------------


class TestCompaction:
    @pytest.mark.asyncio
    async def test_rewrites_head_summary_tail_with_llm_summarizer(self) -> None:
        head = _system(50)
        mid1 = _user(300)
        mid2 = _user(300)
        tail1 = _user(250)
        tail2 = _user(250)
        messages = [head, mid1, mid2, tail1, tail2]

        captured: list = []

        async def fake_summarizer(mid_messages) -> str:
            captured.append(mid_messages)
            return "CANNED_SUMMARY"

        shaper = shape_auto_compact(
            summarizer=fake_summarizer, trigger_ratio=0.85, max_tail_ratio=0.6
        )
        ctx, calls = await _run_middleware(shaper, messages, _budget(1_000))

        assert calls == [True]
        assert captured == [[mid1, mid2]]
        assert len(ctx.messages) == 4
        assert ctx.messages[0] is head
        assert ctx.messages[2] is tail1
        assert ctx.messages[3] is tail2

        summary_msg = ctx.messages[1]
        assert isinstance(summary_msg, UserMessage)
        assert summary_msg.injected, "the loop wrote this summary, not the person"
        assert summary_msg.content == "[Auto-compacted summary of 2 earlier message(s)]\nCANNED_SUMMARY"

    @pytest.mark.asyncio
    async def test_current_request_mid_history_stays_verbatim_between_summaries(self) -> None:
        """In a chat, earlier turns come before the current question and the
        run's own work after it: each side is summarised on its own and the
        question itself is sent unchanged, in place."""
        first_question = UserMessage(content="QUESTION A " + "a" * 1_200)
        first_answer = AssistantMessage(content=[TextPart(text="answer A " + "b" * 1_200)])
        current = UserMessage(content="QUESTION B: which practice do I log into?")
        work = [
            _assistant_with_calls("tc_1"), _tool_msg(content="x" * 1_200, call_id="tc_1"),
            _assistant_with_calls("tc_2"), _tool_msg(content="y" * 1_200, call_id="tc_2"),
        ]
        recent = [_assistant_with_calls("tc_3"), _tool_msg(content="z" * 400, call_id="tc_3")]
        messages = [first_question, first_answer, current, *work, *recent]

        captured: list = []

        async def fake_summarizer(run) -> str:
            captured.append(run)
            return f"SUMMARY OF {len(run)}"

        shaper = shape_auto_compact(summarizer=fake_summarizer, trigger_ratio=0.0, max_tail_ratio=0.15)
        ctx, calls = await _run_middleware(shaper, messages, _budget(2_000))

        assert calls == [True]
        assert captured == [[first_question, first_answer], work]
        assert ctx.messages[1] is current
        assert [m for m in ctx.messages if isinstance(m, UserMessage) and not m.injected] == [current]
        assert ctx.messages[-2:] == recent

    @pytest.mark.asyncio
    async def test_uses_naive_summary_when_no_summarizer_configured(self) -> None:
        head = _system(50)
        mid1 = _user(100)
        mid2 = _user(100)
        tail1 = _user(100)
        tail2 = _user(100)
        messages = [head, mid1, mid2, tail1, tail2]

        shaper = shape_auto_compact(trigger_ratio=0.0, max_tail_ratio=0.25)
        ctx, calls = await _run_middleware(shaper, messages, _budget(1_000))

        assert calls == [True]
        expected_summary = _naive_summary([mid1, mid2])
        assert ctx.messages[1].content == f"[Auto-compacted summary of 2 earlier message(s)]\n{expected_summary}"
        assert ctx.messages[0] is head
        assert ctx.messages[2] is tail1
        assert ctx.messages[3] is tail2

    @pytest.mark.asyncio
    async def test_preserves_tool_call_pairing_across_compaction(self) -> None:
        head = _system(10)
        old_call = _assistant_with_calls("tc_old")
        old_result = _tool_msg(content="x" * 2_000, call_id="tc_old")
        filler = _user(10)
        recent_call = _assistant_with_calls("tc_new")
        recent_result = _tool_msg(content="y" * 2_000, call_id="tc_new")
        messages = [head, old_call, old_result, filler, recent_call, recent_result]

        shaper = shape_auto_compact(trigger_ratio=0.0, max_tail_ratio=0.3)
        ctx, calls = await _run_middleware(shaper, messages, _budget(2_000))

        assert calls == [True]
        tool_messages = [m for m in ctx.messages if isinstance(m, ToolMessage)]
        for tm in tool_messages:
            preceding_idx = ctx.messages.index(tm) - 1
            preceding = ctx.messages[preceding_idx]
            assert isinstance(preceding, AssistantMessage)
            assert tm.tool_call_id in {tc.id for tc in preceding.tool_calls}

    @pytest.mark.asyncio
    async def test_custom_thresholds_change_trigger_point(self) -> None:
        messages = [_system(10), _user(200), _user(200), _user(200)]

        lenient = shape_auto_compact(trigger_ratio=0.95)
        ctx_lenient, _ = await _run_middleware(lenient, messages, _budget(1_000))
        assert ctx_lenient.messages == messages

        strict = shape_auto_compact(trigger_ratio=0.1, max_tail_ratio=0.3)
        ctx_strict, _ = await _run_middleware(strict, messages, _budget(1_000))
        assert len(ctx_strict.messages) < len(messages)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestSummarizerFailure:
    @pytest.mark.asyncio
    async def test_summarizer_exception_propagates_and_skips_next_fn(self) -> None:
        """There is no try/except around the summarizer call, so a
        transient LLM failure aborts the whole PRE_MODEL pipeline instead
        of degrading to the naive summary — documented here as the actual
        (not necessarily desired) behavior."""
        messages = [_system(10), _user(300), _user(300), _user(250)]

        async def failing_summarizer(_mid) -> Never:
            raise RuntimeError("llm unavailable")

        shaper = shape_auto_compact(summarizer=failing_summarizer, trigger_ratio=0.0)
        ctx = ModelCallContext(messages=list(messages), budget=_budget(1_000))
        next_called = []

        async def next_fn() -> None:
            next_called.append(True)

        with pytest.raises(RuntimeError, match="llm unavailable"):
            await shaper(ctx, next_fn)

        assert next_called == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_single_message_never_compacted(self) -> None:
        messages = [_user(5_000)]
        shaper = shape_auto_compact(trigger_ratio=0.0)
        ctx, calls = await _run_middleware(shaper, messages, _budget(1_000))

        assert calls == [True]
        assert ctx.messages == messages

    @pytest.mark.asyncio
    async def test_all_tool_messages_compacted_safely(self) -> None:
        messages = [
            _system(10),
            _tool_msg(content="a" * 800, call_id="tc_1"),
            _tool_msg(content="b" * 800, call_id="tc_2"),
            _tool_msg(content="c" * 800, call_id="tc_3"),
            _tool_msg(content="d" * 800, call_id="tc_4"),
        ]
        shaper = shape_auto_compact(trigger_ratio=0.0, max_tail_ratio=0.3)
        ctx, calls = await _run_middleware(shaper, messages, _budget(1_000))

        assert calls == [True]
        assert isinstance(ctx.messages[0], SystemMessage)
        assert any(
            isinstance(m, UserMessage) and "Auto-compacted summary" in str(m.content)
            for m in ctx.messages
        )
        remaining_tool_ids = {m.tool_call_id for m in ctx.messages if isinstance(m, ToolMessage)}
        assert remaining_tool_ids <= {"tc_1", "tc_2", "tc_3", "tc_4"}
