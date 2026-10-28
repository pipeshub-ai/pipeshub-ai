"""The three knowledge-graph arms through `PipesHubAgentFactory.create()` with
the real `KnowledgeGraph` toolset: what the model is offered (tools, their
schemas, the Entities paragraph of the system prompt), what the entity tools
then search, and that the arm cannot change once the request has started."""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops import entity_discovery, entity_filters
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    execute_search_entities,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    execute_find_records_by_entity,
)
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.knowledge_graph_policy import (
    ENTITY_SEARCH_TOOL_ENV_VAR,
    KNOWLEDGE_GRAPH_POLICY_STATE_KEY,
    KnowledgeGraphMode,
)
from app.agents.agent_loop.protocol.run_usage import RunUsagePayload, emit_run_usage
from app.modules.retrieval.entity_permissions import EntityAccessContext
from app.services.featureflag.config.config import CONFIG
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context
from tests.unit.agents.adapter.test_entity_search_tool_grant import (
    _MODES,
    _registry,
    _surface,
)

_JIRA = {"displayName": "Engineering Jira", "type": "JIRA", "connectorId": "jira-1"}
_ENTITY_TOOLS = {
    "knowledgegraph__search_entities",
    "knowledgegraph__find_records_by_entity",
    "knowledgegraph__find_records_by_value",
    "knowledgegraph__search_by_entities",
}
_ARMS = {
    KnowledgeGraphMode.OFF: {CONFIG.KNOWLEDGE_GRAPH_TOOLS: False},
    KnowledgeGraphMode.TAXONOMY: {},
    KnowledgeGraphMode.FULL: {CONFIG.KNOWLEDGE_GRAPH_PEOPLE_AND_COMPANIES: True},
}
_PEOPLE_WORDS = re.compile(r"person|people|organi[sz]ation|colleague|compan", re.IGNORECASE)


class _Settings:
    """The Labs settings blob; `flags` may be changed while a request runs."""

    def __init__(self, flags: dict[str, bool]) -> None:
        self.flags = dict(flags)

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, **_: Any) -> Any:  # noqa: ANN401
        return {"featureFlags": dict(self.flags)}


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.delenv(ENTITY_SEARCH_TOOL_ENV_VAR, raising=False)
    monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)


async def _create(arm: KnowledgeGraphMode, mode: str = "quick", *, ner: bool = False) -> tuple[Any, Any, Any, Any, _Settings]:
    flags = {**_ARMS[arm], CONFIG.ENABLE_NAMED_ENTITY_EXTRACTION: ner}
    settings = _Settings(flags)
    context = make_context(
        llm=FakeChatModel(), has_knowledge=True, agent_knowledge=[_JIRA], config_service=settings,
        query="Who owns the Contoso renewal?",
    )
    context.tool_state["config_service"] = settings
    context.tool_state["web_search_config"] = {"provider": "duckduckgo"}
    context.tool_state["entity_vector_store"] = MagicMock()
    with patch("app.agents.registry.toolset_registry.get_toolset_registry", return_value=_registry()), \
            patch("app.agents.agent_loop.tool_loader.ClientFactoryRegistry.get_factory", return_value=None):
        agent, runtime, goal, _clarifying = await PipesHubAgentFactory().create(
            context, context.llm, mode, query="Who owns the Contoso renewal?",
        )
    return context, agent, runtime, goal, settings


def _kg_schemas(runtime: Any) -> dict[str, str]:  # noqa: ANN401
    registry = runtime.tool_registry
    tools = [registry.resolve_by_name(name) for name in registry.names() if name.startswith("knowledgegraph__")]
    return {
        tool.name: json.dumps(
            [tool.short_description, tool.description, [(p.name, p.description) for p in tool.parameters]],
        )
        for tool in tools
    }


def _entities_paragraph(system: str) -> str:
    found = re.search(r"\*\*Entities\.\*\*[^\n]*", system)
    return found.group(0) if found else ""


