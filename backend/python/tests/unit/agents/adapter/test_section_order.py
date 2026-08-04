"""Tests for Phase 4 section-order and cache-split.

Per plan Part 8:
- `build()` equals `"\\n\\n".join(b for b in build_blocks() if b)`.
- Every name in `PIPESHUB_SECTION_ORDER` is declared once; no duplicates.
- `build_blocks()` returns a 2-tuple `(stable, volatile)` where `stable`
  does NOT contain turn-volatile content (time context) and `volatile` does.
- Band C sections (TURN) always come after Band A+B sections in the ordering.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.section_order import (
    PIPESHUB_SECTION_ORDER,
    SECTION_NAMES,
    TURN_VOLATILE_NAMES,
    Volatility,
)
from tests.unit.agents.adapter.conftest import make_context


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_runtime() -> AgentRuntime:
    return AgentRuntime(tool_registry=ToolRegistry())


def _make_spec(tool_names: list[str] | None = None) -> AgentSpec:
    return AgentSpec(
        name="pipeshub-agent",
        system_prompt="BASE_REACT_PROMPT",
        tool_names=tool_names or [],
        model=ModelSpec(provider="scripted", model="scripted-model"),
    )


_PATCH_PROMPT_BUILDER = dict(
    _build_knowledge_context=MagicMock(return_value=""),
    build_llm_time_context=MagicMock(return_value="Current time: 2026-01-01T00:00:00Z"),
    build_capability_summary=MagicMock(return_value=""),
    _format_user_context=MagicMock(return_value=""),
)


def _run_build(context, *, goal: Goal | None = None, sandbox_has_network: bool = False) -> str:
    builder = PipesHubPromptBuilder(context)
    spec = _make_spec()
    runtime = _make_runtime()
    with patch.multiple("app.agents.agent_loop.prompt_builder", **_PATCH_PROMPT_BUILDER), \
         patch("app.modules.agents.context.tool_surface.sandbox_network_enabled",
               MagicMock(return_value=sandbox_has_network)):
        return builder.build(spec, runtime, goal or Goal(description="hello"), [], {})


def _run_build_blocks(
    context,
    *,
    goal: Goal | None = None,
    sandbox_has_network: bool = False,
    extra_sections: dict | None = None,
    time_output: str = "Current time: 2026-01-01T00:00:00Z",
) -> tuple[str, str]:
    builder = PipesHubPromptBuilder(context)
    spec = _make_spec()
    runtime = _make_runtime()
    patches = dict(
        _build_knowledge_context=MagicMock(return_value=""),
        build_llm_time_context=MagicMock(return_value=time_output),
        build_capability_summary=MagicMock(return_value=""),
        _format_user_context=MagicMock(return_value=""),
    )
    with patch.multiple("app.agents.agent_loop.prompt_builder", **patches), \
         patch("app.modules.agents.context.tool_surface.sandbox_network_enabled",
               MagicMock(return_value=sandbox_has_network)):
        return builder.build_blocks(spec, runtime, goal or Goal(description="hello"), [], extra_sections or {})


# ---------------------------------------------------------------------------
# section_order.py structure
# ---------------------------------------------------------------------------

class TestSectionOrderModule:

    def test_pipeshub_section_order_is_nonempty(self) -> None:
        assert len(PIPESHUB_SECTION_ORDER) > 10

    def test_all_volatility_values_covered(self) -> None:
        bands = {v for _, v in PIPESHUB_SECTION_ORDER}
        assert Volatility.STATIC in bands
        assert Volatility.CONV in bands
        assert Volatility.TURN in bands

    def test_turn_volatile_names_subset_of_all_names(self) -> None:
        assert TURN_VOLATILE_NAMES.issubset(set(SECTION_NAMES))

    def test_turn_volatile_names_nonempty(self) -> None:
        assert len(TURN_VOLATILE_NAMES) >= 3

    def test_stable_sections_before_volatile(self) -> None:
        """All TURN sections must come AFTER all non-TURN sections."""
        hit_turn = False
        for name, volatility in PIPESHUB_SECTION_ORDER:
            if volatility == Volatility.TURN:
                hit_turn = True
            elif hit_turn:
                pytest.fail(
                    f"Non-TURN section '{name}' comes after a TURN section — "
                    "all Band A + B sections must precede all Band C sections"
                )

    def test_answer_confidence_in_band_b_not_turn(self) -> None:
        """answer_confidence must be Band B (CONV), not TURN."""
        order_dict = dict(PIPESHUB_SECTION_ORDER)
        assert order_dict.get("answer_confidence") == Volatility.CONV

    def test_time_context_is_turn(self) -> None:
        order_dict = dict(PIPESHUB_SECTION_ORDER)
        assert order_dict.get("time_context") == Volatility.TURN

    def test_identity_is_static(self) -> None:
        order_dict = dict(PIPESHUB_SECTION_ORDER)
        assert order_dict.get("identity") == Volatility.STATIC

    def test_knowledge_sources_is_conv(self) -> None:
        order_dict = dict(PIPESHUB_SECTION_ORDER)
        assert order_dict.get("knowledge_sources") == Volatility.CONV

    def test_no_duplicate_section_names(self) -> None:
        names = [name for name, _ in PIPESHUB_SECTION_ORDER]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# build() == "\n\n".join(build_blocks())
# ---------------------------------------------------------------------------

class TestBuildEqualsBuildBlocks:

    def test_no_sources(self) -> None:
        ctx = make_context()
        full = _run_build(ctx)
        stable, volatile = _run_build_blocks(ctx)
        blocks_joined = "\n\n".join(b for b in (stable, volatile) if b)
        assert full == blocks_joined

    def test_with_time_context(self) -> None:
        """time_context is turn-volatile; it must appear in volatile block only."""
        ctx = make_context()
        stable, volatile = _run_build_blocks(ctx)
        assert "Current time:" not in stable
        assert "Current time:" in volatile

    def test_additional_context_is_volatile(self) -> None:
        """Constraints from goal.constraints go into the volatile block."""
        ctx = make_context()
        goal = Goal(description="hello", constraints=["Reuse previous results."])
        stable, volatile = _run_build_blocks(ctx, goal=goal)
        assert "Reuse previous results." not in stable
        assert "Reuse previous results." in volatile


# ---------------------------------------------------------------------------
# Cache split: stable vs volatile
# ---------------------------------------------------------------------------

class TestCacheSplit:

    def test_stable_block_contains_identity(self) -> None:
        ctx = make_context()
        stable, _ = _run_build_blocks(ctx)
        assert "PipesHub" in stable

    def test_stable_block_contains_answer_confidence_when_flag_on(self) -> None:
        """answer_confidence renders in the stable (Band B) block when the flag is on."""
        from unittest.mock import patch
        ctx = make_context()
        with patch("app.agents.agent_loop.prompt_builder.confidence_enabled", return_value=True):
            stable, _ = _run_build_blocks(ctx)
        assert "Confidence:" in stable

    def test_stable_block_omits_answer_confidence_when_flag_off(self) -> None:
        """When PIPESHUB_ENABLE_CONFIDENCE is false the section is absent."""
        from unittest.mock import patch
        ctx = make_context()
        with patch("app.agents.agent_loop.prompt_builder.confidence_enabled", return_value=False):
            stable, _ = _run_build_blocks(ctx)
        assert "## Answer Confidence" not in stable

    def test_volatile_block_empty_when_no_volatile_content(self) -> None:
        """When time_context is empty and no constraints/attachments, volatile==''."""
        ctx = make_context()
        stable, volatile = _run_build_blocks(ctx, time_output="")
        assert volatile == ""

    def test_extra_sections_land_in_volatile_block(self) -> None:
        ctx = make_context()
        extra = {"my_hook_section": "## Hook Output\nSome hook content."}
        stable, volatile = _run_build_blocks(ctx, time_output="", extra_sections=extra)
        assert "Hook Output" not in stable
        assert "Hook Output" in volatile
