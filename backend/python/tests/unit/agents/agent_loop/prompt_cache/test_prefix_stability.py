"""The request prefix a provider caches stays byte-identical where it should:
from one turn to the next of a question, across questions of one agent, and
across processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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


# ── Answer-rule switches, per agent mode ────────────────────────────────────

_ANSWER_RULE_SWITCHES = ("ANSWER_COMPOSITION_RULES", "THOROUGH_RETRIEVAL", "MULTI_STEP_ANSWER_RULES")
# Other switches that change prompt text; pinned off so only the answer rules vary.
_OTHER_PROMPT_SWITCHES = (
    "HIGH_ACCURACY_MODE", "KEYWORD_SEARCH_QUERIES", "TARGETED_RETRIEVAL", "TIMELINE_SUMMARIES",
)
_MODES = ("quick", "react", "planExecute", "deep", "internal_search")
_SNAPSHOT = Path(__file__).parent / "snapshots" / "answer_rules_sections.json"
_SECTIONS = ("agent_instructions", "finding_information")
_QUESTIONS = (
    "Who owns the HarborBank account?",
    "Compare the Q3 incident counts for EMEA and APAC, and say which runbook changed.",
)


def _knowledge_graph_registry() -> MagicMock:
    from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph

    registry = MagicMock()
    registry.get_all_toolsets.return_value = {
        "knowledgegraph": {"class": KnowledgeGraph, "isInternal": True, "description": "Knowledge graph",
                           "essential": True},
    }
    return registry


async def _mode_request(mode: str, question: str) -> tuple[str, dict[str, str | None], list[dict[str, Any]]]:
    """Stable block, the answer-rule sections, and tool schemas for one
    request, built the way production builds them (`internal_search` the way
    `chat_modes.bridge` does: the quick loop, the mode text, and the fetch
    tool granted after a prefetch that found records)."""
    from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
    from app.agent_loop_lib.roles.prompt_template import PromptTemplate
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from app.agents.chat_modes.bridge import _apply_mode_instructions
    from app.agents.chat_modes.policy import INTERNAL_SEARCH_POLICY
    from tests.unit.agents.adapter.conftest import FakeChatModel

    context = make_context(llm=FakeChatModel(), has_knowledge=True, timezone="Asia/Kolkata")
    context.tool_state["has_knowledge"] = True
    loop_mode = mode
    if mode == "internal_search":
        context.tool_state["entity_vector_store"] = MagicMock()
        _apply_mode_instructions(context.tool_state, INTERNAL_SEARCH_POLICY)
        context.instructions = context.tool_state["instructions"]
        loop_mode = INTERNAL_SEARCH_POLICY.loop_chat_mode

    with (
        patch("app.agents.registry.toolset_registry.get_toolset_registry",
              return_value=_knowledge_graph_registry()),
        patch("app.agents.agent_loop.tool_loader.ClientFactoryRegistry.get_factory", return_value=None),
    ):
        agent, runtime, goal, _ = await PipesHubAgentFactory().create(
            context, context.llm, loop_mode, query=question,
        )
    if mode == "internal_search":
        from app.agents.agent_loop.hooks import ensure_fetch_full_record_available

        # What the bridge does once the prefetch found records.
        ensure_fetch_full_record_available(context, registry=runtime.tool_registry)

    captured: dict[str, str | None] = {}
    original_set = PromptTemplate.set

    def _capture(self: PromptTemplate, name: str, value: str | None) -> None:
        if name in _SECTIONS:
            captured[name] = value
        original_set(self, name, value)

    with (
        patch.multiple("app.agents.agent_loop.prompt_builder", **_QUIET_SECTIONS),
        patch.object(PromptTemplate, "set", _capture),
    ):
        stable, _volatile = agent.spec.system_prompt.build_blocks(agent.spec, runtime, goal, [], {})
    schemas = [s.model_dump() for s in tool_schemas_for_turn(agent, agent.spec, runtime)]
    return stable, captured, schemas


def _set_answer_rules(monkeypatch: pytest.MonkeyPatch, *, on: bool) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    for name in _OTHER_PROMPT_SWITCHES:
        # "false", not unset: TARGETED_RETRIEVAL and TIMELINE_SUMMARIES are on
        # when unset.
        monkeypatch.setenv(name, "false")
    for name in _ANSWER_RULE_SWITCHES:
        if on:
            monkeypatch.setenv(name, "true")
        else:
            monkeypatch.delenv(name, raising=False)


class TestAnswerRulesKeepThePrefixStable:
    """The answer-rule switches are deployment settings: on or off, the
    stable block and the tool list must be the same for every question of a
    mode, and the rules may only appear where knowledge retrieval is granted."""

    @pytest.mark.parametrize("on", [False, True])
    @pytest.mark.parametrize("mode", _MODES)
    async def test_stable_block_and_tools_do_not_depend_on_the_question(
        self, monkeypatch: pytest.MonkeyPatch, mode: str, on: bool,
    ) -> None:
        _set_answer_rules(monkeypatch, on=on)
        first_stable, first_sections, first_tools = await _mode_request(mode, _QUESTIONS[0])
        second_stable, second_sections, second_tools = await _mode_request(mode, _QUESTIONS[1])

        assert first_stable == second_stable
        assert first_sections == second_sections
        assert first_tools == second_tools
        for question in _QUESTIONS:
            assert question not in first_stable

    @pytest.mark.parametrize("mode", _MODES)
    async def test_switches_change_no_tool_and_no_other_section(
        self, monkeypatch: pytest.MonkeyPatch, mode: str,
    ) -> None:
        _set_answer_rules(monkeypatch, on=False)
        off_stable, off_sections, off_tools = await _mode_request(mode, _QUESTIONS[0])
        _set_answer_rules(monkeypatch, on=True)
        on_stable, on_sections, on_tools = await _mode_request(mode, _QUESTIONS[0])

        if mode == "internal_search":
            # The fetch tool's skip case follows THOROUGH_RETRIEVAL's full-read policy.
            fetch = "knowledgegraph__fetch_record"
            on_fetch = next(t for t in on_tools if t["name"] == fetch)
            off_fetch = next(t for t in off_tools if t["name"] == fetch)
            assert on_fetch["description"] != off_fetch["description"]
            on_tools = [off_fetch if t["name"] == fetch else t for t in on_tools]
        assert on_tools == off_tools
        for name in _SECTIONS:
            if off_sections.get(name):
                on_stable = on_stable.replace(on_sections[name] or "", off_sections[name])
        assert on_stable == off_stable

    @pytest.mark.parametrize("mode", _MODES)
    async def test_off_is_the_same_under_high_accuracy_mode_with_each_switch_false(
        self, monkeypatch: pytest.MonkeyPatch, mode: str,
    ) -> None:
        _set_answer_rules(monkeypatch, on=False)
        unset = await _mode_request(mode, _QUESTIONS[0])
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
        monkeypatch.setenv("KEYWORD_SEARCH_QUERIES", "false")
        for name in _ANSWER_RULE_SWITCHES:
            monkeypatch.setenv(name, "false")
        explicit = await _mode_request(mode, _QUESTIONS[0])

        assert explicit == unset

    @pytest.mark.parametrize("mode", _MODES)
    async def test_where_each_rule_appears(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        from app.agents.agent_loop.answer_rules import MULTI_STEP_ANSWER_RULES

        _set_answer_rules(monkeypatch, on=True)
        stable, sections, _tools = await _mode_request(mode, _QUESTIONS[0])

        has_retrieval = mode != "deep"
        assert stable.count(MULTI_STEP_ANSWER_RULES) == (1 if has_retrieval else 0)
        assert ("Compose the answer so it stands on its own" in stable) is (mode == "internal_search")
        assert ("read in full the records your answer will rest on" in stable) is (mode == "internal_search")
        assert MULTI_STEP_ANSWER_RULES in (sections.get("finding_information") or "") or not has_retrieval

    async def test_enterprise_search_text_names_only_what_the_first_turn_can_call(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The mode text is not filtered by the grant, so every tool or
        parameter it names must be in this turn's tool schemas."""
        import re

        _set_answer_rules(monkeypatch, on=True)
        _stable, sections, tools = await _mode_request("internal_search", _QUESTIONS[0])
        callable_names = {tool["name"] for tool in tools}
        parameters = {name for tool in tools for name in (tool["input_schema"].get("properties") or {})}
        named = set(re.findall(r"`([^`]+)`", sections["agent_instructions"] or ""))

        assert "knowledgegraph__search_entities" in named
        assert named <= callable_names | parameters, named - callable_names - parameters

    async def test_sections_match_the_reviewed_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every prompt edit to these sections shows up as a reviewed diff.
        Regenerate with UPDATE_PROMPT_SNAPSHOTS=1 after checking the change."""
        actual: dict[str, Any] = {}
        for on in (False, True):
            _set_answer_rules(monkeypatch, on=on)
            for mode in _MODES:
                _stable, sections, tools = await _mode_request(mode, _QUESTIONS[0])
                actual[f"{mode}:{'on' if on else 'off'}"] = {
                    **{name: sections.get(name) for name in _SECTIONS},
                    "tools": [tool["name"] for tool in tools],
                }

        if os.environ.get("UPDATE_PROMPT_SNAPSHOTS") == "1":
            _SNAPSHOT.parent.mkdir(exist_ok=True)
            _SNAPSHOT.write_text(json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        assert actual == json.loads(_SNAPSHOT.read_text())


_STABLE_BLOCK_SCRIPT = textwrap.dedent('''
    import asyncio, hashlib, os
    for name in ("ANSWER_COMPOSITION_RULES", "THOROUGH_RETRIEVAL", "MULTI_STEP_ANSWER_RULES"):
        os.environ[name] = "true"
    os.environ["PIPESHUB_ENABLE_SKILLS"] = "false"
    from tests.unit.agents.agent_loop.prompt_cache.test_prefix_stability import _mode_request
    stable, _sections, tools = asyncio.run(_mode_request("internal_search", "Who owns HarborBank?"))
    print(hashlib.sha256((stable + repr(tools)).encode()).hexdigest())
''')
_HASH_SEEDS = (0, 7)
_PROCESS_TIMEOUT_S = 120


# Each seed cold-starts the agent stack in a fresh interpreter (langchain_core alone pulls in
# transformers), which outgrows pytest.ini's 30s for the whole test.
@pytest.mark.timeout(len(_HASH_SEEDS) * _PROCESS_TIMEOUT_S + 30)
def test_answer_rules_prefix_is_the_same_in_every_process() -> None:
    root = Path(__file__).resolve().parents[5]
    digests = set()
    for seed in _HASH_SEEDS:
        env = {k: v for k, v in os.environ.items() if k not in (*_ANSWER_RULE_SWITCHES, *_OTHER_PROMPT_SWITCHES)}
        result = subprocess.run(
            [sys.executable, "-c", _STABLE_BLOCK_SCRIPT], capture_output=True, text=True, check=False,
            env={**env, "PYTHONHASHSEED": str(seed), "PYTHONPATH": str(root)}, cwd=root, timeout=_PROCESS_TIMEOUT_S,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        digests.add(result.stdout.strip().splitlines()[-1])
    assert len(digests) == 1
