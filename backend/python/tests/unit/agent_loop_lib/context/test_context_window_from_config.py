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
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.hooks.middleware.context import ModelCallContext


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

    def test_model_spec_carries_no_window_by_default(self) -> None:
        assert ModelSpec(model="m").context_window is None


async def _budget_seen_by_the_shapers(spec_model: ModelSpec) -> ContextBudget:
    seen: list[ContextBudget] = []

    async def _record(ctx: ModelCallContext, next_fn: Callable[[], Awaitable[None]]) -> None:
        seen.append(ctx.budget)
        await next_fn()

    hooks = HookRegistry()
    hooks.on(HookEvent.PRE_MODEL).use(_record)
    transports = TransportRegistry()
    transports.register("scripted", lambda: ScriptedTransport().add_text("done"))
    runtime = AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry(), hooks=hooks)
    spec = AgentSpec(name="a", system_prompt="You help.", model=spec_model, loop=ReActLoop(), max_turns=2)

    await Agent(spec, runtime).run(Goal(description="hi"))

    assert seen
    return seen[0]


class TestAgentTurn:
    async def test_the_shapers_get_the_configured_window(self) -> None:
        budget = await _budget_seen_by_the_shapers(
            ModelSpec(provider="scripted", model="claude-opus-5-5", context_window=1_000_000),
        )

        assert budget.max_tokens == 1_000_000 - 8_000

    async def test_without_one_the_shapers_get_the_table_window(self) -> None:
        budget = await _budget_seen_by_the_shapers(ModelSpec(provider="scripted", model="claude-opus-5-5"))

        assert budget.max_tokens == 300_000 - 8_000
