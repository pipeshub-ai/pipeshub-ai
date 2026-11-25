"""MA1 QA37-U10: the agents API stores a model per sub-agent role, each a configured LLM, and both graph
providers hold it; the run definition carries it to Node and is unchanged for an agent that sets none."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import jsonschema
import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (edition seams load before any router)
from app.agents.run_definition import build_run_definition
from app.config.constants.service import config_node_constants
from app.schema.arango.documents import agent_schema
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.support.bound_org import bind_test_org

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """``authMiddleware`` binds the caller's org; these tests call the route functions directly, as that caller."""
    yield from bind_test_org(ORG)

MODELS = [{"modelKey": "mk-a", "modelName": "gpt-5.4", "isReasoning": True}]
CONFIGURED = [
    {"modelKey": "mk-a", "provider": "openAI", "configuration": {"model": "gpt-5.4", "apiKey": "sk-a"}},
    {"modelKey": "mk-b", "provider": "anthropic", "configuration": {"model": "claude-haiku-4-5, claude-sonnet-5"}},
]
HAIKU = {"modelKey": "mk-b", "modelName": "claude-haiku-4-5", "provider": "anthropic"}
SONNET = {"modelKey": "mk-b", "modelName": "claude-sonnet-5", "provider": "anthropic"}
ROLES = {"researcher": HAIKU, "default": SONNET}
STORED = json.dumps(ROLES, sort_keys=True, separators=(",", ":"))


def _config(entries: list[dict[str, Any]] | None = None) -> AsyncMock:
    config = AsyncMock()
    llm = CONFIGURED if entries is None else entries

    async def get_config(key: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return {"llm": llm} if key == config_node_constants.AI_MODELS.value else None

    config.get_config = AsyncMock(side_effect=get_config)
    return config


def _graph(stored: dict[str, Any] | None = None) -> AsyncMock:
    graph = AsyncMock()
    graph.begin_transaction = AsyncMock(return_value="txn-1")
    graph.batch_upsert_nodes = AsyncMock(return_value=True)
    graph.batch_create_edges = AsyncMock(return_value=True)
    graph.commit_transaction = AsyncMock()
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": True, "can_share": True, "role": "OWNER"})
    graph.get_agent = AsyncMock(return_value={"_key": "agent-1", "name": "A", "shareWithOrg": False, **(stored or {})})
    graph.update_agent = AsyncMock(return_value=True)
    return graph


def _request(body: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    request.state.user = {"userId": "u1", "orgId": ORG, "role": "member"}
    return request


def _patches(graph: AsyncMock, config: AsyncMock):  # noqa: ANN202
    services = {"graph_provider": graph, "config_service": config, "logger": MagicMock()}
    return (
        patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services),
        patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": ORG}),
        patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "k1"}),
    )


async def _create(body: dict[str, Any], graph: AsyncMock, config: AsyncMock) -> dict[str, Any]:
    from app.api.routes.agent import create_agent

    services, context, user = _patches(graph, config)
    with services, context, user:
        response = await create_agent(_request({"name": "A", "models": MODELS, **body}))
    return json.loads(response.body)


async def _update(body: dict[str, Any], graph: AsyncMock, config: AsyncMock) -> dict[str, Any]:
    from app.api.routes.agent import update_agent

    services, context, user = _patches(graph, config)
    with services, context, user:
        await update_agent(_request(body), "agent-1")
    return graph.update_agent.await_args.args[1]


def _ai_model_reads(config: AsyncMock) -> int:
    return sum(1 for call in config.get_config.await_args_list if call.args[0] == config_node_constants.AI_MODELS.value)


BAD = [
    pytest.param({"Researcher": HAIKU}, "Researcher", "ROLE_KEY_INVALID", id="upper_case_key"),
    pytest.param({"-lead": HAIKU}, "-lead", "ROLE_KEY_INVALID", id="key_starting_with_a_dash"),
    pytest.param({"a b": HAIKU}, "a b", "ROLE_KEY_INVALID", id="key_with_a_space"),
    pytest.param({"a" * 65: HAIKU}, "a" * 65, "ROLE_KEY_INVALID", id="key_of_65_characters"),
    pytest.param({"": HAIKU}, "''", "ROLE_KEY_INVALID", id="empty_key"),
    pytest.param({f"r{i}": HAIKU for i in range(65)}, "65 roles", "ROLE_MODELS_TOO_MANY", id="65_roles"),
    pytest.param({"researcher": {"modelKey": "mk-gone", "modelName": "x"}}, "researcher", "ROLE_MODEL_NOT_CONFIGURED", id="unknown_model_key"),
    pytest.param({"researcher": {"modelKey": "mk-gone"}}, "researcher", "ROLE_MODEL_NOT_CONFIGURED", id="unknown_model_key_alone"),
    pytest.param({"researcher": {**HAIKU, "modelName": "claude-opus-4-8"}}, "researcher", "ROLE_MODEL_NOT_CONFIGURED", id="a_model_the_entry_does_not_serve"),
    pytest.param({"researcher": {**HAIKU, "modelName": 5}}, "researcher", "ROLE_MODEL_NOT_CONFIGURED", id="a_model_name_that_is_not_text"),
    pytest.param({"researcher": {**HAIKU, "provider": "openAI"}}, "researcher", "ROLE_MODEL_NOT_CONFIGURED", id="another_provider"),
    pytest.param({"default": SONNET, "researcher": "mk-b"}, "researcher", "ROLE_MODEL_INVALID", id="entry_not_an_object"),
    pytest.param({"researcher": {"modelName": "claude-haiku-4-5"}}, "researcher", "ROLE_MODEL_INVALID", id="no_model_key"),
    pytest.param(["researcher"], "roleModels", "ROLE_MODELS_INVALID", id="not_an_object"),
]


