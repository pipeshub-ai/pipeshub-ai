"""AG1 ST: both graph providers store an engine agent's settings as text and read them back as JSON."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.utils import (
    decode_agent_engine_profile,
    encode_agent_engine_profile,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

AGENTS = CollectionNames.AGENT_INSTANCES.value
NARROWING = {"mode": "plan", "accountKinds": ["org_cloud"]}
STORED = '{"accountKinds":["org_cloud"],"mode":"plan"}'


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


class TestCodec:
    def test_an_object_is_stored_as_canonical_text_and_read_back(self) -> None:
        assert encode_agent_engine_profile(NARROWING) == STORED
        assert encode_agent_engine_profile(None) is None
        assert decode_agent_engine_profile({"engineProfile": STORED}) == {"engineProfile": NARROWING}
        assert decode_agent_engine_profile({"name": "x"}) == {"name": "x"}
        assert decode_agent_engine_profile(None) is None

    @pytest.mark.parametrize("text", ["{not json", '["org_cloud"]', '"plan"'])
    def test_st3_text_that_is_not_a_json_object_never_reads_as_an_object(self, text: str) -> None:
        value = decode_agent_engine_profile({"engineProfile": text})["engineProfile"]  # type: ignore[index]
        assert not isinstance(value, dict)

    def test_st6_the_schema_admits_text_or_null_and_refuses_an_object(self) -> None:
        validator = NodeSchemaValidator()
        validator.validate_node_update(AGENTS, {"engineProfile": STORED, "modelAccountId": "cloud/acc-1"})
        validator.validate_node_update(AGENTS, {"engineProfile": None, "modelAccountId": None})
        with pytest.raises(SchemaValidationError):
            validator.validate_node_update(AGENTS, {"engineProfile": NARROWING})
        validator.validate_node(
            AGENTS,
            {
                "id": "a1",
                "name": "A",
                "description": "d",
                "startMessage": "s",
                "systemPrompt": "p",
                "models": [],
                "createdBy": "u1",
                "createdAtTimestamp": 1,
                "engineProfile": STORED,
                "modelAccountId": "cloud/acc-1",
            },
        )


class TestUpdateAgent:
    @pytest.mark.asyncio
    async def test_st1_neo4j_writes_the_narrowing_as_text_through_its_validator(self, neo4j: Neo4jProvider) -> None:
        written = await _neo4j_written(neo4j, {"engineProfile": NARROWING, "modelAccountId": "cloud/acc-1"})
        assert (written["engineProfile"], written["modelAccountId"]) == (STORED, "cloud/acc-1")

    @pytest.mark.asyncio
    async def test_st1_arango_writes_the_narrowing_as_text(self, arango: ArangoHTTPProvider) -> None:
        written = await _arango_written(arango, {"engineProfile": NARROWING, "modelAccountId": "cloud/acc-1"})
        assert (written["engineProfile"], written["modelAccountId"]) == (STORED, "cloud/acc-1")

    @pytest.mark.asyncio
    async def test_st5_null_is_stored_and_absent_fields_are_untouched(
        self, neo4j: Neo4jProvider, arango: ArangoHTTPProvider
    ) -> None:
        for written in (
            await _neo4j_written(neo4j, {"engineProfile": None, "modelAccountId": None}),
            await _arango_written(arango, {"engineProfile": None, "modelAccountId": None}),
        ):
            assert (written["engineProfile"], written["modelAccountId"]) == (None, None)
        for written in (await _neo4j_written(neo4j, {"name": "B"}), await _arango_written(arango, {"name": "B"})):
            assert "engineProfile" not in written and "modelAccountId" not in written


class TestReadAgent:
    @pytest.mark.asyncio
    async def test_st2_neo4j_get_agent_decodes(self, neo4j: Neo4jProvider) -> None:
        neo4j.client.execute_query = AsyncMock(
            return_value=[{"agent": {"id": "a1", "name": "A", "engineProfile": STORED, "modelAccountId": "cloud/acc-1"}}]
        )
        neo4j._project_agents_toolsets_and_knowledge = AsyncMock(return_value={})  # type: ignore[method-assign]
        neo4j._project_agent_skills = AsyncMock(return_value=[])  # type: ignore[method-assign]
        neo4j._project_agents_mcp_servers = AsyncMock(return_value={})  # type: ignore[method-assign]
        agent = await neo4j.get_agent("a1")
        assert agent is not None
        assert (agent["engineProfile"], agent["modelAccountId"]) == (NARROWING, "cloud/acc-1")

    @pytest.mark.asyncio
    async def test_st2_arango_get_agent_decodes(self, arango: ArangoHTTPProvider) -> None:
        with patch.object(
            arango,
            "execute_query",
            new_callable=AsyncMock,
            return_value=[{"_key": "a1", "engineProfile": STORED, "modelAccountId": "cloud/acc-1"}],
        ):
            agent = await arango.get_agent("a1", "o1")
        assert agent is not None
        assert (agent["engineProfile"], agent["modelAccountId"]) == (NARROWING, "cloud/acc-1")

    @pytest.mark.asyncio
    async def test_st2_a_loop_agent_reads_without_the_fields(self, arango: ArangoHTTPProvider) -> None:
        with patch.object(arango, "execute_query", new_callable=AsyncMock, return_value=[{"_key": "a1"}]):
            agent = await arango.get_agent("a1", "o1")
        assert agent is not None and "engineProfile" not in agent

    @pytest.mark.asyncio
    async def test_st7_both_lists_decode(self, neo4j: Neo4jProvider, arango: ArangoHTTPProvider) -> None:
        neo4j.client.execute_query = AsyncMock(
            side_effect=[
                [
                    {
                        "agent": {"id": "a1", "name": "A", "updatedAtTimestamp": 1, "engineProfile": STORED},
                        "role": "OWNER",
                        "access_type": "INDIVIDUAL",
                        "priority": 1,
                    }
                ],
                [{"org_shared_ids": []}],
            ]
        )
        neo4j._project_agents_toolsets_and_knowledge = AsyncMock(return_value={})  # type: ignore[method-assign]
        neo4j._project_agents_mcp_servers = AsyncMock(return_value={})  # type: ignore[method-assign]
        listed = await neo4j.get_all_agents("u1", "o1")
        assert isinstance(listed, list) and listed[0]["engineProfile"] == NARROWING

        arango.execute_query = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"agents": [{"_key": "a1", "engineProfile": STORED}], "totalItems": 1}]
        )
        listed = await arango.get_all_agents("u1", "o1")
        assert isinstance(listed, list) and listed[0]["engineProfile"] == NARROWING
        assert json.loads(STORED) == NARROWING
