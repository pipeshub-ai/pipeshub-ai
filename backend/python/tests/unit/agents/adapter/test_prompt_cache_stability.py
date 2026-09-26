"""Cache-stable prompt prefix (P0.14).

The stable block (Band A + B) must be byte-identical across turns and
follow-up messages when only per-message content changes — goal brief,
todos, time, request context, preloaded skills/tools — and none of that
content may be dropped from the full prompt.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.agent.prompt import DefaultPromptBuilder
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal, Todo
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.section_order import PIPESHUB_SECTION_ORDER, Volatility
from tests.unit.agents.adapter.conftest import make_context

_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=" -_"),
    min_size=3, max_size=40,
).map(lambda s: f"x{s.strip()}x")
_texts = st.lists(_text, max_size=3)


def _spec() -> AgentSpec:
    return AgentSpec(
        name="pipeshub-agent",
        system_prompt="BASE_REACT_PROMPT",
        tool_names=[],
        model=ModelSpec(provider="scripted", model="scripted-model"),
    )


def _blocks(goal: Goal, todos: list[Todo], time_text: str, extra: dict[str, str]) -> tuple[str, str]:
    context = make_context(instructions="Always be terse.", custom_instructions="Org rule.")
    patches = {
        "_build_knowledge_context": MagicMock(return_value="## Knowledge\nKB-1"),
        "build_llm_time_context": MagicMock(return_value=time_text),
        "build_capability_summary": MagicMock(return_value="## Capabilities\ncap"),
        "_format_user_context": MagicMock(return_value="## User\nuser-1"),
    }
    with patch.multiple("app.agents.agent_loop.prompt_builder", **patches), \
         patch("app.modules.agents.context.tool_surface.sandbox_network_enabled",
               MagicMock(return_value=False)):
        return PipesHubPromptBuilder(context).build_blocks(
            _spec(), AgentRuntime(tool_registry=ToolRegistry()), goal, todos, extra,
        )


_BASELINE_STABLE, _ = _blocks(Goal(description="first"), [], "Current time: T0", {})


def test_goal_brief_is_turn_volatile() -> None:
    assert dict(PIPESHUB_SECTION_ORDER)["goal_brief"] == Volatility.TURN


@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(
    description=_text,
    requirements=_texts,
    success=_texts,
    gaps=_texts,
    constraints=_texts,
    todos=_texts,
    time_text=_text,
    preloaded_skill=st.one_of(st.none(), _text),
    preloaded_tool=st.one_of(st.none(), _text),
)
def test_stable_prefix_byte_identical_when_only_turn_content_changes(
    description, requirements, success, gaps, constraints, todos,
    time_text, preloaded_skill, preloaded_tool,
) -> None:
    goal = Goal(
        description=description, requirements=requirements,
        success_criteria=success, gaps=gaps, constraints=constraints,
    )
    extra = {}
    if preloaded_skill:
        extra["preloaded_skills"] = preloaded_skill
    if preloaded_tool:
        extra["preloaded_tools"] = preloaded_tool

    stable, volatile = _blocks(goal, [Todo(content=t) for t in todos], time_text, extra)

    assert stable == _BASELINE_STABLE
    # Nothing dropped: every per-turn fact still reaches the model, in the tail.
    for fragment in [*requirements, *success, *gaps, *constraints, time_text,
                     *(v for v in extra.values())]:
        assert fragment in volatile


def test_goal_brief_rendered_after_static_rules() -> None:
    stable, volatile = _blocks(
        Goal(description="d", requirements=["REQ-1"]), [], "Current time: T1", {},
    )
    assert "REQ-1" not in stable
    assert "## Your Goal" in volatile
    assert volatile.index("Current time: T1") < volatile.index("## Your Goal")


@settings(max_examples=40, deadline=None)
@given(description=_text, requirements=_texts, todos=_texts)
def test_default_builder_prefix_stable_before_goal_and_todos(description, requirements, todos) -> None:
    spec = AgentSpec(
        name="child", system_prompt="You are a researcher.",
        model=ModelSpec(provider="scripted", model="m"), mode="plan", output_style="concise",
    )
    runtime = AgentRuntime(tool_registry=ToolRegistry())

    def render(goal: Goal, todo_items: list[Todo]) -> str:
        return DefaultPromptBuilder().build(spec, runtime, goal, todo_items, {})

    baseline = render(Goal(description="seed"), [])
    prefix = baseline[: baseline.index("Goal: seed")]
    rendered = render(Goal(description=description, requirements=requirements),
                      [Todo(content=t) for t in todos])

    assert rendered.startswith(prefix)
    assert f"Goal: {description}" in rendered
    for t in todos:
        assert t in rendered