class TestSave:
    async def test_qa37_u10_create_stores_the_map_as_canonical_text_and_answers_it(self) -> None:
        graph = _graph()

        answer = await _create({"roleModels": ROLES}, graph, _config())

        document = graph.batch_upsert_nodes.await_args.args[0][0]
        assert document["roleModels"] == STORED
        jsonschema.validate({k: v for k, v in document.items() if k != "_key"}, agent_schema["rule"])
        assert answer["agent"]["roleModels"] == ROLES

    async def test_qa37_u10_the_stored_entry_is_the_configured_one_whatever_the_body_left_out(self) -> None:
        graph = _graph()

        _ = await _create({"roleModels": {"default": {"modelKey": "mk-b"}, "writer": {"modelKey": "mk-a", "provider": "openai"}}}, graph, _config())

        stored = json.loads(graph.batch_upsert_nodes.await_args.args[0][0]["roleModels"])
        assert stored == {
            "default": HAIKU,
            "writer": {"modelKey": "mk-a", "modelName": "gpt-5.4", "provider": "openAI"},
        }

    @pytest.mark.parametrize("body", [{}, {"roleModels": {}}, {"roleModels": None}], ids=["absent", "empty", "null"])
    async def test_qa37_u10_an_agent_without_role_models_is_stored_as_before_and_reads_no_configuration(
        self, body: dict[str, Any]
    ) -> None:
        plain, graph, config = _graph(), _graph(), _config()
        _ = await _create({}, plain, _config())

        answer = await _create(body, graph, config)

        def written(g: AsyncMock) -> dict[str, Any]:
            document = dict(g.batch_upsert_nodes.await_args.args[0][0])
            return {k: v for k, v in document.items() if k not in ("_key", "createdAtTimestamp", "updatedAtTimestamp")}

        assert written(graph) == written(plain)
        assert "roleModels" not in answer["agent"]
        assert _ai_model_reads(config) == 0

    @pytest.mark.parametrize(("role_models", "names", "code"), BAD)
    async def test_qa37_u10_a_refused_create_answers_400_naming_the_role_and_writes_nothing(
        self, role_models: object, names: str, code: str
    ) -> None:
        graph = _graph()

        with pytest.raises(HTTPException) as refused:
            _ = await _create({"roleModels": role_models}, graph, _config())

        assert refused.value.status_code == 400
        assert names in str(refused.value.detail) and f"[{code}]" in str(refused.value.detail)
        graph.begin_transaction.assert_not_awaited()
        graph.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.parametrize(("role_models", "names", "code"), BAD)
    async def test_qa37_u10_a_refused_update_answers_400_and_changes_nothing(
        self, role_models: object, names: str, code: str
    ) -> None:
        graph = _graph({"roleModels": ROLES})

        with pytest.raises(HTTPException) as refused:
            _ = await _update({"roleModels": role_models, "shareWithOrg": True}, graph, _config())

        assert refused.value.status_code == 400
        assert names in str(refused.value.detail) and f"[{code}]" in str(refused.value.detail)
        graph.update_agent.assert_not_awaited()
        graph.batch_create_edges.assert_not_awaited()

    async def test_qa37_u10_64_roles_and_a_64_character_key_are_allowed(self) -> None:
        roles = {**{f"r{i}": HAIKU for i in range(63)}, "a" * 64: SONNET}

        sent = await _update({"roleModels": roles}, _graph(), _config())

        assert sent["roleModels"] == roles

    async def test_qa37_u10_an_update_that_omits_the_field_keeps_the_stored_value(self) -> None:
        config = _config()

        sent = await _update({"name": "B"}, _graph({"roleModels": ROLES}), config)

        assert "roleModels" not in sent
        assert _ai_model_reads(config) == 0

    @pytest.mark.parametrize("cleared", [{}, None], ids=["empty", "null"])
    async def test_qa37_u10_an_empty_map_clears_the_stored_value(self, cleared: object) -> None:
        sent = await _update({"roleModels": cleared}, _graph({"roleModels": ROLES}), _config())

        assert sent["roleModels"] is None

    async def test_qa37_u10_a_model_removed_from_the_configuration_is_refused_on_the_next_save(self) -> None:
        with pytest.raises(HTTPException) as refused:
            _ = await _update({"roleModels": ROLES}, _graph({"roleModels": ROLES}), _config([CONFIGURED[0]]))

        assert "[ROLE_MODEL_NOT_CONFIGURED]" in str(refused.value.detail)


