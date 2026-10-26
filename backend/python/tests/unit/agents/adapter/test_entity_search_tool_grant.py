"""``knowledgegraph__search_by_entities`` through `PipesHubAgentFactory.create()`
with the real `KnowledgeGraph` toolset, in every mode.

The deployment switch ``ENTITY_SEARCH_TOOL`` (off by default, and off under
``HIGH_ACCURACY_MODE`` unless set) decides whether the tool is offered, and
only where named-entity extraction is on and the entity store is wired. With
it off, the tools and the system prompt are what they were before the tool
existed. Either way they do not depend on the question, so the provider's
prompt cache keeps hitting across questions.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.agent_loop.entity_search_tool import (
    ENTITY_SEARCH_OFFERED_STATE_KEY,
    ENTITY_SEARCH_TOOL_ENV_VAR,
    NAMED_ENTITIES_ENABLED_STATE_KEY,
    SEARCH_BY_ENTITIES_TOOL_NAME,
    entity_search_tool_offered,
)
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks.progressive_tools import (
    PROGRESSIVE_FIND_RECORDS_TOOL_NAME,
)
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

_NER_FLAG = "app.agents.agent_loop.factory.is_named_entity_extraction_enabled"
_JIRA = {"displayName": "Engineering Jira", "type": "JIRA", "connectorId": "jira-1"}
_MODES = ("quick", "react", "planExecute", "deep")
_QUESTIONS = ("Who owns the Contoso renewal?", "Summarize the Q3 incident reviews for Northwind")


class _CrmToolset:
    """A connector toolset: not essential, so lazy disclosure would hide it."""

    def __init__(self, state: dict | None = None) -> None:
        self.state = state

    @tool(path="/tools/acmecrm/get_account", short_description="Get an account", description="Read one CRM account.")
    async def get_account(self, account_id: str) -> str:
        return "ok"

    @tool(path="/tools/acmecrm/list_deals", short_description="List deals", description="List CRM deals.")
    async def list_deals(self, account_id: str) -> str:
        return "ok"


def _registry() -> MagicMock:
    registry = MagicMock()
    registry.get_all_toolsets.return_value = {
        "knowledgegraph": {
            "class": KnowledgeGraph, "isInternal": True, "description": "Knowledge graph", "essential": True,
        },
        "acmecrm": {"class": _CrmToolset, "isInternal": True, "description": "CRM", "essential": False},
    }
    return registry


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.delenv(ENTITY_SEARCH_TOOL_ENV_VAR, raising=False)
    monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)


async def _create(
    mode: str = "quick", *, query: str = _QUESTIONS[0], ner: bool = True, store: bool = True,
    flag: AsyncMock | None = None,
) -> tuple[Any, Any, Any, Any]:
    context = make_context(
        llm=FakeChatModel(), has_knowledge=True, agent_knowledge=[_JIRA], query=query,
    )
    context.tool_state["web_search_config"] = {"provider": "duckduckgo"}
    if store:
        context.tool_state["entity_vector_store"] = MagicMock()
    flag = flag or AsyncMock(return_value=ner)
    with patch("app.agents.registry.toolset_registry.get_toolset_registry", return_value=_registry()), \
            patch("app.agents.agent_loop.tool_loader.ClientFactoryRegistry.get_factory", return_value=None), \
            patch(_NER_FLAG, flag):
        agent, runtime, goal, _clarifying = await PipesHubAgentFactory().create(
            context, context.llm, mode, query=query,
        )
    return context, agent, runtime, goal


def _granted(mode: str, agent: Any, runtime: Any) -> list[str]:  # noqa: ANN401
    """What the model can call: the agent's grant, or deep mode's spawn pool."""
    if mode == "deep":
        return list(runtime.spec_factory("worker").tool_names)
    return list(agent.spec.tool_names)


def _surface(mode: str, agent: Any, runtime: Any, goal: Any) -> tuple[str, str]:  # noqa: ANN401
    """The cached prefix: the stable system-prompt block and the tool schemas."""
    if mode == "deep":
        spec = runtime.spec_factory("worker")
        system = spec.system_prompt
    else:
        spec = agent.spec
        system, _volatile = spec.system_prompt.build_blocks(spec, runtime, goal, [], {})
    schemas = tool_schemas_for_turn(type("A", (), {"visible_tools": None})(), spec, runtime)
    return system, json.dumps([_schema(s) for s in schemas], sort_keys=True, default=str)


def _schema(schema: Any) -> Any:  # noqa: ANN401
    return dataclasses.asdict(schema) if dataclasses.is_dataclass(schema) else repr(schema)


class TestTheSwitch:
    @pytest.mark.parametrize(("env", "expected"), [
        ({}, False),
        ({ENTITY_SEARCH_TOOL_ENV_VAR: "true"}, True),
        ({ENTITY_SEARCH_TOOL_ENV_VAR: "TRUE "}, True),
        ({ENTITY_SEARCH_TOOL_ENV_VAR: "false"}, False),
        ({ENTITY_SEARCH_TOOL_ENV_VAR: ""}, False),
        ({"HIGH_ACCURACY_MODE": "true"}, False),
        ({"HIGH_ACCURACY_MODE": "true", ENTITY_SEARCH_TOOL_ENV_VAR: ""}, False),
        ({"HIGH_ACCURACY_MODE": "true", ENTITY_SEARCH_TOOL_ENV_VAR: "false"}, False),
        ({"HIGH_ACCURACY_MODE": "true", ENTITY_SEARCH_TOOL_ENV_VAR: "true"}, True),
    ])
    def test_only_the_switch_turns_it_on(
        self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: bool,
    ) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        state = {"entity_vector_store": object(), NAMED_ENTITIES_ENABLED_STATE_KEY: True}
        assert entity_search_tool_offered(state) is expected

    @pytest.mark.parametrize(("store", "ner"), [(None, True), (object(), False), (object(), None)])
    def test_it_needs_extraction_and_the_entity_store(
        self, monkeypatch: pytest.MonkeyPatch, store: object, ner: bool | None,
    ) -> None:
        monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        state: dict[str, Any] = {"entity_vector_store": store}
        if ner is not None:
            state[NAMED_ENTITIES_ENABLED_STATE_KEY] = ner
        assert entity_search_tool_offered(state) is False


class TestTheNamedEntityFlag:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("switch", ("off", "on"))
    @pytest.mark.parametrize("ner", (True, False))
    async def test_one_read_decides_both(self, monkeypatch: pytest.MonkeyPatch, switch: str, ner: bool) -> None:
        if switch == "on":
            monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        flag = AsyncMock(return_value=ner)
        context, _agent, _runtime, _ = await _create(flag=flag)
        flag.assert_awaited_once()
        assert context.tool_state[NAMED_ENTITIES_ENABLED_STATE_KEY] is ner
        assert context.tool_state[ENTITY_SEARCH_OFFERED_STATE_KEY] is (ner and switch == "on")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", _MODES)
    @pytest.mark.parametrize("ner", (True, False))
    async def test_off_leaves_out_what_reads_named_entities(self, mode: str, ner: bool) -> None:
        """Shown with extraction off, they made the model call tools that could only fail."""
        _context, agent, runtime, goal = await _create(mode, ner=ner)
        assert runtime.tool_registry.has("knowledgegraph__find_records_by_value") is ner
        assert runtime.tool_registry.has("knowledgegraph__search_entities")
        _system, schemas = _surface(mode, agent, runtime, goal)
        assert ("named_entity_kinds" in schemas) is ner


class TestGrantPerMode:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("env", [
        {},
        {"HIGH_ACCURACY_MODE": "true"},
        {"HIGH_ACCURACY_MODE": "true", ENTITY_SEARCH_TOOL_ENV_VAR: ""},
        {"HIGH_ACCURACY_MODE": "true", ENTITY_SEARCH_TOOL_ENV_VAR: "false"},
    ], ids=["unset", "high-accuracy", "high-accuracy-blank", "high-accuracy-false"])
    @pytest.mark.parametrize("mode", _MODES)
    async def test_off_offers_nothing_new(
        self, monkeypatch: pytest.MonkeyPatch, mode: str, env: dict[str, str],
    ) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        context, agent, runtime, goal = await _create(mode)
        assert context.tool_state[ENTITY_SEARCH_OFFERED_STATE_KEY] is False
        assert not runtime.tool_registry.has(SEARCH_BY_ENTITIES_TOOL_NAME)
        system, schemas = _surface(mode, agent, runtime, goal)
        assert "search_by_entities" not in system and "search_by_entities" not in schemas
        assert "named_entity'" not in schemas, "the named-entity descriptions come only with the tool"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", _MODES)
    async def test_on_adds_exactly_the_one_tool(self, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
        _context, off_agent, off_runtime, _ = await _create(mode)
        monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        context, agent, runtime, _ = await _create(mode)
        assert context.tool_state[ENTITY_SEARCH_OFFERED_STATE_KEY] is True
        off, on = _granted(mode, off_agent, off_runtime), _granted(mode, agent, runtime)
        assert [name for name in on if name != SEARCH_BY_ENTITIES_TOOL_NAME] == off
        assert SEARCH_BY_ENTITIES_TOOL_NAME in on

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("ner", "store"), [(False, True), (True, False)])
    async def test_on_without_extraction_or_store_offers_nothing(
        self, monkeypatch: pytest.MonkeyPatch, ner: bool, store: bool,
    ) -> None:
        monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        _context, agent, runtime, _ = await _create(ner=ner, store=store)
        assert SEARCH_BY_ENTITIES_TOOL_NAME not in agent.spec.tool_names
        assert not runtime.tool_registry.has(SEARCH_BY_ENTITIES_TOOL_NAME)

    @pytest.mark.asyncio
    async def test_it_unlocks_nothing_mid_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Its result shows no entity ids, so it does not grant
        find_records_by_entity, and the tool list stays what the run began with."""
        monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        _context, agent, _runtime, _ = await _create()
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME not in agent.spec.tool_names
        from app.agents.agent_loop.hooks.progressive_tools import (
            _ENTITY_SEARCH_TRIGGER_NAMES,
        )

        assert SEARCH_BY_ENTITIES_TOOL_NAME not in _ENTITY_SEARCH_TRIGGER_NAMES