class TestToolsPerArm:
    @pytest.mark.parametrize("mode", _MODES)
    async def test_off_withdraws_the_entity_tools_and_their_hint_together(self, mode: str) -> None:
        context, agent, runtime, goal, _ = await _create(KnowledgeGraphMode.OFF, mode, ner=True)
        assert context.tool_state[KNOWLEDGE_GRAPH_POLICY_STATE_KEY].mode is KnowledgeGraphMode.OFF
        assert not any(runtime.tool_registry.has(name) for name in _ENTITY_TOOLS)
        system, schemas = _surface(mode, agent, runtime, goal)
        assert "**Entities.**" not in system
        assert "search_entities" not in system and "search_entities" not in schemas
        assert "entity_ids" not in schemas, "search(entity_ids=...) takes only search_entities' ids"
        assert runtime.tool_registry.has("knowledgegraph__search")

    @pytest.mark.parametrize("mode", _MODES)
    async def test_taxonomy_keeps_the_entity_tools_without_people_or_companies(self, mode: str) -> None:
        _context, agent, runtime, goal, _ = await _create(KnowledgeGraphMode.TAXONOMY, mode, ner=True)
        assert runtime.tool_registry.has("knowledgegraph__search_entities")
        assert not runtime.tool_registry.has("knowledgegraph__search_by_entities")
        assert not runtime.tool_registry.has("knowledgegraph__find_records_by_value")
        for name, schema in _kg_schemas(runtime).items():
            assert not _PEOPLE_WORDS.search(schema), f"{name} still offers people or companies"
        system, _schemas = _surface(mode, agent, runtime, goal)
        if mode != "deep":
            paragraph = _entities_paragraph(system)
            assert paragraph, "the taxonomy arm keeps the Entities paragraph"
            assert not _PEOPLE_WORDS.search(paragraph)

    @pytest.mark.parametrize("mode", ("quick", "react", "planExecute"))
    async def test_full_is_todays_surface(self, mode: str) -> None:
        _context, agent, runtime, goal, _ = await _create(KnowledgeGraphMode.FULL, mode, ner=True)
        schemas = _kg_schemas(runtime)
        assert "'person', 'organization'" in schemas["knowledgegraph__search_entities"]
        assert "colleagues, companies" in _entities_paragraph(_surface(mode, agent, runtime, goal)[0])
        assert runtime.tool_registry.has("knowledgegraph__find_records_by_value")


class TestEntityTypesPerArm:
    @pytest.fixture
    def search(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        context = EntityAccessContext(
            org_id="org-1", user_key="u", app_level_app_ids=frozenset({"kb-1"}),
            record_level_app_ids=frozenset(), record_group_ids=frozenset(), app_names={},
        )
        monkeypatch.setattr(entity_filters, "get_entity_access_context", AsyncMock(return_value=context))
        found = AsyncMock(return_value=[])
        monkeypatch.setattr(entity_discovery, "search_entities_for_user", found)
        return found

    @pytest.mark.parametrize(("arm", "people"), [(KnowledgeGraphMode.TAXONOMY, False), (KnowledgeGraphMode.FULL, True)])
    async def test_search_entities_searches_the_arms_types(
        self, search: AsyncMock, arm: KnowledgeGraphMode, people: bool,
    ) -> None:
        context, *_ = await _create(arm)
        ok, _ = await execute_search_entities(context.tool_state, "ann")
        assert ok
        allowed = search.call_args.kwargs["allowed_types"]
        assert {"topic", "record", "record_group"} <= allowed
        assert ("person" in allowed) is people and ("organization" in allowed) is people

    async def test_find_records_by_entity_refuses_a_person_in_the_taxonomy_arm(self) -> None:
        context, *_ = await _create(KnowledgeGraphMode.TAXONOMY)
        ok, text = await execute_find_records_by_entity(context.tool_state, "u-ann", entity_type="person")
        assert ok is False and "Unsupported entity_type 'person'" in text
        assert "person" not in text.split("pass one of:")[1]


class TestOneDecisionPerRequest:
    async def test_a_flag_flipped_mid_request_changes_neither_tools_nor_behaviour(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        context, _agent, runtime, _goal, settings = await _create(KnowledgeGraphMode.TAXONOMY)
        tools_before = sorted(runtime.tool_registry.names())
        settings.flags = {CONFIG.KNOWLEDGE_GRAPH_PEOPLE_AND_COMPANIES: True, CONFIG.ENABLE_NAMED_ENTITY_EXTRACTION: True}
        reads = AsyncMock(side_effect=AssertionError("a tool read a flag mid-request"))
        monkeypatch.setattr(settings, "get_config", reads)

        ok, text = await execute_find_records_by_entity(context.tool_state, "u-ann", entity_type="person")
        assert ok is False and "Unsupported entity_type" in text
        ok, text = await execute_search_entities(context.tool_state, "acme", entity_types=["named_entity"])
        assert ok is False and "not enabled" in text
        assert sorted(runtime.tool_registry.names()) == tools_before
        assert context.tool_state[KNOWLEDGE_GRAPH_POLICY_STATE_KEY].mode is KnowledgeGraphMode.TAXONOMY
        reads.assert_not_called()


class TestTheArmIsReported:
    @pytest.mark.parametrize("arm", list(KnowledgeGraphMode))
    async def test_the_run_usage_frame_names_the_arm(self, arm: KnowledgeGraphMode) -> None:
        context, agent, *_ = await _create(arm)
        context.include_retrieval_context = True
        context.event_sink = MagicMock(write=AsyncMock())
        await emit_run_usage(context, agent)
        (frame,), _ = context.event_sink.write.call_args
        assert json.dumps(frame).count(f'"knowledgeGraphArm": "{arm.value}"') == 1

    def test_a_request_that_never_decided_reports_no_arm(self) -> None:
        assert "knowledgeGraphArm" not in RunUsagePayload().to_wire_dict()