@pytest.fixture
def neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.check_agent_permission = AsyncMock(return_value={"can_edit": True})  # type: ignore[method-assign]
    return provider


@pytest.fixture
def arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.http_client = AsyncMock()
    provider.check_agent_permission = AsyncMock(return_value={"can_edit": True})  # type: ignore[method-assign]
    return provider


async def _neo4j_written(provider: Neo4jProvider, updates: dict[str, Any]) -> dict[str, Any]:
    provider.client.execute_query = AsyncMock(return_value=[{"n": {"id": "a1"}}])
    assert await provider.update_agent("a1", updates, "u1", "o1") is True
    return provider.client.execute_query.await_args.kwargs["parameters"]["updates"]


async def _arango_written(provider: ArangoHTTPProvider, updates: dict[str, Any]) -> dict[str, Any]:
    provider.http_client.update_document = AsyncMock(return_value={"_key": "a1"})
    assert await provider.update_agent("a1", updates, "u1", "o1") is True
    return provider.http_client.update_document.await_args.args[2]


class TestBothGraphBackends:
    async def test_qa37_u10_both_providers_write_the_map_as_text_and_null_to_clear(
        self, neo4j: Neo4jProvider, arango: ArangoHTTPProvider
    ) -> None:
        for written in (await _neo4j_written(neo4j, {"roleModels": ROLES}), await _arango_written(arango, {"roleModels": ROLES})):
            assert written["roleModels"] == STORED
        for written in (await _neo4j_written(neo4j, {"roleModels": None}), await _arango_written(arango, {"roleModels": None})):
            assert written["roleModels"] is None
        for written in (await _neo4j_written(neo4j, {"name": "B"}), await _arango_written(arango, {"name": "B"})):
            assert "roleModels" not in written

    async def test_qa37_u10_neo4j_get_agent_reads_the_map_back(self, neo4j: Neo4jProvider) -> None:
        neo4j.client.execute_query = AsyncMock(return_value=[{"agent": {"id": "a1", "name": "A", "roleModels": STORED}}])
        neo4j._project_agents_toolsets_and_knowledge = AsyncMock(return_value={})  # type: ignore[method-assign]
        neo4j._project_agent_skills = AsyncMock(return_value=[])  # type: ignore[method-assign]

        agent = await neo4j.get_agent("a1", "o1")

        assert agent is not None and agent["roleModels"] == ROLES

    async def test_qa37_u10_arango_get_agent_reads_the_map_back(self, arango: ArangoHTTPProvider) -> None:
        arango.http_client.execute_aql = AsyncMock(return_value=[{"_key": "a1", "name": "A", "roleModels": STORED}])

        agent = await arango.get_agent("a1", "o1")

        assert agent is not None and agent["roleModels"] == ROLES

    def test_qa37_u10_the_agent_document_holds_the_text_or_null_never_an_object(self) -> None:
        base = {"name": "a", "description": "d", "startMessage": "s", "systemPrompt": "p", "models": [], "createdBy": "u", "createdAtTimestamp": 1}
        jsonschema.validate({**base, "roleModels": STORED}, agent_schema["rule"])
        jsonschema.validate({**base, "roleModels": None}, agent_schema["rule"])
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**base, "roleModels": ROLES}, agent_schema["rule"])


def _definition(agent: dict[str, Any]) -> dict[str, Any]:
    definition = build_run_definition(
        {"updatedAtTimestamp": 7, **agent}, agent_key="agent-1", actions_enabled=True, mcp_enabled=True, web_search_provider=None
    )
    return definition.model_dump(by_alias=True, mode="json")


class TestRunDefinition:
    def test_qa37_u10_the_run_definition_carries_the_role_models(self) -> None:
        assert _definition({"roleModels": ROLES})["roleModels"] == ROLES

    @pytest.mark.parametrize("stored", [None, {}, "{not json", ["researcher"]], ids=["null", "empty", "unparsed", "list"])
    def test_qa37_u10_an_agent_without_role_models_has_the_definition_it_had(self, stored: object) -> None:
        assert _definition({"roleModels": stored}) == _definition({})
        assert "roleModels" not in _definition({})

    def test_qa37_u10_a_stored_entry_that_names_no_model_is_not_handed_to_node(self) -> None:
        stored = {"researcher": HAIKU, "writer": {"modelKey": "mk-a"}, "Bad Key": SONNET}
        assert _definition({"roleModels": stored})["roleModels"] == {"researcher": HAIKU}
