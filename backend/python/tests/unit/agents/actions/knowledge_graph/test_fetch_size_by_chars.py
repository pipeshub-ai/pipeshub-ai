"""FETCH_SIZE_BY_CHARS: fetch sized by characters alone, on unless a
deployment turns it off.

A block count is a stand-in for size that cuts records which fit, and models
that send `max_blocks` on every call turn one read into several. With the
switch on, a first read ignores `max_blocks`, a call gets its share of the
window (at most `FETCH_MAX_CHARS`), and fetch has its own tool-result cap so
the shared 64K cap never cuts it. Off, every fetch is byte-identical to the
behaviour before the switch existed.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, ToolMessage
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
    shape_budget_reduction,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.utils.render_budget import (
    FETCH_MAX_CHARS,
    FETCH_RESULT_RESERVE,
    FETCH_SAFETY_MAX_BLOCKS,
    MAX_RENDER_CHARS,
    fetch_result_cap,
    fetch_size_by_chars_enabled,
    resolve_render_budget,
)
from tests.unit.agents.actions.knowledge_graph.fetch_scenarios import (
    fetch_scenarios_digest,
)
from tests.unit.agents.actions.knowledge_graph.test_fetch_sizing import (
    _context,
    _fetch,
    _outcome,
    _records_part,
    _shown,
    _text_record,
)

if TYPE_CHECKING:
    from collections.abc import Callable

SWITCH = "FETCH_SIZE_BY_CHARS"
# What the scenarios in `fetch_scenarios` rendered before this switch existed.
# Recompute only for a deliberate change to fetch with the switch off.
_SWITCH_OFF_DIGEST = "51551a500c3643b8fd80693dad54cbe3cde705cce84fbcff8733c683e779f911"


@pytest.fixture
def switch(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def _set(value: str | None, *, high_accuracy: str | None = None) -> None:
        for name, raw in ((SWITCH, value), ("HIGH_ACCURACY_MODE", high_accuracy)):
            if raw is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, raw)

    _set(None)
    return _set


class TestSwitch:
    @pytest.mark.parametrize(
        ("value", "high_accuracy", "expected"),
        [
            (None, None, True),
            ("", None, True),
            ("  ", None, True),
            ("true", None, True),
            ("false", None, False),
            ("FALSE", None, False),
            ("0", None, False),
            ("off", None, False),
            (None, "true", True),
            ("", "true", True),
            ("false", "true", False),
            (None, "false", True),
        ],
    )
    def test_on_unless_turned_off_and_independent_of_high_accuracy_mode(
        self, switch, value: str | None, high_accuracy: str | None, expected: bool,
    ) -> None:
        switch(value, high_accuracy=high_accuracy)

        assert fetch_size_by_chars_enabled() is expected


class TestBudget:
    @pytest.mark.parametrize(
        ("window", "expected"),
        [(8_192, 8_192), (16_000, 16_000), (128_000, 128_000), (200_000, FETCH_MAX_CHARS),
         (1_000_000, FETCH_MAX_CHARS)],
    )
    def test_a_share_of_the_window_up_to_the_ceiling(self, switch, window: int, expected: int) -> None:
        assert resolve_render_budget(window).max_chars == expected

    def test_an_8k_window_gets_at_most_8k_characters(self, switch) -> None:
        assert resolve_render_budget(8_192).max_chars <= 8_192

    @pytest.mark.parametrize("window", [8_192, 64_000, 128_000, 1_000_000])
    def test_off_keeps_the_size_under_the_shared_cap(self, switch, window: int) -> None:
        switch("false")

        assert resolve_render_budget(window).max_chars <= MAX_RENDER_CHARS
        assert fetch_result_cap(window) is None

    def test_the_override_is_clamped_to_the_ceiling(self, switch, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_FULL_RECORD_MAX_CHARS", "999999999")

        assert resolve_render_budget(1_000_000).max_chars == FETCH_MAX_CHARS

    @pytest.mark.parametrize("window", [8_192, 128_000, 1_000_000])
    def test_fetch_gets_a_cap_that_holds_its_budget(self, switch, window: int) -> None:
        cap = fetch_result_cap(window)

        assert cap == max(DEFAULT_MAX_RESULT_CHARS, resolve_render_budget(window).max_chars + FETCH_RESULT_RESERVE)


class TestAFirstReadIgnoresMaxBlocks:
    @pytest.mark.asyncio
    async def test_max_blocks_20_on_a_record_that_fits_returns_all_100(self, switch) -> None:
        context = _context(query="")
        text = await _fetch(context, [_text_record("a", 100, 150)], start_block=0, max_blocks=20,
                            start_block_sent=True)

        assert _shown(text, "a") == set(range(100))
        assert _outcome(context, "rec-a")["complete"] is True
        assert _outcome(context, "rec-a")["requestedMaxBlocks"] == 20

    @pytest.mark.asyncio
    async def test_off_honours_it(self, switch) -> None:
        switch("false")
        context = _context(query="")
        text = await _fetch(context, [_text_record("a", 100, 150)], max_blocks=20)

        assert _shown(text, "a") == set(range(20))

    @pytest.mark.asyncio
    async def test_a_continuation_honours_it_per_record(self, switch) -> None:
        context = _context(query="")
        text = await _fetch(context, [_text_record("k", 100, 150)], start_block=40, max_blocks=20,
                            start_block_sent=True)

        assert _shown(text, "k") == set(range(40, 60))
        assert "start_block=60" in text
        assert _outcome(context, "rec-k")["truncatedBy"] == "blocks"


class TestRecordsComeBackWhole:
    @pytest.mark.asyncio
    async def test_three_records_of_30k_at_a_128k_window(self, switch) -> None:
        context = _context(context_length=128_000, query="")
        names = ["p", "q", "r"]
        text = await _fetch(context, [_text_record(n, 100, 300) for n in names])

        for name in names:
            assert _shown(text, name) == set(range(100))
            assert _outcome(context, f"rec-{name}")["complete"] is True
            assert f"rec-{name}" in context.full_records_fetched
        assert "To read on" not in text
        assert len(text) > DEFAULT_MAX_RESULT_CHARS, "more than the shared cap would have allowed"
        assert len(text) <= fetch_result_cap(128_000)

    @pytest.mark.asyncio
    async def test_the_safety_cap_bounds_a_record_of_tiny_blocks(self, switch) -> None:
        context = _context(context_length=1_000_000, query="")
        text = await _fetch(context, [_text_record("t", FETCH_SAFETY_MAX_BLOCKS + 500, 14)])

        assert _shown(text, "t") == set(range(FETCH_SAFETY_MAX_BLOCKS))
        assert f"start_block={FETCH_SAFETY_MAX_BLOCKS}" in text
        assert _outcome(context, "rec-t")["truncatedBy"] == "blocks"

    @pytest.mark.asyncio
    async def test_an_8k_window_reads_in_proportion(self, switch) -> None:
        context = _context(context_length=8_192, query="")
        text = await _fetch(context, [_text_record("w", 300, 400)])

        assert len(text) <= 8_192
        assert len(_records_part(text)) <= resolve_render_budget(8_192).max_chars
        assert _shown(text, "w")


class TestTheSharedCapDoesNotCutFetch:
    @staticmethod
    async def _shape(messages: list, tool_caps: dict[str, int] | None) -> list:
        ctx = ModelCallContext(messages=messages, budget=ContextBudget(max_tokens=1_000_000))

        async def _next() -> None:
            return None

        await shape_budget_reduction(tool_caps=tool_caps)(ctx, _next)
        return ctx.messages

    @pytest.mark.asyncio
    async def test_a_large_fetch_reaches_the_model_whole_and_other_tools_are_still_capped(self, switch) -> None:
        context = _context(context_length=1_000_000, query="")
        fetched = await _fetch(context, [_text_record(f"L{i}", 100, 1_000) for i in range(2)])
        assert len(fetched) > DEFAULT_MAX_RESULT_CHARS
        messages = [
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="f1", name=FETCH_RECORD_TOOL_NAME, arguments={}),
                ToolCall(id="s1", name="knowledgegraph__search", arguments={}),
            ]),
            ToolMessage(content=fetched, tool_call_id="f1"),
            ToolMessage(content="x" * 100_000, tool_call_id="s1"),
        ]

        shaped = await self._shape(messages, {FETCH_RECORD_TOOL_NAME: fetch_result_cap(1_000_000)})

        assert shaped[1].content == fetched
        assert len(shaped[2].content) <= DEFAULT_MAX_RESULT_CHARS

    @pytest.mark.asyncio
    async def test_without_tool_caps_the_shaper_is_unchanged(self, switch) -> None:
        messages = [
            AssistantMessage(content="", tool_calls=[ToolCall(id="f1", name=FETCH_RECORD_TOOL_NAME, arguments={})]),
            ToolMessage(content="y" * 100_000, tool_call_id="f1"),
        ]

        shaped = await self._shape(messages, None)

        assert len(shaped[1].content) <= DEFAULT_MAX_RESULT_CHARS

    @pytest.mark.parametrize(("value", "expected"), [(None, 146_000), ("false", None)])
    def test_the_factory_gives_fetch_its_cap_only_when_on(self, switch, value: str | None, expected) -> None:
        from app.agents.agent_loop.factory import PipesHubAgentFactory
        from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

        switch(value)
        context = make_context(llm=FakeChatModel(), context_length=1_000_000)
        with patch(
            "app.agent_loop_lib.context.pipeline.shape_budget_reduction", wraps=shape_budget_reduction,
        ) as shaper:
            PipesHubAgentFactory._build_hooks(context)

        caps = shaper.call_args.kwargs["tool_caps"]
        assert (caps[FETCH_RECORD_TOOL_NAME] if caps else None) == expected


class TestOffIsUnchanged:
    @pytest.mark.asyncio
    async def test_every_scenario_renders_byte_identically(self, switch) -> None:
        switch("false")

        assert await fetch_scenarios_digest() == _SWITCH_OFF_DIGEST

    @pytest.mark.asyncio
    async def test_on_changes_them(self, switch) -> None:
        assert await fetch_scenarios_digest() != _SWITCH_OFF_DIGEST


def _fetch_tool_schema(query: str) -> dict:
    from app.agents.agent_loop.hooks.citations import (
        CitationCollector,
        _FetchFullRecordTool,
    )
    from tests.unit.agents.adapter.conftest import make_context

    context = make_context()
    context.tool_state["query"] = query
    return _FetchFullRecordTool(CitationCollector(context), context).to_schema().model_dump()


class TestTheSchemaIsStablePerDeployment:
    """The tool schema is part of the prompt prefix a provider caches, so it
    may depend on the deployment's switch but never on the request."""

    @pytest.mark.parametrize("value", [None, "false"])
    def test_identical_across_questions(self, switch, value: str | None) -> None:
        switch(value)

        assert _fetch_tool_schema("Who owns HarborBank?") == _fetch_tool_schema("Compare Q3 revenue")

    def test_only_the_max_blocks_text_differs_between_on_and_off(self, switch) -> None:
        on = _fetch_tool_schema("q")
        switch("false")
        off = _fetch_tool_schema("q")

        on_text = json.dumps(on, sort_keys=True)
        off_text = json.dumps(off, sort_keys=True)
        assert on_text != off_text
        on_props, off_props = on["input_schema"]["properties"], off["input_schema"]["properties"]
        assert set(on_props) == set(off_props), "max_blocks is still accepted"
        assert {k: v for k, v in on_props.items() if k != "max_blocks"} == {
            k: v for k, v in off_props.items() if k != "max_blocks"
        }
        assert on["description"] == off["description"]
        assert re.search(r"Leave unset", on_props["max_blocks"]["description"])
        assert "smaller slice" in off_props["max_blocks"]["description"]