class TestLazyDisclosure:
    @staticmethod
    async def _at_threshold(monkeypatch: pytest.MonkeyPatch, mode: str) -> int:
        """Pin the lazy threshold to exactly the number of tools granted with the
        switch off, the edge where one more counted tool flips disclosure."""
        from app.agents.agent_loop import lazy_tools_wiring

        counted: list[int] = []
        real = lazy_tools_wiring.should_apply_lazy_tools

        def spy(count: int) -> bool:
            counted.append(count)
            return real(count)

        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "1000")
        with patch.object(lazy_tools_wiring, "should_apply_lazy_tools", spy):
            await _create(mode)
        (count,) = counted
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", str(count))
        return count

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ("quick", "react", "planExecute"))
    async def test_off_is_not_newly_lazy(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        await self._at_threshold(monkeypatch, mode)
        _context, agent, _runtime, _ = await _create(mode)
        assert agent.spec.tool_disclosure != "lazy"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ("quick", "react", "planExecute"))
    async def test_on_keeps_connector_and_web_tools_bound(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        await self._at_threshold(monkeypatch, mode)
        monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        _context, agent, runtime, goal = await _create(mode)
        assert agent.spec.tool_disclosure != "lazy"
        assert "fetch_tools" not in agent.spec.tool_names
        _system, schemas = _surface(mode, agent, runtime, goal)
        assert SEARCH_BY_ENTITIES_TOOL_NAME in schemas
        names = set(agent.spec.tool_names)
        assert any("web_search" in name for name in names) or any("web_agent" in name for name in names)
        assert any(name.startswith("acmecrm__") or "agent" in name for name in names - {SEARCH_BY_ENTITIES_TOOL_NAME})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ("quick", "react", "planExecute"))
    async def test_the_timeline_switch_does_not_make_a_request_lazy(
        self, monkeypatch: pytest.MonkeyPatch, mode: str,
    ) -> None:
        from app.agents.actions.knowledge_graph.ops.timeline import (
            TIMELINE_SUMMARIES_ENV_VAR,
            TIMELINE_TOOL_NAME,
        )

        # The threshold is counted with the timeline off ("false": it is on
        # when unset), then the switch adds the tool.
        monkeypatch.setenv(TIMELINE_SUMMARIES_ENV_VAR, "false")
        await self._at_threshold(monkeypatch, mode)
        monkeypatch.setenv(TIMELINE_SUMMARIES_ENV_VAR, "true")
        _context, agent, runtime, _ = await _create(mode)
        assert agent.spec.tool_disclosure != "lazy"
        assert "fetch_tools" not in agent.spec.tool_names
        assert runtime.tool_registry.has(TIMELINE_TOOL_NAME)

    @pytest.mark.asyncio
    async def test_a_request_already_over_the_threshold_is_lazy_either_way(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        count = await self._at_threshold(monkeypatch, "quick")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", str(count - 1))
        _context, off_agent, _runtime, _ = await _create("quick")
        monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        _context, on_agent, _runtime, _ = await _create("quick")
        assert off_agent.spec.tool_disclosure == on_agent.spec.tool_disclosure == "lazy"
        assert SEARCH_BY_ENTITIES_TOOL_NAME in on_agent.spec.tool_names


class TestPromptCacheStability:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("switch", ("off", "on"))
    @pytest.mark.parametrize("mode", _MODES)
    async def test_prompt_and_tools_do_not_depend_on_the_question(
        self, monkeypatch: pytest.MonkeyPatch, mode: str, switch: str,
    ) -> None:
        if switch == "on":
            monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        surfaces = []
        for question in _QUESTIONS:
            _context, agent, runtime, goal = await _create(mode, query=question)
            surfaces.append(_surface(mode, agent, runtime, goal))
        first, second = surfaces
        assert first[0] == second[0], "the stable system-prompt block changed with the question"
        assert first[1] == second[1], "the tool schemas changed with the question"
        assert ("search_by_entities" in first[1]) is (switch == "on")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ("quick", "react", "planExecute"))
    async def test_on_names_the_tool_in_the_entities_paragraph(
        self, monkeypatch: pytest.MonkeyPatch, mode: str,
    ) -> None:
        monkeypatch.setenv(ENTITY_SEARCH_TOOL_ENV_VAR, "true")
        _context, agent, runtime, goal = await _create(mode)
        system, _ = _surface(mode, agent, runtime, goal)
        assert f"`{SEARCH_BY_ENTITIES_TOOL_NAME}(mentions=[...])`" in system
