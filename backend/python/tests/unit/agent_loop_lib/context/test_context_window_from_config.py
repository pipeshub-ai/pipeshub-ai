"""The context budget follows the window a deployment configured for its model.

The built-in table only knows some model names and gives every other one 128k,
so a 1M-window model was compacted as if it had 128k, and a local model
configured with 8k was sent prompts it could not take.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_schema_tokens, count_text_tokens
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import (
    shape_sliding_window,
)
from app.agent_loop_lib.hooks.middleware.builtin.synthesis_guard import (
    shape_synthesis_guard,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agent_loop_lib.agent.test_agent_step_outcomes import _NoteTool
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware
    from app.agent_loop_lib.tools.base import Tool


class TestBudget:
    def test_a_configured_window_wins_over_the_table(self) -> None:
        assert ContextBudget.for_model("claude-opus-5", context_window=1_000_000).max_tokens == 1_000_000 - 8_000

    def test_a_small_configured_window_shrinks_the_budget(self) -> None:
        assert ContextBudget.for_model("unknown-local-model", context_window=32_768).max_tokens == 32_768 - 8_000

    @pytest.mark.parametrize("window", [None, 0, -1])
    def test_no_usable_configured_window_falls_back_to_the_table(self, window: int | None) -> None:
        assert ContextBudget.for_model("claude-opus-5", context_window=window).max_tokens == 300_000 - 8_000

    @pytest.mark.parametrize(("model", "window"), [("claude-opus-5-5", 300_000), ("claude-sonnet-5-5", 200_000)])
    def test_current_claude_models_have_table_entries(self, model: str, window: int) -> None:
        assert ContextBudget.for_model(model).max_tokens == window - 8_000

    @pytest.mark.parametrize("model", ["gpt-6-sol", "gpt-6-luna", "gpt-6-astra", "gpt-5.6-sol", "gpt-5.6", "gpt-5.5"])
    def test_current_openai_models_get_their_input_limit(self, model: str) -> None:
        assert ContextBudget.for_model(model).max_tokens == 922_000 - 8_000

    def test_gpt_6_1_sol_gets_a_working_budget_below_its_limit(self) -> None:
        assert ContextBudget.for_model("gpt-6.1-sol").max_tokens == 300_000 - 8_000

    def test_a_configured_window_overrides_the_working_budget(self) -> None:
        assert ContextBudget.for_model("gpt-6.1-sol", context_window=922_000).max_tokens == 922_000 - 8_000

    def test_a_model_the_table_lacks_uses_its_configured_window(self) -> None:
        assert ContextBudget.for_model("gpt-7-preview", context_window=400_000).max_tokens == 400_000 - 8_000

    def test_model_spec_carries_no_window_by_default(self) -> None:
        assert ModelSpec(model="m").context_window is None


class TestSmallWindows:
    """A local model's window is too small to give 8k of it to the reply."""

    @pytest.mark.parametrize(("window", "budget"), [
        (8_192, 6_144), (9_000, 6_750), (16_384, 12_288), (32_768, 24_768), (128_000, 120_000),
    ])
    def test_at_least_three_quarters_of_a_window_stays_for_the_prompt(self, window: int, budget: int) -> None:
        sized = ContextBudget.for_model("local-model", context_window=window)

        assert sized.max_tokens == budget
        assert sized.max_tokens + sized.reserved_output_tokens == window

    @pytest.mark.parametrize("window", [1, 512, 1_000, 4_096])
    def test_no_floor_lifts_the_budget_past_the_window(self, window: int) -> None:
        sized = ContextBudget.for_model("local-model", context_window=window)

        assert 0 < sized.effective_max_tokens <= window
        assert sized.max_tokens + sized.reserved_output_tokens == window

    def test_the_system_prompt_and_tools_come_off_the_message_budget(self) -> None:
        sized = ContextBudget.for_model("local-model", context_window=8_192, reserved_prompt_tokens=1_800)

        assert sized.max_tokens == 8_192 - 2_048 - 1_800

    @pytest.mark.parametrize("prompt", [0, 1_800, 6_144, 7_000, 8_000, 8_191])
    def test_prompt_messages_and_reply_fit_whenever_the_prompt_does(self, prompt: int) -> None:
        sized = ContextBudget.for_model("local-model", context_window=8_192, reserved_prompt_tokens=prompt)

        assert sized.max_tokens >= 1
        assert prompt + sized.max_tokens + sized.reserved_output_tokens <= 8_192

    def test_a_large_prompt_shares_what_it_leaves_between_messages_and_reply(self) -> None:
        sized = ContextBudget.for_model("local-model", context_window=8_192, reserved_prompt_tokens=7_000)

        assert (sized.max_tokens, sized.reserved_output_tokens) == (596, 596)

    def test_a_prompt_over_the_window_leaves_one_token(self) -> None:
        sized = ContextBudget.for_model("local-model", context_window=8_192, reserved_prompt_tokens=9_000)

        assert (sized.max_tokens, sized.reserved_output_tokens) == (1, 0)

    def test_large_windows_keep_the_full_reply_reserve_beside_a_large_prompt(self) -> None:
        sized = ContextBudget.for_model("claude-opus-5", reserved_prompt_tokens=20_000)

        assert (sized.max_tokens, sized.reserved_output_tokens) == (300_000 - 20_000 - 8_000, 8_000)

    def test_table_windows_keep_the_full_output_reserve(self) -> None:
        assert ContextBudget.for_model("claude-opus-5").reserved_output_tokens == 8_000
        assert ContextBudget.for_model(None).max_tokens == 120_000


