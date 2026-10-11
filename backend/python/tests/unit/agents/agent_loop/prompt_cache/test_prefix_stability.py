"""The request prefix a provider caches stays byte-identical where it should:
from one turn to the next of a question, across questions of one agent, and
across processes."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.prompt_cache import AnthropicCacheStrategy
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.agent_loop.prompt_cache.conftest import anthropic_reply

FIVE_MIN = {"type": "ephemeral"}
_QUIET_SECTIONS = {
    "_build_knowledge_context": MagicMock(return_value=""),
    "build_capability_summary": MagicMock(return_value=""),
    "_format_user_context": MagicMock(return_value=""),
}


class _LookupTool(Tool):
    @property
    def name(self) -> str:
        return "lookup"

    @property
    def short_description(self) -> str:
        return "Looks a name up"

    @property
    def description(self) -> str:
        return "Looks a name up in the directory."

    @property
    def path(self) -> str:
        return "/toolsets/test/lookup"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="name", type=ParameterType.STRING, description="who")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data=f"{kwargs['name']} works in finance. " * 20)


def _spec(builder: PipesHubPromptBuilder) -> AgentSpec:
    return AgentSpec(
        name="pipeshub-agent", system_prompt=builder,
        model=ModelSpec(provider="lc", model="claude-opus-5-5"), loop=ReActLoop(), max_turns=6,
    )


def _blocks(builder: PipesHubPromptBuilder, goal: Goal, extra: dict[str, str] | None = None) -> tuple[str, str]:
    runtime = AgentRuntime(tool_registry=ToolRegistry())
    with patch.multiple("app.agents.agent_loop.prompt_builder", **_QUIET_SECTIONS):
        return builder.build_blocks(_spec(builder), runtime, goal, [], extra or {})


class TestAcrossQuestions:
    def test_the_stable_block_does_not_depend_on_the_question(self) -> None:
        plain = make_context(timezone="Asia/Kolkata", has_knowledge=True)
        first = _blocks(PipesHubPromptBuilder(plain), Goal(description="Who owns HarborBank?"))
        with_goal = Goal(
            description="Compare Q3 revenue", requirements=["Use the finance reports"],
            success_criteria=["Both quarters named"], gaps=["Which region?"],
        )
        attached = make_context(timezone="Asia/Kolkata", has_knowledge=True)
        attached.tool_state["attachments"] = [{"recordName": "q3.pdf", "mimeType": "application/pdf"}]
        attached.tool_state["final_results"] = [{"virtual_record_id": "v1"}]
        second = _blocks(PipesHubPromptBuilder(attached), with_goal)

        assert first[0] == second[0]
        assert "Use the finance reports" in second[1]
        assert "q3.pdf" in second[1]

    def test_an_agent_without_knowledge_gets_citation_rules_only_with_material(self) -> None:
        bare = _blocks(PipesHubPromptBuilder(make_context()), Goal(description="hi"))
        attached = make_context()
        attached.tool_state["attachments"] = [{"recordName": "notes.txt"}]
        with_file = _blocks(PipesHubPromptBuilder(attached), Goal(description="summarize"))

        assert with_file[0] == bare[0]
        assert "## Citation" not in bare[1]
        assert len(with_file[1]) > len(bare[1])

    def test_attachments_still_come_first(self) -> None:
        context = make_context(has_knowledge=True)
        context.tool_state["attachments"] = [{"recordName": "q3.pdf"}]
        _, volatile = _blocks(PipesHubPromptBuilder(context), Goal(description="summarize"))
        assert "before the sources listed under Finding Information" in volatile


class TestTimeIsFrozenPerRequest:
    def test_the_clock_is_read_once_per_builder(self) -> None:
        ticks = iter(["2026-10-11T09:00:00.000001Z", "2026-10-11T09:00:07.250000Z", "2026-10-11T09:01:00Z"])
        builder = PipesHubPromptBuilder(make_context(timezone="Asia/Kolkata"))
        with patch("app.utils.time_conversion._utc_reference_now_iso", side_effect=lambda: next(ticks)):
            first = _blocks(builder, Goal(description="q"))
            second = _blocks(builder, Goal(description="q"))
            later = _blocks(PipesHubPromptBuilder(make_context(timezone="Asia/Kolkata")), Goal(description="q"))
        assert first == second
        assert "09:00:00.000001" in first[1]
        assert "09:00:07" in later[1]


class TestTurnToTurn:
    async def test_each_request_extends_the_one_before(self, endpoint: Any) -> None:  # noqa: ANN401
        from langchain_anthropic import ChatAnthropic

        endpoint.replies = [
            anthropic_reply([{"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"name": "Ann"}}],
                            "tool_use"),
            anthropic_reply([{"type": "tool_use", "id": "toolu_2", "name": "lookup", "input": {"name": "Bo"}}],
                            "tool_use"),
            anthropic_reply([{"type": "text", "text": "Ann and Bo work in finance."}]),
        ]
        llm = ChatAnthropic(model="claude-opus-5-5", api_key="test", base_url=endpoint.url, max_retries=0)
        transport = LangChainTransport(llm, model_name="claude-opus-5-5",
                                       cache_strategy=AnthropicCacheStrategy(FIVE_MIN, top_level=True))
        builder = PipesHubPromptBuilder(make_context(timezone="Asia/Kolkata", has_knowledge=True))
        registry = ToolRegistry()
        registry.register_tool(_LookupTool())
        transports = TransportRegistry()
        transports.register("lc", lambda: transport)
        agent = Agent(_spec(builder), AgentRuntime(transport_registry=transports, tool_registry=registry))

        with patch.multiple("app.agents.agent_loop.prompt_builder", **_QUIET_SECTIONS):
            await agent.run(Goal(description="Where do Ann and Bo work?", requirements=["Name both"]))

        bodies = [_unmarked(body) for body in endpoint.bodies]
        assert len(bodies) == 3
        for earlier, later in zip(bodies, bodies[1:], strict=False):
            assert later["tools"] == earlier["tools"]
            assert later["system"] == earlier["system"]
            assert later["messages"][: len(earlier["messages"])] == earlier["messages"]
        assert endpoint.bodies[0]["system"][0]["cache_control"] == FIVE_MIN
        assert "Name both" not in endpoint.bodies[0]["system"][0]["text"]


def _unmarked(node: Any) -> Any:  # noqa: ANN401
    if isinstance(node, dict):
        out = {k: _unmarked(v) for k, v in node.items() if k != "cache_control"}
        if isinstance(out.get("content"), str):
            out["content"] = [{"type": "text", "text": out["content"]}]
        return out
    if isinstance(node, list):
        return [_unmarked(item) for item in node]
    return node


_TOOL_ORDER_SCRIPT = textwrap.dedent('''
    import json
    from types import SimpleNamespace
    from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
    from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agent_loop_lib.tools.base import Tool, ToolOutput
    from app.agent_loop_lib.tools.registry import ToolRegistry

    def make(n):
        class T(Tool):
            name = n
            short_description = description = "tool " + n
            path = "/toolsets/t/" + n
            parameters = []
            async def execute(self, **kw):
                return ToolOutput(success=True, data="")
        return T()

    registry = ToolRegistry()
    names = [f"tool_{word}" for word in "kilo alpha zulu echo mike bravo yankee delta".split()]
    for name in names:
        registry.register_tool(make(name))
    registry.register_toolset("grouped", "Grouped tools", names[:2])
    spec = AgentSpec(name="a", system_prompt="s", model=ModelSpec(provider="x", model="y"))
    agent = SimpleNamespace(visible_tools=None)
    print(json.dumps([t.name for t in tool_schemas_for_turn(agent, spec, AgentRuntime(tool_registry=registry))]))
''')


def test_tool_order_is_the_same_in_every_process() -> None:
    root = Path(__file__).resolve().parents[5]
    orders = set()
    for seed in range(4):
        result = subprocess.run(
            [sys.executable, "-c", _TOOL_ORDER_SCRIPT], capture_output=True, text=True, check=False,
            env={"PYTHONHASHSEED": str(seed), "PYTHONPATH": str(root), "PATH": ""}, cwd=root, timeout=120,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        orders.add(result.stdout.strip().splitlines()[-1])
    assert len(orders) == 1
    names = json.loads(orders.pop())
    assert names == sorted(names)
    assert len(names) == 6
