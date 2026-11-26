"""G-52 / GA-27: the date and locale lines at the context tail leave the provider's cached prefix unchanged.

A replay of the system prompts a conversation sends over several days, in two locales: the block the provider caches
(``build_blocks``' stable block, ``cache_control`` on it) is byte-identical every turn, so the cached-prefix ratio
equals the baseline with no date or locale line; only the uncached tail differs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from tests.unit.agents.adapter.conftest import make_context

_QUIET = {
    "_build_knowledge_context": MagicMock(return_value=""),
    "build_capability_summary": MagicMock(return_value=""),
    "_format_user_context": MagicMock(return_value=""),
}
# One conversation's turns over three days and a DST change, the person switching the app's language midway.
_TURNS = (
    {"current_time": "2026-10-24T09:00:00+02:00", "locale": "de-DE"},
    {"current_time": "2026-10-25T09:00:00+01:00", "locale": "de-DE"},
    {"current_time": "2026-10-26T17:45:00+01:00", "locale": "es-ES"},
    {"current_time": "2026-10-26T17:46:00+01:00", "locale": None},
)


def _blocks(timezone: str | None = "Europe/Berlin", **context: object) -> tuple[str, str]:
    builder = PipesHubPromptBuilder(make_context(timezone=timezone, has_knowledge=True, **context))
    spec = AgentSpec(
        name="pipeshub-agent", system_prompt=builder,
        model=ModelSpec(provider="lc", model="claude-opus-5-5"), loop=ReActLoop(), max_turns=4,
    )
    with patch.multiple("app.agents.agent_loop.prompt_builder", **_QUIET):
        return builder.build_blocks(
            spec, AgentRuntime(tool_registry=ToolRegistry()), Goal(description="Who owns HarborBank?"), [], {}
        )


def _cached_ratio(prompts: list[tuple[str, str]]) -> float:
    """The cached-prefix ratio of a replay: of the bytes every turn after the first marks cacheable (its stable
    block), the share a prefix cache reads, i.e. those whose block is byte-identical to the turn before's."""
    cached = cacheable = 0
    for earlier, later in zip(prompts, prompts[1:], strict=False):
        cacheable += len(later[0])
        cached += len(later[0]) if later[0] == earlier[0] else 0
    return cached / cacheable


def test_g52_cache_1_the_cached_block_is_the_same_every_turn() -> None:
    prompts = [_blocks(**turn) for turn in _TURNS]
    assert len({stable for stable, _ in prompts}) == 1
    assert all("**Current time**" not in stable for stable, _ in prompts)
    assert "Write your answer in German" in prompts[0][1]
    assert "Write your answer in Spanish" in prompts[2][1]
    assert len({volatile for _, volatile in prompts}) == len(_TURNS)


def test_g52_cache_2_the_cached_prefix_ratio_equals_the_baseline_without_the_lines() -> None:
    with_lines = [_blocks(**turn) for turn in _TURNS]
    baseline = [_blocks(timezone=None) for _ in _TURNS]
    assert _cached_ratio(with_lines) == _cached_ratio(baseline) == 1.0
    # The same bytes are cached with and without the lines; they only add to the uncached tail.
    assert [s for s, _ in with_lines] == [s for s, _ in baseline]
    assert all("**Current time**" not in v and "Write your answer in" not in v for _, v in baseline)
    assert all(len(v) > len(b) for (_, v), (_, b) in zip(with_lines, baseline, strict=True))


def test_g52_cache_3_a_line_in_the_cached_block_would_be_caught() -> None:
    """The ratio is not vacuous: a date written into the cached block (EV4's regression) drops it below 1."""
    prompts = [(f"stable {turn['current_time']}", "tail") for turn in _TURNS]
    assert _cached_ratio(prompts) == 0.0