async def _run(
    spec_model: ModelSpec, *, seen: list[ContextBudget], system_prompt: str = "You help.", tools: list[Tool] | None = None,
) -> ScriptedTransport:
    async def _record(ctx: ModelCallContext, next_fn: Callable[[], Awaitable[None]]) -> None:
        seen.append(ctx.budget)
        await next_fn()

    hooks = HookRegistry()
    hooks.on(HookEvent.PRE_MODEL).use(_record)
    transport = ScriptedTransport().add_text("done")
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    registry = ToolRegistry()
    for tool in tools or []:
        registry.register_tool(tool)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=registry, hooks=hooks)
    spec = AgentSpec(name="a", system_prompt=system_prompt, model=spec_model, loop=ReActLoop(), max_turns=2)

    await Agent(spec, runtime).run(Goal(description="hi"))
    return transport


async def _budget_seen_by_the_shapers(spec_model: ModelSpec) -> ContextBudget:
    seen: list[ContextBudget] = []
    await _run(spec_model, seen=seen)
    assert seen
    return seen[0]


class TestAgentTurn:
    async def test_the_shapers_get_the_configured_window(self) -> None:
        budget = await _budget_seen_by_the_shapers(
            ModelSpec(provider="scripted", model="claude-opus-5-5", context_window=1_000_000),
        )

        assert budget.max_tokens == 1_000_000 - 8_000 - budget.reserved_prompt_tokens

    async def test_without_one_the_shapers_get_the_table_window(self) -> None:
        budget = await _budget_seen_by_the_shapers(ModelSpec(provider="scripted", model="claude-opus-5-5"))

        assert budget.max_tokens == 300_000 - 8_000 - budget.reserved_prompt_tokens

    async def test_the_whole_call_fits_an_8k_window(self) -> None:
        """The system prompt and tool schemas go on the same call as the
        messages, so the messages get only what they leave of the window."""
        seen: list[ContextBudget] = []

        transport = await _run(
            ModelSpec(provider="scripted", model="local-model", context_window=8_192), seen=seen,
            system_prompt="Answer from the user's documents. " * 180, tools=[_NoteTool()],
        )

        sent = transport.calls[0]
        prompt = count_text_tokens(sent["system"]) + count_schema_tokens(sent["tools"])
        budget = seen[0]
        assert prompt > 1_500
        assert budget.reserved_prompt_tokens == prompt
        assert prompt + budget.max_tokens + budget.reserved_output_tokens <= 8_192


async def _shape(shaper: Middleware[ModelCallContext], messages: list[Message], budget: ContextBudget) -> list[Message]:
    ctx = ModelCallContext(messages=list(messages), budget=budget)

    async def _next() -> None:
        return None

    await shaper(ctx, _next)
    return ctx.messages


_8K = ContextBudget.for_model("local-model", context_window=8_192)
# About 2,000 tokens at the shapers' four characters a token: fits an 8k window.
_LONG = "word " * 1_600


class TestShapersOnAnEightKWindow:
    """With the whole 8k given to the reply, the budget floored at 1,000 tokens:
    the shapers dropped what the model had just read, and the last-resort guard
    could not fit a single long question."""

    async def test_the_sliding_window_keeps_a_search_result_that_fits(self) -> None:
        messages: list[Message] = [
            UserMessage(content="What changed in the Q3 plan?"),
            AssistantMessage(content="", tool_calls=[ToolCall(id="c1", name="search", arguments={"query": "Q3"})]),
            ToolMessage(content=_LONG, tool_call_id="c1"),
        ]

        shaped = await _shape(shape_sliding_window(), messages, _8K)

        assert shaped == messages

    async def test_a_short_question_fits_beside_a_7000_token_prompt(self) -> None:
        """About 1,200 tokens of the window are left; a 1-token budget dropped
        the question that fits in them."""
        budget = ContextBudget.for_model("local-model", context_window=8_192, reserved_prompt_tokens=7_000)
        messages: list[Message] = [UserMessage(content="What changed in the Q3 plan, and who approved it?")]

        shaped = await _shape(shape_synthesis_guard(), messages, budget)

        assert shaped == messages

    async def test_the_last_resort_guard_lets_a_long_question_through(self) -> None:
        messages: list[Message] = [UserMessage(content=_LONG)]

        shaped = await _shape(shape_synthesis_guard(), messages, _8K)

        assert shaped == messages