class TestThePromptPrefixPerMode:
    """With the switch off and on, each agent mode's tool list and stable
    system-prompt block are the same for two different questions."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [None, "false"])
    @pytest.mark.parametrize("mode", ["quick", "react", "planExecute", "deep"])
    async def test_two_questions_share_the_prefix(
        self, switch, monkeypatch: pytest.MonkeyPatch, mode: str, value: str | None,
    ) -> None:
        from app.agents.agent_loop.factory import PipesHubAgentFactory
        from app.agents.agent_loop.hooks.citations import (
            CitationCollector,
            _FetchFullRecordTool,
        )
        from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

        switch(value)
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")

        async def _prefix(question: str) -> tuple[str, str]:
            context = make_context(llm=FakeChatModel())
            agent, runtime, goal, _ = await PipesHubAgentFactory().create(
                context, context.llm, mode, query=question,
            )
            if not runtime.tool_registry.has(FETCH_RECORD_TOOL_NAME):
                runtime.tool_registry.register_tool(_FetchFullRecordTool(CitationCollector(context), context))
            tools = json.dumps(
                sorted((s.model_dump() for s in runtime.tool_registry.schemas()), key=lambda s: s["name"]),
                sort_keys=True,
            )
            builder = agent.spec.system_prompt
            stable = ""
            if hasattr(builder, "build_blocks"):
                with patch.multiple(
                    "app.agents.agent_loop.prompt_builder",
                    _build_knowledge_context=MagicMock(return_value=""),
                    build_capability_summary=MagicMock(return_value=""),
                    _format_user_context=MagicMock(return_value=""),
                ):
                    stable = builder.build_blocks(agent.spec, runtime, goal, [], {})[0]
            return tools, stable

        first = await _prefix("Who owns HarborBank?")
        second = await _prefix("Compare Q3 revenue across the finance reports")

        assert first == second
        assert FETCH_RECORD_TOOL_NAME in first[0]


class TestSmallWindowsAdvance:
    """A 1k- or 2k-token window gives a call under 2,000 characters. The only
    record of the call must still be read, or every retry repeats."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("window", [1_024, 2_048])
    async def test_one_oversized_record_is_read_on_hint_by_hint(self, switch, window: int) -> None:
        record = _text_record("sw", 12, 300)
        context = _context(context_length=window, query="")
        seen: set[int] = set()
        start = 0
        for _ in range(60):
            text = await _fetch(context, [record], start_block=start, start_block_sent=start > 0)
            assert "was not read" not in text
            assert len(text) <= resolve_render_budget(window).max_chars
            seen |= set(_outcome(context, "rec-sw")["shownBlocks"])
            match = re.search(r"start_block=(\d+)", text)
            if match is None:
                break
            assert int(match.group(1)) > start
            start = int(match.group(1))
        assert seen == set(range(12)), "the hints walk the whole record"

    @pytest.mark.asyncio
    async def test_the_floor_still_applies_to_a_share_cut_for_another_record(self, switch) -> None:
        context = _context(context_length=2_048, query="")

        text = await _fetch(context, [_text_record("fa", 12, 300), _text_record("fb", 12, 300)])

        assert "Record rec-fa was not read" in text
        assert _shown(text, "fb")
