"""`AgentTool._finalize_output`/`.finalize_output` (p4-output-schema): the
static agent-as-tool composition path returns plain string content (unlike
`spawn_agent`'s dict-shaped result, see `test_child_result_content.py`), so
a child's `needs_input` escalation has to be folded into that string
instead of a separate dict key nothing here would read."""

from __future__ import annotations

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool

_GOAL = Goal(description="x")


def _make_tool(*, result_note: str | None = None) -> AgentTool:
    spec = AgentSpec(
        name="domain-agent",
        system_prompt="You are a domain agent.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
    )
    return AgentTool(spec, runtime=None, result_note=result_note)  # type: ignore[arg-type]


class TestFinalizeOutput:
    def test_plain_result_is_unchanged(self) -> None:
        tool = _make_tool()
        result = AgentResult(goal=_GOAL, output="the answer", success=True)
        assert tool.finalize_output(result) == "the answer"

    def test_result_note_is_still_applied(self) -> None:
        tool = _make_tool(result_note="[NOTE] cite sources.")
        result = AgentResult(goal=_GOAL, output="the answer", success=True)
        assert tool.finalize_output(result) == "the answer\n\n[NOTE] cite sources."

    def test_needs_input_appends_an_escalation_suffix(self) -> None:
        tool = _make_tool()
        result = AgentResult(
            goal=_GOAL, output="partial answer", success=True,
            needs_input="the target project key",
        )
        output = tool.finalize_output(result)
        assert output.startswith("partial answer")
        assert "[ESCALATION]" in output
        assert "the target project key" in output

    def test_needs_input_and_result_note_both_apply(self) -> None:
        tool = _make_tool(result_note="[NOTE] cite sources.")
        result = AgentResult(
            goal=_GOAL, output="partial answer", success=True,
            needs_input="the target project key",
        )
        output = tool.finalize_output(result)
        assert "[NOTE] cite sources." in output
        assert "[ESCALATION]" in output

    def test_no_needs_input_means_no_escalation_suffix(self) -> None:
        tool = _make_tool()
        result = AgentResult(goal=_GOAL, output="the answer", success=True)
        assert "[ESCALATION]" not in tool.finalize_output(result)

    def test_apply_result_note_still_works_standalone(self) -> None:
        """Back-compat: `apply_result_note` (used by `run_spawned_child`'s
        precedent-setting caller before `finalize_output` existed, and
        still tested against directly in `test_spawn_agent_direct_dispatch.
        py`) is unaffected by adding `finalize_output` alongside it."""
        tool = _make_tool(result_note="[NOTE] cite sources.")
        assert tool.apply_result_note("the answer") == "the answer\n\n[NOTE] cite sources."
