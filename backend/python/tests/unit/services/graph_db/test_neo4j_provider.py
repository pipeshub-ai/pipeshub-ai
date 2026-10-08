from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.exceptions.graph_db_exceptions import GraphQueryError
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_GRACE_MS,
    MoveDestinationMissing,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.fixture
def neo4j_provider() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.get_authenticated_as = AsyncMock(return_value=[])
    provider._resolve_acting_user_key_for_node = AsyncMock(side_effect=lambda user_key, *_: user_key)
    provider._authenticated_as_apps = AsyncMock(return_value=[])
    return provider


class TestConnectionManagement:
    @pytest.mark.asyncio
    async def test_connect_success_uses_env_values(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict(
            "os.environ",
            {
                "NEO4J_URI": "bolt://neo4j.test:7687",
                "NEO4J_USERNAME": "neo-user",
                "NEO4J_PASSWORD": "secret",
                "NEO4J_DATABASE": "graphdb",
            },
            clear=False,
        ), patch("app.services.graph_db.neo4j.neo4j_provider.Neo4jClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()

        assert result is True
        mock_client_cls.assert_called_once()
        _, kwargs = mock_client_cls.call_args
        assert kwargs["uri"] == "bolt://neo4j.test:7687"
        assert kwargs["username"] == "neo-user"
        assert kwargs["password"] == "secret"
        assert kwargs["database"] == "graphdb"
        assert provider.client is mock_client

    @pytest.mark.asyncio
    async def test_connect_uses_defaults_for_optional_env_vars(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict("os.environ", {"NEO4J_PASSWORD": "secret"}, clear=True), patch(
            "app.services.graph_db.neo4j.neo4j_provider.Neo4jClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()

        assert result is True
        _, kwargs = mock_client_cls.call_args
        assert kwargs["uri"] == "bolt://localhost:7687"
        assert kwargs["username"] == "neo4j"
        assert kwargs["database"] == "neo4j"

    @pytest.mark.asyncio
    async def test_connect_returns_false_when_password_missing(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict("os.environ", {}, clear=True):
            result = await provider.connect()

        assert result is False
        assert provider.client is None

    @pytest.mark.asyncio
    async def test_connect_returns_false_when_client_connect_returns_false(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict("os.environ", {"NEO4J_PASSWORD": "secret"}, clear=True), patch(
            "app.services.graph_db.neo4j.neo4j_provider.Neo4jClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()

        assert result is False
        assert provider.client is None

    @pytest.mark.asyncio
    async def test_connect_returns_false_when_client_ctor_raises(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

        with patch.dict("os.environ", {"NEO4J_PASSWORD": "secret"}, clear=True), patch(
            "app.services.graph_db.neo4j.neo4j_provider.Neo4jClient",
            side_effect=RuntimeError("bad config"),
        ):
            result = await provider.connect()

        assert result is False
        assert provider.client is None

    @pytest.mark.asyncio
    async def test_disconnect_calls_client_and_returns_true(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock(return_value=True)
        provider.client = mock_client

        result = await provider.disconnect()

        assert result is True
        mock_client.disconnect.assert_awaited_once()
        assert provider.client is None

    @pytest.mark.asyncio
    async def test_disconnect_without_client_returns_true(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        provider.client = None

        result = await provider.disconnect()

        assert result is True
        assert provider.client is None

    @pytest.mark.asyncio
    async def test_disconnect_returns_false_when_client_disconnect_raises(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))
        provider.client = mock_client

        result = await provider.disconnect()

        assert result is False
        assert provider.client is mock_client


class TestPrivateHelperDelegation:
    def test_get_label_delegates_to_collection_to_label(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.collection_to_label",
            return_value="AgentInstances",
        ) as mock_mapper:
            result = provider._get_label("agentInstances")
        assert result == "AgentInstances"
        mock_mapper.assert_called_once_with("agentInstances")

    def test_get_relationship_type_delegates_to_edge_mapper(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.edge_collection_to_relationship",
            return_value="AGENT_HAS_TOOLSET",
        ) as mock_mapper:
            result = provider._get_relationship_type("agentHasToolset")
        assert result == "AGENT_HAS_TOOLSET"
        mock_mapper.assert_called_once_with("agentHasToolset")

    @pytest.mark.asyncio
    async def test_initialize_schema_delegates_to_ensure_schema(self):
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        provider.ensure_schema = AsyncMock(return_value=None)  # type: ignore[method-assign]

        await provider._initialize_schema()

        provider.ensure_schema.assert_awaited_once()


class TestCheckToolsetInstanceInUse:
    @pytest.mark.asyncio
    async def test_returns_empty_when_query_returns_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=None)

        result = await neo4j_provider.check_toolset_instance_in_use("inst-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_dedupes_by_id_not_name(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"agentId": "neo4j-id-1", "agentName": "kb-agent"},
                {"agentId": "neo4j-id-2", "agentName": "kb-agent"},
                {"agentId": "neo4j-id-1", "agentName": "kb-agent"},
            ]
        )

        result = await neo4j_provider.check_toolset_instance_in_use("inst-1")

        assert result == ["kb-agent", "kb-agent"]
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_passes_parameters_and_txn_id(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.check_toolset_instance_in_use("inst-123", transaction="txn-1")

        neo4j_provider.client.execute_query.assert_awaited_once()
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"instance_id": "inst-123"}
        assert kwargs["txn_id"] == "txn-1"

    @pytest.mark.asyncio
    async def test_raises_on_query_error(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("neo4j down"))

        with pytest.raises(RuntimeError, match="neo4j down"):
            await neo4j_provider.check_toolset_instance_in_use("inst-1")


class TestCheckConnectorInUse:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_agents(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.check_connector_in_use("conn-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_dedupes_by_id_not_name(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"agentId": "neo4j-id-1", "agentName": "kb-agent"},
                {"agentId": "neo4j-id-2", "agentName": "kb-agent"},
                {"agentId": "neo4j-id-1", "agentName": "kb-agent"},
            ]
        )

        result = await neo4j_provider.check_connector_in_use("conn-1")

        assert result == ["kb-agent", "kb-agent"]
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_handles_none_result_as_empty_list(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=None)

        result = await neo4j_provider.check_connector_in_use("conn-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_passes_parameters_and_txn_id(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.check_connector_in_use("conn-123", transaction="txn-2")

        neo4j_provider.client.execute_query.assert_awaited_once()
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"connector_id": "conn-123"}
        assert kwargs["txn_id"] == "txn-2"

    @pytest.mark.asyncio
    async def test_raises_on_query_error(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("neo4j down"))

        with pytest.raises(RuntimeError, match="neo4j down"):
            await neo4j_provider.check_connector_in_use("conn-1")


class TestGetAgentsByModelKey:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_agents_by_model_key("org-1", "model-key-1")

        assert result == []


class TestGetAgent:
    @pytest.mark.asyncio
    async def test_returns_none_when_agent_not_found(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_agent("agent-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_agent_with_toolsets_knowledge_and_share_flag(
        self, neo4j_provider: Neo4jProvider
    ):
        # New batched projection order: agent → toolsets → knowledge → batched
        # app/KB doc lookup (via client.execute_query) → skills → org-share.
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"agent": {"id": "agent-1", "name": "Agent One"}}],  # agent query
                [{"agent_id": "agent-1", "toolset": {"_key": "ts1", "name": "Toolset1", "tools": []}}],  # toolsets
                [{"agent_id": "agent-1", "_key": "k1", "connectorId": "conn-1", "filters": "{}"}],  # knowledge
                [{"n": {"id": "conn-1", "name": "Jira", "type": "APP"}}],  # batched app-doc lookup
                [{"name": "pdf-extractor", "description": "Extracts tables", "category": "docs",
                  "subcategory": None, "version": "1.0.0", "status": "active",
                  "deprecatedReason": None, "replacedBy": None}],  # skills
                [],  # mcp servers projection
                [{"share_with_org": True}],  # org share query
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda node, _collection: {**node, "_key": node.get("id")}
        )

        result = await neo4j_provider.get_agent("agent-1", org_id="org-1", transaction="txn-1")

        assert result is not None
        assert result["_key"] == "agent-1"
        assert result["toolsets"] == [{"_key": "ts1", "name": "Toolset1", "tools": []}]
        assert result["knowledge"][0]["name"] == "Jira"
        assert result["knowledge"][0]["type"] == "APP"
        assert result["skills"] == [{
            "name": "pdf-extractor", "description": "Extracts tables", "category": "docs",
            "subcategory": None, "version": "1.0.0", "status": "active",
            "deprecatedReason": None, "replacedBy": None,
        }]
        assert result["shareWithOrg"] is True

    @pytest.mark.asyncio
    async def test_returns_empty_toolsets_and_knowledge_when_no_rows(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"agent": {"id": "agent-1", "name": "Agent One"}}],  # agent query
                [],  # toolsets query
                [],  # knowledge query
                [],  # skills query
                [],  # mcp servers projection
                [],  # org share query
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            return_value={"_key": "agent-1", "name": "Agent One"}
        )

        result = await neo4j_provider.get_agent("agent-1")

        assert result is not None
        assert result["toolsets"] == []
        assert result["knowledge"] == []
        assert result["skills"] == []
        assert result["mcpServers"] == []
        assert result["shareWithOrg"] is False

    @pytest.mark.asyncio
    async def test_invalid_filters_json_falls_back_to_empty_dict(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"agent": {"id": "agent-1", "name": "Agent One"}}],  # agent query
                [],  # toolsets query
                [{"agent_id": "agent-1", "_key": "k1", "connectorId": "conn-1", "filters": "{not-json"}],  # knowledge
                [{"n": {"id": "conn-1", "name": "Connector One", "type": "APP"}}],  # batched app-doc lookup
                [],  # skills query
                [],  # mcp servers projection
                [],  # org share query
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda node, _collection: {**node, "_key": node.get("id")}
        )

        result = await neo4j_provider.get_agent("agent-1")

        assert result is not None
        assert result["knowledge"][0]["filtersParsed"] == {}

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("boom"))

        result = await neo4j_provider.get_agent("agent-1")

        assert result is None


class TestProjectAgentSkills:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_skills_assigned(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider._project_agent_skills("agent-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_maps_rows_to_skill_dicts(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {
                    "name": "pdf-extractor", "description": "Extracts tables",
                    "category": "docs", "subcategory": "tables",
                    "version": "1.2.0", "status": "active",
                },
                {
                    "name": "csv-cleaner", "description": "Cleans CSV data",
                    "category": "docs", "subcategory": None,
                    "version": "2.0.0", "status": "deprecated",
                    "deprecatedReason": "superseded", "replacedBy": "csv-cleaner-v2",
                },
            ]
        )

        result = await neo4j_provider._project_agent_skills("agent-1", transaction="txn-1")

        assert result == [
            {
                "name": "pdf-extractor", "description": "Extracts tables",
                "category": "docs", "subcategory": "tables",
                "version": "1.2.0", "status": "active",
                "deprecatedReason": None, "replacedBy": None,
            },
            {
                "name": "csv-cleaner", "description": "Cleans CSV data",
                "category": "docs", "subcategory": None,
                "version": "2.0.0", "status": "deprecated",
                "deprecatedReason": "superseded", "replacedBy": "csv-cleaner-v2",
            },
        ]
        call_args = neo4j_provider.client.execute_query.await_args
        query = call_args.args[0]
        assert "skill.deprecatedReason AS deprecatedReason" in query
        assert "skill.replacedBy AS replacedBy" in query
        assert call_args.kwargs["parameters"] == {"agent_id": "agent-1"}
        assert call_args.kwargs["txn_id"] == "txn-1"


class TestCheckAgentPermission:
    @pytest.mark.asyncio
    async def test_returns_individual_access(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"role": "OWNER", "access_type": "INDIVIDUAL"}]
        )

        result = await neo4j_provider.check_agent_permission("agent-1", "user-1", "org-1")

        assert result is not None
        assert result["access_type"] == "INDIVIDUAL"
        assert result["user_role"] == "OWNER"
        assert result["can_edit"] is True
        assert result["can_delete"] is True
        assert result["can_share"] is True
        assert result["can_view"] is True

    @pytest.mark.asyncio
    async def test_returns_org_access_when_individual_missing(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [],  # individual query — no individual access
                [{"role": "WRITER", "access_type": "ORG"}],  # org query
            ]
        )

        result = await neo4j_provider.check_agent_permission("agent-1", "user-1", "org-1")

        assert result is not None
        assert result["access_type"] == "ORG"
        assert result["user_role"] == "WRITER"
        assert result["can_edit"] is True
        assert result["can_delete"] is False
        assert result["can_share"] is False

    @pytest.mark.asyncio
    async def test_returns_none_when_no_access(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [],  # individual query
                [],  # org query
            ]
        )

        result = await neo4j_provider.check_agent_permission("agent-1", "user-1", "org-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db down"))

        result = await neo4j_provider.check_agent_permission("agent-1", "user-1", "org-1")

        assert result is None


class TestGetAgentsByWebSearchProvider:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_agents_by_web_search_provider("org-1", "serper")

        assert result == []


class TestGetAllAgents:
    @pytest.mark.asyncio
    async def test_returns_list_when_paging_not_requested(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [
                    {
                        "agent": {"id": "a1", "name": "Agent 1", "updatedAtTimestamp": 10},
                        "role": "OWNER",
                        "access_type": "INDIVIDUAL",
                        "priority": 1,
                    }
                ],
                [{"org_shared_ids": ["a1"]}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda data, _collection: {"_key": data["id"], "name": data["name"], "updatedAtTimestamp": data.get("updatedAtTimestamp")}
        )
        neo4j_provider._project_agents_toolsets_and_knowledge = AsyncMock(return_value={"a1": {"toolsets": [], "knowledge": []}})  # type: ignore[method-assign]
        neo4j_provider._project_agents_mcp_servers = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await neo4j_provider.get_all_agents("user-1", "org-1")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["_key"] == "a1"
        assert result[0]["shareWithOrg"] is True
        assert result[0]["can_delete"] is True

    @pytest.mark.asyncio
    async def test_dedupes_and_keeps_highest_priority_permission(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [
                    {
                        "agent": {"id": "a1", "name": "Agent 1", "updatedAtTimestamp": 10},
                        "role": "READER",
                        "access_type": "ORG",
                        "priority": 3,
                    },
                    {
                        "agent": {"id": "a1", "name": "Agent 1", "updatedAtTimestamp": 10},
                        "role": "OWNER",
                        "access_type": "INDIVIDUAL",
                        "priority": 1,
                    },
                ],
                [{"org_shared_ids": []}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda data, _collection: {"_key": data["id"], "name": data["name"], "updatedAtTimestamp": data.get("updatedAtTimestamp")}
        )
        neo4j_provider._project_agents_toolsets_and_knowledge = AsyncMock(return_value={"a1": {"toolsets": [], "knowledge": []}})  # type: ignore[method-assign]
        neo4j_provider._project_agents_mcp_servers = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await neo4j_provider.get_all_agents("user-1", "org-1")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["user_role"] == "OWNER"
        assert result[0]["access_type"] == "INDIVIDUAL"
        assert result[0]["can_delete"] is True

    @pytest.mark.asyncio
    async def test_returns_paginated_response_with_total_items(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [
                    {"agent": {"id": "a1", "name": "A", "updatedAtTimestamp": 30}, "role": "OWNER", "access_type": "INDIVIDUAL", "priority": 1},
                    {"agent": {"id": "a2", "name": "B", "updatedAtTimestamp": 20}, "role": "OWNER", "access_type": "INDIVIDUAL", "priority": 1},
                    {"agent": {"id": "a3", "name": "C", "updatedAtTimestamp": 10}, "role": "OWNER", "access_type": "INDIVIDUAL", "priority": 1},
                ],
                [{"org_shared_ids": []}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda data, _collection: {"_key": data["id"], "name": data["name"], "updatedAtTimestamp": data.get("updatedAtTimestamp")}
        )
        neo4j_provider._project_agents_toolsets_and_knowledge = AsyncMock(return_value={"a2": {"toolsets": [], "knowledge": []}})  # type: ignore[method-assign]
        neo4j_provider._project_agents_mcp_servers = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await neo4j_provider.get_all_agents("user-1", "org-1", page=2, limit=1)

        assert isinstance(result, dict)
        assert result["totalItems"] == 3
        assert len(result["agents"]) == 1
        assert result["agents"][0]["_key"] == "a2"

    @pytest.mark.asyncio
    async def test_search_is_normalized_and_passed_to_query(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_all_agents("user-1", "org-1", search="  AGENT  ")

        assert result == []
        call_kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert call_kwargs["parameters"]["q"] == "agent"

    @pytest.mark.asyncio
    async def test_org_share_defaults_false_when_no_agents(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_all_agents("user-1", "org-1")

        assert result == []


class TestUpdateAgent:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_permission(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value=None)  # type: ignore[method-assign]

        result = await neo4j_provider.update_agent("a1", {"name": "New"}, "u1", "o1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_user_cannot_edit(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_edit": False}
        )

        result = await neo4j_provider.update_agent("a1", {"name": "New"}, "u1", "o1")

        assert result is False

    @pytest.mark.asyncio
    async def test_updates_allowed_fields_and_models_and_returns_true(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_edit": True}
        )
        neo4j_provider.update_node = AsyncMock(return_value=True)  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms",
            return_value=123456789,
        ):
            result = await neo4j_provider.update_agent(
                "a1",
                {
                    "name": "Updated Name",
                    "description": "Updated Description",
                    "unknownField": "ignore-me",
                    "models": [
                        {"modelKey": "mk1", "modelName": "gpt4"},
                        {"modelKey": "mk1", "modelName": "gpt4"},  # duplicate
                        "mk2_modelX",
                        "mk3",
                    ],
                },
                "u1",
                "o1",
                transaction="txn-1",
            )

        assert result is True
        neo4j_provider.update_node.assert_awaited_once()
        args, kwargs = neo4j_provider.update_node.await_args
        assert args[0] == "a1"
        # CollectionNames.AGENT_INSTANCES.value
        assert args[1] == "agentInstances"
        payload = args[2]
        assert payload["updatedAtTimestamp"] == 123456789
        assert payload["updatedBy"] == "u1"
        assert payload["name"] == "Updated Name"
        assert payload["description"] == "Updated Description"
        assert "unknownField" not in payload
        assert payload["models"] == ["mk1_gpt4", "mk2_modelX", "mk3"]
        assert kwargs["transaction"] == "txn-1"

    @pytest.mark.asyncio
    async def test_models_none_becomes_empty_list(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_edit": True}
        )
        neo4j_provider.update_node = AsyncMock(return_value=True)  # type: ignore[method-assign]

        await neo4j_provider.update_agent("a1", {"models": None}, "u1", "o1")

        args, _kwargs = neo4j_provider.update_node.await_args
        payload = args[2]
        assert payload["models"] == []

    @pytest.mark.asyncio
    async def test_returns_false_when_update_node_fails(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_edit": True}
        )
        neo4j_provider.update_node = AsyncMock(return_value=False)  # type: ignore[method-assign]

        result = await neo4j_provider.update_agent("a1", {"name": "N"}, "u1", "o1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(side_effect=RuntimeError("perm error"))  # type: ignore[method-assign]

        result = await neo4j_provider.update_agent("a1", {"name": "N"}, "u1", "o1")

        assert result is False


class TestDeleteAgent:
    @pytest.mark.asyncio
    async def test_returns_false_when_agent_not_found(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_document = AsyncMock(return_value=None)  # type: ignore[method-assign]

        result = await neo4j_provider.delete_agent("a1", "u1", "o1")

        assert result is False


class TestHardDeleteAgent:
    @pytest.mark.asyncio
    async def test_hard_delete_happy_path_returns_aggregate_counts(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"kid_list": ["k1", "k2"], "rel_count": 2}],  # step 1
                [{"deleted_count": 2}],  # step 2 knowledge delete
                [{"toolset_ids": ["ts1", "ts2"]}],  # step 3
                [{"tid_list": ["t1", "t2"], "rel_count": 3}],  # step 4 tool rel delete
                [{"deleted_count": 2}],  # step 5 tool delete
                [{"rel_count": 2}],  # step 6 agent->toolset rel delete
                [{"deleted_count": 2}],  # step 7 toolset delete
                [{"deleted_count": 1}],  # step 8 delete agent
            ]
        )

        result = await neo4j_provider.hard_delete_agent("agent-1", transaction="txn-1")

        assert result == {
            "agents_deleted": 1,
            "toolsets_deleted": 2,
            "tools_deleted": 2,
            "knowledge_deleted": 2,
            "edges_deleted": 7,  # 2 + 3 + 2
        }
        assert neo4j_provider.client.execute_query.await_count == 8

    @pytest.mark.asyncio
    async def test_skips_knowledge_and_toolset_branches_when_lists_empty(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"kid_list": [], "rel_count": 0}],  # step 1
                [{"toolset_ids": []}],  # step 3
                [{"rel_count": 0}],  # step 6
                [{"deleted_count": 1}],  # step 8
            ]
        )

        result = await neo4j_provider.hard_delete_agent("agent-1")

        assert result == {
            "agents_deleted": 1,
            "toolsets_deleted": 0,
            "tools_deleted": 0,
            "knowledge_deleted": 0,
            "edges_deleted": 0,
        }
        # Only steps 1, 3, 6, 8 should execute in this branch
        assert neo4j_provider.client.execute_query.await_count == 4

    @pytest.mark.asyncio
    async def test_handles_none_results_without_crashing(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                None,  # step 1
                None,  # step 3
                None,  # step 6
                None,  # step 8
            ]
        )

        result = await neo4j_provider.hard_delete_agent("agent-1")

        assert result == {
            "agents_deleted": 0,
            "toolsets_deleted": 0,
            "tools_deleted": 0,
            "knowledge_deleted": 0,
            "edges_deleted": 0,
        }


class TestHardDeleteAllAgents:
    @pytest.mark.asyncio
    async def test_hard_delete_all_happy_path_returns_aggregate_counts(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"knowledge_ids": ["k1", "k2"]}],  # step 1
                [{"rel_count": 3}],  # step 2
                [{"knowledge_count": 2}],  # step 3
                [{"toolset_ids": ["ts1"]}],  # step 4
                [{"tool_ids": ["t1", "t2"]}],  # step 5
                [{"rel_count": 2}],  # step 6
                [{"tool_count": 2}],  # step 7
                [{"rel_count": 1}],  # step 8
                [{"toolset_count": 1}],  # step 9
                [{"perm_count": 4}],  # step 10
                [{"agent_count": 5}],  # step 11 count
                [],  # step 11 detach delete
            ]
        )

        result = await neo4j_provider.hard_delete_all_agents(transaction="txn-all")

        assert result == {
            "agents_deleted": 5,
            "toolsets_deleted": 1,
            "tools_deleted": 2,
            "knowledge_deleted": 2,
            "edges_deleted": 10,  # 3 + 2 + 1 + 4
        }

    @pytest.mark.asyncio
    async def test_skips_optional_deletes_when_ids_empty(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"knowledge_ids": []}],  # step 1
                [{"rel_count": 0}],  # step 2
                [{"toolset_ids": []}],  # step 4
                [{"rel_count": 0}],  # step 8
                [{"perm_count": 0}],  # step 10
                [{"agent_count": 0}],  # step 11 count
                [],  # detach delete
            ]
        )

        result = await neo4j_provider.hard_delete_all_agents()

        assert result == {
            "agents_deleted": 0,
            "toolsets_deleted": 0,
            "tools_deleted": 0,
            "knowledge_deleted": 0,
            "edges_deleted": 0,
        }

    @pytest.mark.asyncio
    async def test_returns_zero_counts_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await neo4j_provider.hard_delete_all_agents()

        assert result == {
            "agents_deleted": 0,
            "toolsets_deleted": 0,
            "tools_deleted": 0,
            "knowledge_deleted": 0,
            "edges_deleted": 0,
        }


class TestDocumentOperations:
    @pytest.mark.asyncio
    async def test_get_document_returns_transformed_doc(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"n": {"id": "doc1", "name": "Doc"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "doc1", "name": "Doc"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_document("doc1", "apps", transaction="txn-doc")

        assert result == {"_key": "doc1", "name": "Doc"}
        neo4j_provider.client.execute_query.assert_awaited_once()
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"key": "doc1"}
        assert kwargs["txn_id"] == "txn-doc"

    @pytest.mark.asyncio
    async def test_get_document_returns_none_when_missing(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_document("missing", "apps")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_document_returns_none_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await neo4j_provider.get_document("doc1", "apps")

        assert result is None

    @pytest.mark.asyncio
    async def test_a_failed_lookup_raises_when_the_caller_asks(self, neo4j_provider: Neo4jProvider):
        """None reads as "no such document", so callers acting on that need the truth."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        with pytest.raises(RuntimeError, match="db fail"):
            await neo4j_provider.get_document("doc1", "apps", raise_on_error=True)

    @pytest.mark.asyncio
    async def test_a_missing_document_is_still_none_when_raising(self, neo4j_provider: Neo4jProvider):
        """Asking for failures to be raised must not turn absence into one."""
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        assert await neo4j_provider.get_document("missing", "apps", raise_on_error=True) is None

    @pytest.mark.asyncio
    async def test_get_all_documents_returns_transformed_list(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"n": {"id": "d1", "name": "A"}}, {"n": {"id": "d2", "name": "B"}}]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda node, _collection: {"_key": node["id"], "name": node["name"]}
        )

        result = await neo4j_provider.get_all_documents("apps", transaction="txn-all-docs")

        assert result == [{"_key": "d1", "name": "A"}, {"_key": "d2", "name": "B"}]

    @pytest.mark.asyncio
    async def test_get_all_documents_returns_empty_on_no_rows(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_all_documents("apps")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_all_documents_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await neo4j_provider.get_all_documents("apps")

        assert result == []


class TestNodeOperations:
    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_returns_true_for_empty_input(self, neo4j_provider: Neo4jProvider):
        result = await neo4j_provider.batch_upsert_nodes([], "apps")
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_converts_and_validates_and_executes(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider._arango_to_neo4j_node = MagicMock(  # type: ignore[method-assign]
            side_effect=[{"_key": "n1", "name": "A"}, {"id": "n2", "name": "B"}]
        )
        neo4j_provider.validator.validate_node_update = MagicMock()
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"id": "n1"}, {"id": "n2"}])

        result = await neo4j_provider.batch_upsert_nodes(
            [{"_key": "n1", "name": "A"}, {"id": "n2", "name": "B"}],
            "apps",
            transaction="txn-bu",
        )

        assert result is True
        neo4j_provider.client.execute_query.assert_awaited_once()
        params = neo4j_provider.client.execute_query.await_args.kwargs["parameters"]
        assert params["nodes"][0]["id"] == "n1"  # derived from _key
        assert params["nodes"][1]["id"] == "n2"

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._arango_to_neo4j_node = MagicMock(return_value={"id": "n1"})  # type: ignore[method-assign]
        neo4j_provider.validator.validate_node_update = MagicMock(side_effect=RuntimeError("invalid"))

        with pytest.raises(RuntimeError, match="invalid"):
            await neo4j_provider.batch_upsert_nodes([{"id": "n1"}], "apps")

    @pytest.mark.asyncio
    async def test_delete_nodes_returns_true_for_empty_keys(self, neo4j_provider: Neo4jProvider):
        result = await neo4j_provider.delete_nodes([], "apps")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_nodes_returns_true_when_all_deleted(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 2}])

        result = await neo4j_provider.delete_nodes(["k1", "k2"], "apps", transaction="txn-delnodes")

        assert result is True
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"keys": ["k1", "k2"]}
        assert kwargs["txn_id"] == "txn-delnodes"

    @pytest.mark.asyncio
    async def test_delete_nodes_returns_false_when_partial_delete(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 1}])

        result = await neo4j_provider.delete_nodes(["k1", "k2"], "apps")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_nodes_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        with pytest.raises(RuntimeError, match="db fail"):
            await neo4j_provider.delete_nodes(["k1"], "apps")

    @pytest.mark.asyncio
    async def test_update_node_returns_true_when_row_found(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._arango_to_neo4j_node = MagicMock(return_value={"name": "Updated"})  # type: ignore[method-assign]
        neo4j_provider.validator.validate_node_update = MagicMock()
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"n": {"id": "k1"}}])

        result = await neo4j_provider.update_node("k1", "apps", {"name": "Updated"}, transaction="txn-upd")

        assert result is True
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"key": "k1", "updates": {"name": "Updated"}}
        assert kwargs["txn_id"] == "txn-upd"

    @pytest.mark.asyncio
    async def test_update_node_returns_false_when_no_rows(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._arango_to_neo4j_node = MagicMock(return_value={"name": "Updated"})  # type: ignore[method-assign]
        neo4j_provider.validator.validate_node_update = MagicMock()
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.update_node("k1", "apps", {"name": "Updated"})

        assert result is False

    @pytest.mark.asyncio
    async def test_update_node_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._arango_to_neo4j_node = MagicMock(return_value={"name": "Updated"})  # type: ignore[method-assign]
        neo4j_provider.validator.validate_node_update = MagicMock(side_effect=RuntimeError("invalid"))

        with pytest.raises(RuntimeError, match="invalid"):
            await neo4j_provider.update_node("k1", "apps", {"name": "Updated"})


class TestEdgeOperations:
    @pytest.mark.asyncio
    async def test_batch_create_edges_returns_true_for_empty_input(self, neo4j_provider: Neo4jProvider):
        result = await neo4j_provider.batch_create_edges([], "permission")
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_create_edges_arango_format_success(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._parse_arango_id = MagicMock(  # type: ignore[method-assign]
            side_effect=[("users", "u1"), ("agentInstances", "a1")]
        )
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"created": 1}])

        result = await neo4j_provider.batch_create_edges(
            [{"_from": "users/u1", "_to": "agentInstances/a1", "role": "READER"}],
            "permission",
            transaction="txn-e1",
        )

        assert result is True
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["txn_id"] == "txn-e1"
        assert kwargs["parameters"]["edges"][0]["from_key"] == "u1"
        assert kwargs["parameters"]["edges"][0]["to_key"] == "a1"
        assert kwargs["parameters"]["edges"][0]["props"]["role"] == "READER"

    @pytest.mark.asyncio
    async def test_batch_create_edges_generic_format_success(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"created": 1}])

        result = await neo4j_provider.batch_create_edges(
            [
                {
                    "from_id": "u1",
                    "to_id": "a1",
                    "from_collection": "users",
                    "to_collection": "agentInstances",
                    "type": "USER",
                }
            ],
            "permission",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_batch_create_edges_skips_invalid_entries_and_returns_true(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock()

        result = await neo4j_provider.batch_create_edges(
            [
                {"foo": "bar"},  # invalid
                {"from_id": "u1", "to_id": "a1", "from_collection": "", "to_collection": "agentInstances"},  # missing fields
            ],
            "permission",
        )

        assert result is True
        neo4j_provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_create_edges_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("edge fail"))

        with pytest.raises(RuntimeError, match="edge fail"):
            await neo4j_provider.batch_create_edges(
                [{"from_id": "u1", "to_id": "a1", "from_collection": "users", "to_collection": "agentInstances"}],
                "permission",
            )

    @pytest.mark.asyncio
    async def test_get_edge_returns_edge_data_with_node_context(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": {"role": "READER", "type": "USER"}}])

        result = await neo4j_provider.get_edge(
            "u1", "users", "a1", "agentInstances", "permission", transaction="txn-ge"
        )

        assert result == {
            "role": "READER",
            "type": "USER",
            "from_id": "u1",
            "from_collection": "users",
            "to_id": "a1",
            "to_collection": "agentInstances",
        }

    @pytest.mark.asyncio
    async def test_get_edge_returns_none_when_missing(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_edge("u1", "users", "a1", "agentInstances", "permission")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_edge_returns_none_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await neo4j_provider.get_edge("u1", "users", "a1", "agentInstances", "permission")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_edge_returns_true_when_deleted(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 1}])

        result = await neo4j_provider.delete_edge("u1", "users", "a1", "agentInstances", "permission")

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_edge_returns_false_when_not_deleted(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 0}])

        result = await neo4j_provider.delete_edge("u1", "users", "a1", "agentInstances", "permission")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_edge_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("delete fail"))

        with pytest.raises(RuntimeError, match="delete fail"):
            await neo4j_provider.delete_edge("u1", "users", "a1", "agentInstances", "permission")

    @pytest.mark.asyncio
    async def test_batch_delete_edges_returns_zero_for_empty_input(self, neo4j_provider: Neo4jProvider):
        result = await neo4j_provider.batch_delete_edges([], "permission")
        assert result == 0

    @pytest.mark.asyncio
    async def test_batch_delete_edges_returns_deleted_count(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 2}])

        result = await neo4j_provider.batch_delete_edges(
            [
                {"from_id": "u1", "to_id": "a1", "from_collection": "users", "to_collection": "agentInstances"},
                {"from_id": "u2", "to_id": "a2", "from_collection": "users", "to_collection": "agentInstances"},
            ],
            "permission",
        )

        assert result == 2

    @pytest.mark.asyncio
    async def test_batch_delete_edges_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("batch delete fail"))

        with pytest.raises(RuntimeError, match="batch delete fail"):
            await neo4j_provider.batch_delete_edges(
                [{"from_id": "u1", "to_id": "a1", "from_collection": "users", "to_collection": "agentInstances"}],
                "permission",
            )

    @pytest.mark.asyncio
    async def test_delete_edges_from_returns_count(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 3}])

        result = await neo4j_provider.delete_edges_from("u1", "users", "permission")

        assert result == 3

    @pytest.mark.asyncio
    async def test_delete_edges_to_returns_count(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 4}])

        result = await neo4j_provider.delete_edges_to("a1", "agentInstances", "permission")

        assert result == 4

    @pytest.mark.asyncio
    async def test_delete_edges_to_groups_returns_count(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 1}])

        result = await neo4j_provider.delete_edges_to_groups("u1", "users", "permission")

        assert result == 1

    @pytest.mark.asyncio
    async def test_delete_edges_between_collections_returns_count(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 5}])

        result = await neo4j_provider.delete_edges_between_collections(
            "u1", "users", "permission", "agentInstances"
        )

        assert result == 5


class TestAdvancedEdgeOperations:
    @pytest.mark.asyncio
    async def test_batch_create_entity_relations_empty_input_returns_true(
        self, neo4j_provider: Neo4jProvider
    ):
        result = await neo4j_provider.batch_create_entity_relations([])
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_create_entity_relations_success_with_arango_and_generic_formats(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider._parse_arango_id = MagicMock(  # type: ignore[method-assign]
            side_effect=[("users", "u1"), ("records", "r1")]
        )
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"created": 1}])

        result = await neo4j_provider.batch_create_entity_relations(
            [
                {"_from": "users/u1", "_to": "records/r1", "edgeType": "ASSIGNED_TO", "weight": 10},
                {
                    "from_id": "u2",
                    "to_id": "r2",
                    "from_collection": "users",
                    "to_collection": "records",
                    "edgeType": "CREATED_BY",
                    "weight": 20,
                },
            ],
            transaction="txn-er",
        )

        assert result is True
        assert neo4j_provider.client.execute_query.await_count == 2

    @pytest.mark.asyncio
    async def test_batch_create_entity_relations_skips_invalid_edges(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock()

        result = await neo4j_provider.batch_create_entity_relations(
            [
                {"foo": "bar"},  # missing _from/_to and from_id/to_id
                {
                    "from_id": "u1",
                    "to_id": "r1",
                    "from_collection": "users",
                    "to_collection": "records",
                    # missing edgeType
                },
            ]
        )

        assert result is True
        neo4j_provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_create_entity_relations_raises_on_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("relation fail"))

        with pytest.raises(RuntimeError, match="relation fail"):
            await neo4j_provider.batch_create_entity_relations(
                [
                    {
                        "from_id": "u1",
                        "to_id": "r1",
                        "from_collection": "users",
                        "to_collection": "records",
                        "edgeType": "ASSIGNED_TO",
                    }
                ]
            )

    @pytest.mark.asyncio
    async def test_delete_nodes_and_edges_no_keys_is_noop(self, neo4j_provider: Neo4jProvider):
        await neo4j_provider.delete_nodes_and_edges([], "records")
        neo4j_provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_nodes_and_edges_executes_query(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 2}])

        await neo4j_provider.delete_nodes_and_edges(["r1", "r2"], "records", transaction="txn-delne")

        neo4j_provider.client.execute_query.assert_awaited_once()
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"keys": ["r1", "r2"]}
        assert kwargs["txn_id"] == "txn-delne"

    @pytest.mark.asyncio
    async def test_delete_nodes_and_edges_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("delete nodes fail"))

        with pytest.raises(RuntimeError, match="delete nodes fail"):
            await neo4j_provider.delete_nodes_and_edges(["r1"], "records")

    @pytest.mark.asyncio
    async def test_delete_all_edges_for_node_returns_zero_when_parse_fails(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("", ""))  # type: ignore[method-assign]

        result = await neo4j_provider.delete_all_edges_for_node("bad-key", "permission")

        assert result == 0
        neo4j_provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_all_edges_for_node_returns_deleted_count(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("users", "u1"))  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 4}])

        result = await neo4j_provider.delete_all_edges_for_node("users/u1", "permission", transaction="txn-dea")

        assert result == 4
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"key": "u1"}
        assert kwargs["txn_id"] == "txn-dea"

    @pytest.mark.asyncio
    async def test_delete_all_edges_for_node_returns_zero_when_no_rows(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("users", "u1"))  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.delete_all_edges_for_node("users/u1", "permission")

        assert result == 0

    @pytest.mark.asyncio
    async def test_delete_all_edges_for_node_raises_on_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("users", "u1"))  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("delete edges fail"))

        with pytest.raises(RuntimeError, match="delete edges fail"):
            await neo4j_provider.delete_all_edges_for_node("users/u1", "permission")


class TestQueryAndFilterHelpers:
    @pytest.mark.asyncio
    async def test_execute_query_passes_bind_vars_and_txn(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"x": 1}])

        result = await neo4j_provider.execute_query(
            "MATCH (n) RETURN n", bind_vars={"k": "v"}, transaction="txn-q1"
        )

        assert result == [{"x": 1}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"k": "v"}
        assert kwargs["txn_id"] == "txn-q1"

    @pytest.mark.asyncio
    async def test_execute_query_uses_empty_dict_when_bind_vars_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.execute_query("MATCH (n) RETURN n")

        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {}

    @pytest.mark.asyncio
    async def test_execute_query_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("query fail"))

        with pytest.raises(RuntimeError, match="query fail"):
            await neo4j_provider.execute_query("MATCH (n) RETURN n")

    @pytest.mark.asyncio
    async def test_get_nodes_by_filters_with_return_fields(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"id": "n1", "name": "A"}, {"id": "n2", "name": "B"}]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda node, _collection: {"_key": node.get("id"), "name": node.get("name")}
        )

        result = await neo4j_provider.get_nodes_by_filters(
            "apps",
            {"status": "ACTIVE", "deletedAt": None},
            return_fields=["id", "name"],
            transaction="txn-f1",
        )

        assert result == [{"_key": "n1", "name": "A"}, {"_key": "n2", "name": "B"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"status": "ACTIVE"}
        assert kwargs["txn_id"] == "txn-f1"

    @pytest.mark.asyncio
    async def test_get_nodes_by_filters_without_filters_returns_all(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"n": {"id": "n1", "name": "A"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "n1", "name": "A"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_nodes_by_filters("apps", {})

        assert result == [{"_key": "n1", "name": "A"}]

    @pytest.mark.asyncio
    async def test_get_nodes_by_filters_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("filters fail"))

        result = await neo4j_provider.get_nodes_by_filters("apps", {"status": "ACTIVE"})

        assert result == []

    @pytest.mark.asyncio
    async def test_get_nodes_by_filters_raises_when_asked(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("filters fail"))

        with pytest.raises(RuntimeError, match="filters fail"):
            await neo4j_provider.get_nodes_by_filters("apps", {"status": "ACTIVE"}, raise_on_error=True)

    @pytest.mark.asyncio
    async def test_get_edges_from_node_with_target_name_raises_when_asked(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("edges fail"))

        assert await neo4j_provider.get_edges_from_node_with_target_name("records/r1", "belongsTo") == []
        with pytest.raises(RuntimeError, match="edges fail"):
            await neo4j_provider.get_edges_from_node_with_target_name("records/r1", "belongsTo", raise_on_error=True)

    @pytest.mark.asyncio
    async def test_get_documents_by_status_returns_raw_nodes(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"n": {"id": "n1", "indexingStatus": "FAILED"}}]
        )

        result = await neo4j_provider.get_documents_by_status("records", "FAILED", transaction="txn-ds")

        assert result == [{"id": "n1", "indexingStatus": "FAILED"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"status": "FAILED"}
        assert kwargs["txn_id"] == "txn-ds"

    @pytest.mark.asyncio
    async def test_get_documents_by_status_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("status fail"))

        result = await neo4j_provider.get_documents_by_status("records", "FAILED")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_nodes_by_field_in_with_return_fields(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"id": "n1", "name": "A"}, {"id": "n2", "name": "B"}]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda node, _collection: {"_key": node.get("id"), "name": node.get("name")}
        )

        result = await neo4j_provider.get_nodes_by_field_in(
            "apps", "id", ["n1", "n2"], return_fields=["id", "name"], transaction="txn-in"
        )

        assert result == [{"_key": "n1", "name": "A"}, {"_key": "n2", "name": "B"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"values": ["n1", "n2"]}
        assert kwargs["txn_id"] == "txn-in"

    @pytest.mark.asyncio
    async def test_get_nodes_by_field_in_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("field in fail"))

        result = await neo4j_provider.get_nodes_by_field_in("apps", "id", ["n1"])

        assert result == []

    @pytest.mark.asyncio
    async def test_remove_nodes_by_field_returns_deleted_count(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 3}])

        result = await neo4j_provider.remove_nodes_by_field(
            "apps", "status", field_value="DELETED", transaction="txn-rm"
        )

        assert result == 3
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"value": "DELETED"}
        assert kwargs["txn_id"] == "txn-rm"

    @pytest.mark.asyncio
    async def test_remove_nodes_by_field_returns_zero_when_no_rows(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.remove_nodes_by_field("apps", "status", field_value="DELETED")

        assert result == 0

    @pytest.mark.asyncio
    async def test_remove_nodes_by_field_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("remove fail"))

        with pytest.raises(RuntimeError, match="remove fail"):
            await neo4j_provider.remove_nodes_by_field("apps", "status", field_value="DELETED")


class TestTraversalAndRecordLookups:
    @pytest.mark.asyncio
    async def test_get_edges_to_node_maps_labels_to_collections(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("records", "r1"))  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {
                    "r": {"edgeType": "PARENT_CHILD"},
                    "from_id": "r-parent",
                    "from_labels": ["Record"],
                    "to_id": "r1",
                    "to_labels": ["Record"],
                }
            ]
        )

        result = await neo4j_provider.get_edges_to_node("records/r1", "recordRelations", transaction="txn-e2n")

        assert len(result) == 1
        assert result[0]["edgeType"] == "PARENT_CHILD"
        assert result[0]["from_id"] == "r-parent"
        assert result[0]["to_id"] == "r1"
        assert result[0]["from_collection"] != ""
        assert result[0]["to_collection"] != ""

    @pytest.mark.asyncio
    async def test_get_edges_to_node_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("records", "r1"))  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("edges fail"))

        result = await neo4j_provider.get_edges_to_node("records/r1", "recordRelations")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_related_nodes_inbound_and_outbound(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("records", "r1"))  # type: ignore[method-assign]
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda n, _c: {"_key": n["id"], "name": n.get("name")}
        )
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"to": {"id": "r-parent", "name": "Parent"}}],  # inbound
                [{"to": {"id": "r-child", "name": "Child"}}],  # outbound
            ]
        )

        inbound = await neo4j_provider.get_related_nodes("records/r1", "recordRelations", "records")
        outbound = await neo4j_provider.get_related_nodes(
            "records/r1", "recordRelations", "records", direction="outbound"
        )

        assert inbound == [{"_key": "r-parent", "name": "Parent"}]
        assert outbound == [{"_key": "r-child", "name": "Child"}]

    @pytest.mark.asyncio
    async def test_get_related_nodes_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("records", "r1"))  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("related fail"))

        result = await neo4j_provider.get_related_nodes("records/r1", "recordRelations", "records")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_related_node_field_returns_values(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("records", "r1"))  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"value": "A"}, {"value": "B"}])

        result = await neo4j_provider.get_related_node_field(
            "records/r1", "recordRelations", "records", "recordName", direction="outbound"
        )

        assert result == ["A", "B"]

    @pytest.mark.asyncio
    async def test_get_related_node_field_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._parse_arango_id = MagicMock(return_value=("records", "r1"))  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("field fail"))

        result = await neo4j_provider.get_related_node_field(
            "records/r1", "recordRelations", "records", "recordName"
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_success(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": {"id": "r1", "recordType": "FILE"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "r1", "recordType": "FILE"})  # type: ignore[method-assign]

        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
            return_value={"record": "ok"},
        ) as mock_from_base:
            result = await neo4j_provider.get_record_by_external_id("conn-1", "ext-1")

        assert result == {"record": "ok"}
        mock_from_base.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_none_and_failure(self, neo4j_provider: Neo4jProvider):
        """None means no such record, and callers create one when told that."""
        from app.exceptions.graph_db_exceptions import GraphQueryError

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_record_by_external_id("conn-1", "ext-1") is None

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("ext fail"))
        with pytest.raises(GraphQueryError):
            await neo4j_provider.get_record_by_external_id("conn-1", "ext-1")

    @pytest.mark.asyncio
    async def test_a_record_that_will_not_rebuild_raises_the_same_way(self, neo4j_provider: Neo4jProvider):
        """A stored record the model rejects leaves the caller as unable to answer
        "does this exist?" as an unreachable database does, so it has to raise the
        same error -- and on both backends, not one."""
        from app.exceptions.graph_db_exceptions import GraphQueryError

        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"r": {"id": "r1"}}]  # nothing else the model needs
        )

        with pytest.raises(GraphQueryError):
            await neo4j_provider.get_record_by_external_id("conn-1", "ext-1")

    @pytest.mark.asyncio
    async def test_get_record_key_by_external_id(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"key": "r1"}])
        assert await neo4j_provider.get_record_key_by_external_id("ext-1", "conn-1") == "r1"

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_record_key_by_external_id("ext-1", "conn-1") is None

    @pytest.mark.asyncio
    async def test_get_records_by_virtual_record_id(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"record_key": "r1"}, {"record_key": "r2"}, {"record_key": None}]
        )

        result = await neo4j_provider.get_records_by_virtual_record_id(
            "vr-1", accessible_record_ids=["r1", "r2"]
        )

        assert result == ["r1", "r2"]

    @pytest.mark.asyncio
    async def test_get_records_by_virtual_record_id_returns_empty_on_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("vr fail"))

        result = await neo4j_provider.get_records_by_virtual_record_id("vr-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_record_by_path_returns_result_or_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"result": {"id": "r-final"}}])
        result = await neo4j_provider.get_record_by_path("conn-1", ["root", "child"], "rg-1")
        assert result == {"id": "r-final"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_record_by_path("conn-1", ["root"], "rg-1") is None

    @pytest.mark.asyncio
    async def test_get_record_by_path_raises_when_the_lookup_fails(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("path fail"))
        with pytest.raises(GraphQueryError):
            await neo4j_provider.get_record_by_path("conn-1", ["root"], "rg-1")

    @pytest.mark.asyncio
    async def test_get_records_by_status_returns_typed_records(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"r": {"id": "r1", "recordType": "FILE"}, "typeDoc": {"id": "f1"}},
                {"r": {"id": "r2", "recordType": "MAIL"}, "typeDoc": {"id": "m1"}},
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(side_effect=lambda n, _c: n)  # type: ignore[method-assign]
        neo4j_provider._create_typed_record_from_neo4j = MagicMock(  # type: ignore[method-assign]
            side_effect=[{"typed": "file"}, {"typed": "mail"}]
        )

        result = await neo4j_provider.get_records_by_status(
            "org-1", "conn-1", ["FAILED"], limit=10, offset=0
        )

        assert result == [{"typed": "file"}, {"typed": "mail"}]

    @pytest.mark.asyncio
    async def test_get_records_by_status_raises_on_query_failure(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("status2 fail"))

        with pytest.raises(GraphQueryError):
            await neo4j_provider.get_records_by_status("org-1", "conn-1", ["FAILED"])

    @pytest.mark.asyncio
    async def test_a_broken_row_is_not_reported_as_an_unreadable_listing(self, neo4j_provider: Neo4jProvider):
        """A bug converting a row is a bug, not a database that could not be read.

        Callers treat GraphQueryError as "try again later" - the rebuild turns it
        into a 409 - which would bury a crash in our own conversion code.
        """
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": object()}])

        with pytest.raises(TypeError):
            await neo4j_provider.get_records_by_status("org-1", "conn-1", ["FAILED"])

    @pytest.mark.asyncio
    async def test_record_group_lookup_raises_on_query_failure(self, neo4j_provider: Neo4jProvider):
        """None means no such group, so a failure must not answer None.

        Callers create a record group when they are told None; on a database
        blip that quietly makes a duplicate, and the folder-scope cleanup reads
        it as an empty bucket and marks the scope clean.
        """
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("connection refused"))

        with pytest.raises(GraphQueryError):
            await neo4j_provider.get_record_group_by_external_id("conn-1", "bucket-a")

    @pytest.mark.asyncio
    async def test_get_records_by_parent_success_and_record_type_filter(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"record": {"id": "r1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "r1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
            return_value={"record": "converted"},
        ):
            result = await neo4j_provider.get_records_by_parent(
                "conn-1", "parent-ext-1", record_type="FILE", transaction="txn-rbp"
            )

        assert result == [{"record": "converted"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"]["record_type"] == "FILE"
        assert kwargs["txn_id"] == "txn-rbp"

    @pytest.mark.asyncio
    async def test_get_records_by_parent_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("parent fail"))

        with pytest.raises(RuntimeError, match="parent fail"):
            await neo4j_provider.get_records_by_parent("conn-1", "parent-ext-1")

    @pytest.mark.asyncio
    async def test_get_records_by_record_type_success(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"record": {"id": "r1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "r1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
            return_value={"record": "converted"},
        ):
            result = await neo4j_provider.get_records_by_record_type(
                "conn-1", "DATABASE", transaction="txn-type"
            )

        assert result == [{"record": "converted"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"]["record_type"] == "DATABASE"
        assert kwargs["txn_id"] == "txn-type"

    @pytest.mark.asyncio
    async def test_get_records_by_record_type_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("type fail"))

        with pytest.raises(RuntimeError, match="type fail"):
            await neo4j_provider.get_records_by_record_type("conn-1", "DATABASE")

    @pytest.mark.asyncio
    async def test_get_records_by_record_group_validates_depth(self, neo4j_provider: Neo4jProvider):
        result = await neo4j_provider.get_records_by_record_group("rg-1", "conn-1", "org-1", depth=-2)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_records_by_record_group_success_with_limit_offset(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._get_permission_role_cypher = MagicMock(return_value="WITH 'OWNER' as permission_role")  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"record": {"id": "r1"}, "typeDoc": {"id": "f1"}}]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(side_effect=lambda n, _c: n)  # type: ignore[method-assign]
        neo4j_provider._create_typed_record_from_neo4j = MagicMock(return_value={"typed": "record"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_records_by_record_group(
            "rg-1",
            "conn-1",
            "org-1",
            depth=1,
            user_key="u1",
            limit=10,
            offset=5,
            transaction="txn-rg",
        )

        assert result == [{"typed": "record"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"]["limit"] == 10
        assert kwargs["parameters"]["offset"] == 5
        assert kwargs["txn_id"] == "txn-rg"

    @pytest.mark.asyncio
    async def test_get_records_by_parent_record_validates_depth(self, neo4j_provider: Neo4jProvider):
        result = await neo4j_provider.get_records_by_parent_record(
            "rec-1", "conn-1", "org-1", depth=-2
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_get_records_by_parent_record_success(self, neo4j_provider: Neo4jProvider):
        neo4j_provider._get_permission_role_cypher = MagicMock(return_value="WITH 'OWNER' as permission_role")  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"record": {"id": "r1"}, "typeDoc": {"id": "f1"}}]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(side_effect=lambda n, _c: n)  # type: ignore[method-assign]
        neo4j_provider._create_typed_record_from_neo4j = MagicMock(return_value={"typed": "child"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_records_by_parent_record(
            "rec-1", "conn-1", "org-1", depth=1, user_key="u1"
        )

        assert result == [{"typed": "child"}]

    @pytest.mark.asyncio
    async def test_get_record_by_issue_key_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"record": {"id": "r1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "r1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
            return_value={"issue": "record"},
        ):
            found = await neo4j_provider.get_record_by_issue_key("conn-1", "PROJ-123")
        assert found == {"issue": "record"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        missing = await neo4j_provider.get_record_by_issue_key("conn-1", "PROJ-999")
        assert missing is None

    @pytest.mark.asyncio
    async def test_get_record_by_conversation_index_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": {"id": "r1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "r1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.Record.from_arango_base_record",
            return_value={"mail": "record"},
        ):
            found = await neo4j_provider.get_record_by_conversation_index(
                "conn-1", "conv-1", "thread-1", "org-1", "user-1"
            )
        assert found == {"mail": "record"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        missing = await neo4j_provider.get_record_by_conversation_index(
            "conn-1", "conv-1", "thread-1", "org-1", "user-1"
        )
        assert missing is None

    @pytest.mark.asyncio
    async def test_get_record_path_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"file_path": "root/folder/file"}])
        assert await neo4j_provider.get_record_path("r1") == "root/folder/file"

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_record_path("r1") is None

    @pytest.mark.asyncio
    async def test_record_group_and_file_lookup_helpers(self, neo4j_provider: Neo4jProvider):
        # get_record_group_by_external_id
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"rg": {"id": "rg1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rg1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.RecordGroup.from_arango_base_record_group",
            return_value={"group": "ok"},
        ):
            rg = await neo4j_provider.get_record_group_by_external_id("conn-1", "ext-rg")
        assert rg == {"group": "ok"}

        # get_record_group_by_id delegates to get_document
        neo4j_provider.get_document = AsyncMock(return_value={"_key": "rg1"})  # type: ignore[method-assign]
        rg_id = await neo4j_provider.get_record_group_by_id("rg1")
        assert rg_id == {"_key": "rg1"}

        # get_file_record_by_id success
        neo4j_provider.get_document = AsyncMock(side_effect=[{"_key": "f1"}, {"_key": "r1"}])  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.FileRecord.from_arango_record",
            return_value={"fileRecord": "ok"},
        ):
            fr = await neo4j_provider.get_file_record_by_id("r1")
        assert fr == {"fileRecord": "ok"}

        # get_file_record_by_id missing file or record
        neo4j_provider.get_document = AsyncMock(side_effect=[None])  # type: ignore[method-assign]
        assert await neo4j_provider.get_file_record_by_id("r1") is None

    @pytest.mark.asyncio
    async def test_get_file_record_by_id_answers_none_only_when_nothing_is_stored(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_file_record_by_id("r1") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing_read", [0, 1], ids=["file node", "record node"])
    async def test_get_file_record_by_id_raises_when_a_read_fails(self, neo4j_provider: Neo4jProvider, failing_read: int) -> None:
        # Through the real get_document: it swallows failures unless asked to raise.
        results: list[object] = [[{"n": {"id": "r1"}}], [{"n": {"id": "r1"}}]]
        results[failing_read] = Exception("neo4j unavailable")
        neo4j_provider.client.execute_query = AsyncMock(side_effect=results)
        with pytest.raises(GraphQueryError, match="r1"):
            await neo4j_provider.get_file_record_by_id("r1")


def _neo4j_group(key: str = "g1", email: str = "sales@example.com") -> dict:
    return {
        "id": key, "name": "Sales", "externalGroupId": email, "connectorName": "DRIVE WORKSPACE",
        "connectorId": "c1", "orgId": "o1", "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    }


class TestGetGroupsWithPermissionToNode:
    @pytest.mark.asyncio
    async def test_returns_the_groups_with_an_edge_to_the_node(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"g": _neo4j_group()}])
        groups = await neo4j_provider.get_groups_with_permission_to_node("records/r1", "records", transaction="t1")
        assert [(g.id, g.source_user_group_id) for g in groups] == [("g1", "sales@example.com")]
        call = neo4j_provider.client.execute_query.call_args
        assert call.kwargs["parameters"] == {"node_key": "r1"}
        assert call.kwargs["txn_id"] == "t1"
        assert "(g:Group)-[r:PERMISSION]->(n)" in call.args[0]

    @pytest.mark.asyncio
    async def test_a_failed_read_answers_empty_unless_asked_to_raise(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("down"))
        assert await neo4j_provider.get_groups_with_permission_to_node("r1", "records") == []
        with pytest.raises(RuntimeError):
            await neo4j_provider.get_groups_with_permission_to_node("r1", "records", raise_on_error=True)


class TestUserAndOrganizationLookups:
    @pytest.mark.asyncio
    async def test_get_user_by_email_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"u": {"id": "u1", "email": "a@x.com"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "u1", "email": "a@x.com"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.User.from_arango_user",
            return_value={"user": "ok"},
        ):
            found = await neo4j_provider.get_user_by_email("a@x.com", transaction="txn-ue")
        assert found == {"user": "ok"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_user_by_email("missing@x.com") is None

    @pytest.mark.asyncio
    async def test_get_user_by_source_id_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"u": {"id": "u1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "u1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.User.from_arango_user",
            return_value={"user": "ok"},
        ):
            found = await neo4j_provider.get_user_by_source_id("src-1", "conn-1")
        assert found == {"user": "ok"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_user_by_source_id("src-missing", "conn-1") is None

    @pytest.mark.asyncio
    async def test_get_user_by_user_id_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"u": {"id": "u1", "userId": "uid1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "u1", "userId": "uid1"})  # type: ignore[method-assign]
        assert await neo4j_provider.get_user_by_user_id("uid1") == {"_key": "u1", "userId": "uid1"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_user_by_user_id("missing") is None

    @pytest.mark.asyncio
    async def test_get_users_active_filter_and_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"u": {"id": "u1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "u1"})  # type: ignore[method-assign]

        active_users = await neo4j_provider.get_users("org-1", active=True)
        assert active_users == [{"_key": "u1"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"]["active"] is True

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("users fail"))
        assert await neo4j_provider.get_users("org-1", active=False) == []

    @pytest.mark.asyncio
    async def test_get_app_user_by_email_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"u": {"id": "u1"}, "sourceUserId": "src-1"}]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "u1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.AppUser.from_arango_user",
            return_value={"appUser": "ok"},
        ):
            found = await neo4j_provider.get_app_user_by_email("a@x.com", "conn-1")
        assert found == {"appUser": "ok"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_app_user_by_email("missing@x.com", "conn-1") is None

    @pytest.mark.asyncio
    async def test_get_app_users_success_and_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"u": {"id": "u1"}, "sourceUserId": "src-1", "appName": "jira"}]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "u1"})  # type: ignore[method-assign]

        users = await neo4j_provider.get_app_users("org-1", "conn-1")
        assert users == [{"_key": "u1", "sourceUserId": "src-1", "appName": "JIRA"}]

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("app users fail"))
        assert await neo4j_provider.get_app_users("org-1", "conn-1") == []

    @pytest.mark.asyncio
    async def test_get_user_group_by_external_id_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"g": {"id": "g1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "g1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.AppUserGroup.from_arango_base_user_group",
            return_value={"group": "ok"},
        ):
            found = await neo4j_provider.get_user_group_by_external_id("conn-1", "ext-g")
        assert found == {"group": "ok"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_user_group_by_external_id("conn-1", "missing") is None

    @pytest.mark.asyncio
    async def test_get_user_groups_success_and_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"g": {"id": "g1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "g1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.AppUserGroup.from_arango_base_user_group",
            return_value={"group": "ok"},
        ):
            groups = await neo4j_provider.get_user_groups("conn-1", "org-1")
        assert groups == [{"group": "ok"}]

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("groups fail"))
        assert await neo4j_provider.get_user_groups("conn-1", "org-1") == []

    @pytest.mark.asyncio
    async def test_get_app_role_by_external_id_success_and_none(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": {"id": "role1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "role1"})  # type: ignore[method-assign]
        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.AppRole.from_arango_base_role",
            return_value={"role": "ok"},
        ):
            found = await neo4j_provider.get_app_role_by_external_id("conn-1", "ext-role")
        assert found == {"role": "ok"}

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_app_role_by_external_id("conn-1", "missing") is None

    @pytest.mark.asyncio
    async def test_get_all_orgs_active_and_all(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"o": {"id": "org1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "org1"})  # type: ignore[method-assign]

        active_orgs = await neo4j_provider.get_all_orgs(active=True, transaction="txn-orgs")
        assert active_orgs == [{"_key": "org1"}]

        all_orgs = await neo4j_provider.get_all_orgs(active=False)
        assert all_orgs == [{"_key": "org1"}]

    @pytest.mark.asyncio
    async def test_get_org_apps_success_and_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"app": {"id": "app1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "app1"})  # type: ignore[method-assign]

        apps = await neo4j_provider.get_org_apps("org-1")
        assert apps == [{"_key": "app1"}]
        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert "app.isActive = true" in query

        apps_all = await neo4j_provider.get_org_apps("org-1", active_only=False)
        query_all = neo4j_provider.client.execute_query.await_args.args[0]
        assert "app.isActive = true" not in query_all
        assert apps_all == [{"_key": "app1"}]

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("apps fail"))
        assert await neo4j_provider.get_org_apps("org-1") == []

    @pytest.mark.asyncio
    async def test_reset_indexing_status_for_connector(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider.reset_indexing_status_for_connector(
            "app-1", "NOT_STARTED", exclude_statuses=["IN_PROGRESS"]
        )
        query = neo4j_provider.client.execute_query.await_args.args[0]
        params = neo4j_provider.client.execute_query.await_args.kwargs["parameters"]
        assert "n.connectorId = $connector_id" in query
        assert params["connector_id"] == "app-1"
        assert params["status"] == "NOT_STARTED"
        assert params["exclude_statuses"] == ["IN_PROGRESS"]

    @pytest.mark.asyncio
    async def test_get_departments_with_and_without_org(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"d.departmentName": "Engineering"}, {"d.departmentName": "HR"}]
        )

        with_org = await neo4j_provider.get_departments("org-1", transaction="txn-dept")
        without_org = await neo4j_provider.get_departments()

        assert with_org == ["Engineering", "HR"]
        assert without_org == ["Engineering", "HR"]

    @pytest.mark.asyncio
    async def test_get_departments_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("dept fail"))

        result = await neo4j_provider.get_departments("org-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_logs_warning_when_orphan_cleanup_partial(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"kid_list": ["k1", "k2"], "rel_count": 2}],  # step 1
                [{"deleted_count": 1}],  # step 2 knowledge deleted only one -> warn
                [{"toolset_ids": []}],  # step 3
                [{"rel_count": 0}],  # step 6
                [{"deleted_count": 1}],  # step 8
            ]
        )

        await neo4j_provider.hard_delete_agent("agent-1")

        assert neo4j_provider.logger.warning.called

    @pytest.mark.asyncio
    async def test_returns_zero_counts_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await neo4j_provider.hard_delete_agent("agent-1")

        assert result == {
            "agents_deleted": 0,
            "toolsets_deleted": 0,
            "tools_deleted": 0,
            "knowledge_deleted": 0,
            "edges_deleted": 0,
        }

    @pytest.mark.asyncio
    async def test_returns_false_when_no_permission(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_document = AsyncMock(return_value={"_key": "a1"})  # type: ignore[method-assign]
        neo4j_provider.check_agent_permission = AsyncMock(return_value=None)  # type: ignore[method-assign]

        result = await neo4j_provider.delete_agent("a1", "u1", "o1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_user_cannot_delete(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_document = AsyncMock(return_value={"_key": "a1"})  # type: ignore[method-assign]
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_delete": False}
        )

        result = await neo4j_provider.delete_agent("a1", "u1", "o1")

        assert result is False

    @pytest.mark.asyncio
    async def test_soft_delete_success_calls_update_node(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_document = AsyncMock(return_value={"_key": "a1"})  # type: ignore[method-assign]
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_delete": True}
        )
        neo4j_provider.update_node = AsyncMock(return_value=True)  # type: ignore[method-assign]

        with patch(
            "app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms",
            return_value=987654321,
        ):
            result = await neo4j_provider.delete_agent("a1", "u1", "o1", transaction="txn-del")

        assert result is True
        neo4j_provider.update_node.assert_awaited_once()
        args, kwargs = neo4j_provider.update_node.await_args
        assert args[0] == "a1"
        assert args[1] == "agentInstances"
        payload = args[2]
        assert payload["isDeleted"] is True
        assert payload["deletedAtTimestamp"] == 987654321
        assert payload["deletedByUserId"] == "u1"
        assert kwargs["transaction"] == "txn-del"

    @pytest.mark.asyncio
    async def test_returns_false_when_soft_delete_update_fails(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_document = AsyncMock(return_value={"_key": "a1"})  # type: ignore[method-assign]
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_delete": True}
        )
        neo4j_provider.update_node = AsyncMock(return_value=False)  # type: ignore[method-assign]

        result = await neo4j_provider.delete_agent("a1", "u1", "o1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_document = AsyncMock(side_effect=RuntimeError("db fail"))  # type: ignore[method-assign]

        result = await neo4j_provider.delete_agent("a1", "u1", "o1")

        assert result is False
        # delete_agent exits early when get_document raises
        assert neo4j_provider.client.execute_query.await_count == 0

    @pytest.mark.asyncio
    async def test_paging_uses_non_negative_start_index(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [
                    {"agent": {"id": "a1", "name": "A", "updatedAtTimestamp": 1}, "role": "OWNER", "access_type": "INDIVIDUAL", "priority": 1}
                ],
                [{"org_shared_ids": []}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda data, _collection: {"_key": data["id"], "name": data["name"], "updatedAtTimestamp": data.get("updatedAtTimestamp")}
        )
        neo4j_provider._project_agents_toolsets_and_knowledge = AsyncMock(return_value={"a1": {"toolsets": [], "knowledge": []}})  # type: ignore[method-assign]
        neo4j_provider._project_agents_mcp_servers = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await neo4j_provider.get_all_agents("user-1", "org-1", page=0, limit=10)

        assert isinstance(result, dict)
        assert result["totalItems"] == 1
        assert len(result["agents"]) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await neo4j_provider.get_all_agents("user-1", "org-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_web_search_agents_dedupe_rows_by_agent_key(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"name": "A", "_key": "a1", "creatorName": "Alice"},
                {"name": "A duplicate", "_key": "a1", "creatorName": "Alice"},
                {"name": "B", "_key": "a2", "creatorName": None},
            ]
        )

        result = await neo4j_provider.get_agents_by_web_search_provider("org-1", "serper")

        assert result == [
            {"name": "A", "_key": "a1", "creatorName": "Alice"},
            {"name": "B", "_key": "a2", "creatorName": None},
        ]

    @pytest.mark.asyncio
    async def test_web_search_agents_skip_rows_without_key(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"name": "NoKeyA", "creatorName": "Alice"},
                {"name": "WithKey", "_key": "a2", "creatorName": "Bob"},
                {"name": "NoKeyB", "_key": None, "creatorName": "Chris"},
            ]
        )

        result = await neo4j_provider.get_agents_by_web_search_provider("org-1", "serper")

        assert result == [{"name": "WithKey", "_key": "a2", "creatorName": "Bob"}]

    @pytest.mark.asyncio
    async def test_passes_org_and_provider_parameters(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.get_agents_by_web_search_provider("org-9", "tavily")

        neo4j_provider.client.execute_query.assert_awaited_once()
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"org_id": "org-9", "provider": "tavily"}

    @pytest.mark.asyncio
    async def test_web_search_agents_return_empty_on_query_error(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("query failed"))

        result = await neo4j_provider.get_agents_by_web_search_provider("org-1", "serper")

        assert result == []

    @pytest.mark.asyncio
    async def test_dedupes_rows_by_agent_key(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"name": "A", "_key": "a1", "creatorName": "Alice"},
                {"name": "A duplicate", "_key": "a1", "creatorName": "Alice"},
                {"name": "B", "_key": "a2", "creatorName": None},
            ]
        )

        result = await neo4j_provider.get_agents_by_model_key("org-1", "model-key-1")

        assert result == [
            {"name": "A", "_key": "a1", "creatorName": "Alice"},
            {"name": "B", "_key": "a2", "creatorName": None},
        ]

    @pytest.mark.asyncio
    async def test_skips_rows_without_key(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"name": "NoKeyA", "creatorName": "Alice"},
                {"name": "WithKey", "_key": "a2", "creatorName": "Bob"},
                {"name": "NoKeyB", "_key": None, "creatorName": "Chris"},
            ]
        )

        result = await neo4j_provider.get_agents_by_model_key("org-1", "model-key-1")

        assert result == [{"name": "WithKey", "_key": "a2", "creatorName": "Bob"}]

    @pytest.mark.asyncio
    async def test_keeps_first_row_when_duplicate_key_has_conflicting_payload(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"name": "Original", "_key": "a1", "creatorName": "Alice"},
                {"name": "Mutated", "_key": "a1", "creatorName": "Mallory"},
                {"name": "Other", "_key": "a2", "creatorName": "Bob"},
            ]
        )

        result = await neo4j_provider.get_agents_by_model_key("org-1", "model-key-1")

        assert result == [
            {"name": "Original", "_key": "a1", "creatorName": "Alice"},
            {"name": "Other", "_key": "a2", "creatorName": "Bob"},
        ]

    @pytest.mark.asyncio
    async def test_passes_org_and_model_parameters(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.get_agents_by_model_key("org-9", "model-x")

        neo4j_provider.client.execute_query.assert_awaited_once()
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"org_id": "org-9", "model_key": "model-x"}

    @pytest.mark.asyncio
    async def test_returns_empty_on_query_error(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("query failed"))

        result = await neo4j_provider.get_agents_by_model_key("org-1", "model-key-1")

        assert result == []


class TestDuplicateAndSyncOperations:
    @pytest.mark.asyncio
    async def test_find_duplicate_records_with_optional_filters(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"r": {"id": "rec-2"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            return_value={"_key": "rec-2", "id": "rec-2"}
        )

        result = await neo4j_provider.find_duplicate_records(
            "rec-1",
            "md5-1",
            org_id="org-9",
            record_type="FILE",
            size_in_bytes=42,
            transaction="txn-dup",
        )

        assert result == [{"_key": "rec-2", "id": "rec-2"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {
            "md5_checksum": "md5-1",
            "record_key": "rec-1",
            "org_id": "org-9",
            "record_type": "FILE",
            "size_in_bytes": 42,
        }
        assert kwargs["txn_id"] == "txn-dup"

    @pytest.mark.asyncio
    async def test_find_duplicate_records_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("dup fail"))
        assert await neo4j_provider.find_duplicate_records("rec-1", "md5-1", org_id="org-9") == []

    @pytest.mark.asyncio
    async def test_find_duplicate_records_org_id_scoping(self, neo4j_provider: Neo4jProvider):
        """org_id is always passed as a query parameter, restricting dedup
        matches to within-org."""
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={})  # type: ignore[method-assign]

        await neo4j_provider.find_duplicate_records("rec-1", "md5-1", org_id="org-9")

        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"]["org_id"] == "org-9"

    @pytest.mark.asyncio
    async def test_find_next_queued_duplicate_returns_none_when_reference_not_found(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.find_next_queued_duplicate("rec-1") is None
        assert neo4j_provider.client.execute_query.await_count == 1

    @pytest.mark.asyncio
    async def test_find_next_queued_duplicate_returns_none_without_md5(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"record": {"id": "rec-1"}}])
        assert await neo4j_provider.find_next_queued_duplicate("rec-1") is None
        assert neo4j_provider.client.execute_query.await_count == 1

    @pytest.mark.asyncio
    async def test_find_next_queued_duplicate_success(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"record": {"id": "rec-1", "md5Checksum": "m1", "sizeInBytes": 10, "orgId": "org-1"}}],
                [{"record": {"id": "rec-2"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rec-2"})  # type: ignore[method-assign]

        found = await neo4j_provider.find_next_queued_duplicate("rec-1", transaction="txn-q")

        assert found == {"_key": "rec-2"}
        second_call = neo4j_provider.client.execute_query.await_args_list[1].kwargs
        assert second_call["parameters"]["queued_status"] == "QUEUED"
        assert second_call["parameters"]["size_in_bytes"] == 10
        assert second_call["parameters"]["org_id"] == "org-1"
        assert second_call["txn_id"] == "txn-q"

    @pytest.mark.asyncio
    async def test_get_taxonomy_entities_for_record_without_a_client_raises(self, neo4j_provider: Neo4jProvider):
        """[] would sync a duplicate's points without its taxonomy and log nothing."""
        neo4j_provider.client = None
        with pytest.raises(RuntimeError):
            await neo4j_provider.get_taxonomy_entities_for_record("rec-1")

    @pytest.mark.asyncio
    async def test_find_next_queued_duplicate_needs_an_org(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"record": {"id": "rec-1", "md5Checksum": "m1"}}]
        )

        assert await neo4j_provider.find_next_queued_duplicate("rec-1") is None
        assert neo4j_provider.client.execute_query.await_count == 1

    @pytest.mark.asyncio
    async def test_find_next_queued_duplicate_scopes_to_reference_records_org(
        self, neo4j_provider: Neo4jProvider
    ):
        """The queued-duplicate search must never cross into another org —
        scoped internally from the reference record's own orgId, so no
        caller can omit it."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"record": {"id": "rec-1", "md5Checksum": "m1", "orgId": "org-9"}}],
                [{"record": {"id": "rec-2"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rec-2"})  # type: ignore[method-assign]

        await neo4j_provider.find_next_queued_duplicate("rec-1")

        second_call = neo4j_provider.client.execute_query.await_args_list[1].kwargs
        assert second_call["parameters"]["org_id"] == "org-9"

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_returns_zero_on_missing_reference(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.update_queued_duplicates_status("rec-1", "COMPLETED") == 0

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_without_org_promotes_nothing(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"record": {"id": "rec-1", "md5Checksum": "m1"}}]
        )
        assert await neo4j_provider.update_queued_duplicates_status("rec-1", "COMPLETED") == 0
        assert neo4j_provider.client.execute_query.await_count == 1

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_is_scoped_to_reference_org(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[{"record": {"id": "rec-1", "orgId": "org-1", "md5Checksum": "m1", "extractionStatus": "COMPLETED"}}], []]
        )
        await neo4j_provider.update_queued_duplicates_status("rec-1", "COMPLETED")
        call = neo4j_provider.client.execute_query.await_args_list[1]
        assert "record.orgId = $org_id" in call.args[0]
        assert call.kwargs["parameters"]["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_updates_records_and_maps_completed_status(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"record": {"id": "rec-1", "orgId": "org-1", "md5Checksum": "m1", "sizeInBytes": 12, "extractionStatus": "COMPLETED"}}],
                [{"record": {"id": "rec-2"}}, {"record": {"id": "rec-3"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=[{"_key": "rec-2"}, {"_key": "rec-3"}]
        )
        neo4j_provider.batch_update_nodes = AsyncMock(return_value=True)  # type: ignore[method-assign]

        with patch("app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms", return_value=101):
            updated_count = await neo4j_provider.update_queued_duplicates_status(
                "rec-1",
                "COMPLETED",
                virtual_record_id="v-1",
                transaction="txn-upd",
            )

        assert updated_count == 2
        payload = neo4j_provider.batch_update_nodes.await_args.args[0]
        # The primary's reconcile flag rides in the same write as the promotion.
        # Re-armed with a fresh due time and attempt count, so a record that
        # gave up before gets the full retry budget again.
        assert payload[-1] == {
            "id": "rec-1", "duplicateReconcilePending": True,
            "duplicateReconcileAttempts": 0, "duplicateReconcileDueAt": 101 + DUPLICATE_RECONCILE_GRACE_MS,
        }
        assert len(payload) == 3
        assert payload[0]["indexingStatus"] == "COMPLETED"
        assert payload[0]["extractionStatus"] == "COMPLETED"
        assert payload[0]["virtualRecordId"] == "v-1"
        assert payload[1]["lastIndexTimestamp"] == 101
        assert neo4j_provider.batch_update_nodes.await_args.args[1] == "records"
        assert neo4j_provider.batch_update_nodes.await_args.args[2] == "txn-upd"

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_maps_failed_and_empty(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"record": {"id": "rec-1", "orgId": "org-1", "md5Checksum": "m1"}}],
                [{"record": {"id": "rec-2"}}],
                [{"record": {"id": "rec-1", "orgId": "org-1", "md5Checksum": "m1"}}],
                [{"record": {"id": "rec-2"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rec-2"})  # type: ignore[method-assign]
        neo4j_provider.batch_update_nodes = AsyncMock(return_value=True)  # type: ignore[method-assign]

        await neo4j_provider.update_queued_duplicates_status("rec-1", "FAILED")
        failed_payload = neo4j_provider.batch_update_nodes.await_args_list[0].args[0]
        assert failed_payload[0]["extractionStatus"] == "FAILED"
        # A failed primary has no taxonomy to copy (KG-51).
        assert all("duplicateReconcilePending" not in row for row in failed_payload)

        await neo4j_provider.update_queued_duplicates_status("rec-1", "EMPTY")
        empty_payload = neo4j_provider.batch_update_nodes.await_args_list[1].args[0]
        assert empty_payload[0]["extractionStatus"] == "EMPTY"
        assert empty_payload[-1]["duplicateReconcilePending"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("primary_extraction", ["IN_PROGRESS"])
    async def test_queued_duplicates_wait_while_the_primarys_enrichment_has_not_ended(
        self, neo4j_provider: Neo4jProvider, primary_extraction: str | None
    ) -> None:
        primary = {"id": "rec-1", "orgId": "org-1", "md5Checksum": "m1", "extractionStatus": primary_extraction}
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[[{"record": primary}], [{"record": {"id": "rec-2"}}]])
        neo4j_provider.batch_update_nodes = AsyncMock(return_value=True)  # type: ignore[method-assign]

        assert await neo4j_provider.update_queued_duplicates_status("rec-1", "COMPLETED", virtual_record_id="v-1") == 0

        neo4j_provider.batch_update_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_primary_from_before_the_status_was_written_still_promotes(
        self, neo4j_provider: Neo4jProvider
    ) -> None:
        primary = {"id": "rec-1", "orgId": "org-1", "md5Checksum": "m1"}
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[[{"record": primary}], [{"record": {"id": "rec-2"}}]])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rec-2"})  # type: ignore[method-assign]
        neo4j_provider.batch_update_nodes = AsyncMock(return_value=True)  # type: ignore[method-assign]

        assert await neo4j_provider.update_queued_duplicates_status("rec-1", "COMPLETED", virtual_record_id="v-1") == 1

        assert neo4j_provider.batch_update_nodes.await_args.args[0][0]["extractionStatus"] == "COMPLETED"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("primary_extraction", ["FAILED", "NOT_STARTED"])
    async def test_promoted_duplicates_take_the_primarys_enrichment_outcome(
        self, neo4j_provider: Neo4jProvider, primary_extraction: str
    ) -> None:
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"record": {"id": "rec-1", "orgId": "org-1", "md5Checksum": "m1", "extractionStatus": primary_extraction}}],
                [{"record": {"id": "rec-2"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rec-2"})  # type: ignore[method-assign]
        neo4j_provider.batch_update_nodes = AsyncMock(return_value=True)  # type: ignore[method-assign]

        await neo4j_provider.update_queued_duplicates_status("rec-1", "COMPLETED", virtual_record_id="v-1")

        assert neo4j_provider.batch_update_nodes.await_args.args[0][0]["extractionStatus"] == primary_extraction

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_includes_reason(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"record": {"id": "rec-1", "orgId": "org-1", "md5Checksum": "m1"}}],
                [{"record": {"id": "rec-2"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rec-2"})  # type: ignore[method-assign]
        neo4j_provider.batch_update_nodes = AsyncMock(return_value=True)  # type: ignore[method-assign]

        await neo4j_provider.update_queued_duplicates_status(
            "rec-1",
            "FAILED",
            reason="Primary duplicate indexing failed: Rate limit exceeded",
        )

        payload = neo4j_provider.batch_update_nodes.await_args.args[0]
        assert payload[0]["reason"] == "Primary duplicate indexing failed: Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_returns_minus_one_on_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("queue fail"))
        assert await neo4j_provider.update_queued_duplicates_status("rec-1", "COMPLETED") == -1

    @pytest.mark.asyncio
    async def test_copy_document_relationships_copies_expected_edge_types(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"target_id": "d1"}],  # department
                [{"target_id": "c1"}],  # category
                [],  # language
                [{"target_id": "t1"}],  # topic
            ]
        )
        neo4j_provider.batch_create_edges = AsyncMock()  # type: ignore[method-assign]

        with patch("app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms", return_value=111):
            ok = await neo4j_provider.copy_document_relationships("src-1", "dst-1", transaction="txn-copy")

        assert ok is True
        assert neo4j_provider.batch_create_edges.await_count == 3
        first_edges = neo4j_provider.batch_create_edges.await_args_list[0].args[0]
        assert first_edges[0]["to_collection"] == "departments"
        assert first_edges[0]["from_id"] == "dst-1"
        assert first_edges[0]["createdAtTimestamp"] == 111
        second_rel = neo4j_provider.batch_create_edges.await_args_list[1].args[1]
        assert second_rel == "BELONGS_TO_CATEGORY"
        last_rel = neo4j_provider.batch_create_edges.await_args_list[2].args[1]
        assert last_rel == "BELONGS_TO_TOPIC"

    @pytest.mark.asyncio
    async def test_copy_document_relationships_returns_false_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("copy fail"))
        assert await neo4j_provider.copy_document_relationships("src-1", "dst-1") is False

    @pytest.mark.asyncio
    async def test_copy_document_relationships_preserves_subcategory_label(
        self, neo4j_provider: Neo4jProvider
    ):
        """A BELONGS_TO_CATEGORY edge pointing at a Subcategory1 node must be
        copied into the subcategories1 collection, not silently promoted to
        'categories' -- batch_create_edges MATCHes on the label, so getting
        this wrong makes the MATCH miss the real node and silently drop the
        edge (the regression this fixes).
        """
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [],  # department
                [{"target_id": "sub-1", "target_labels": ["Subcategories1"]}],  # category
                [],  # language
                [],  # topic
            ]
        )
        neo4j_provider.batch_create_edges = AsyncMock()  # type: ignore[method-assign]

        ok = await neo4j_provider.copy_document_relationships("src-1", "dst-1")

        assert ok is True
        neo4j_provider.batch_create_edges.assert_awaited_once()
        edges = neo4j_provider.batch_create_edges.await_args.args[0]
        assert edges[0]["to_id"] == "sub-1"
        assert edges[0]["to_collection"] == "subcategories1"

    @pytest.mark.asyncio
    async def test_copy_document_relationships_deeper_subcategory_level(
        self, neo4j_provider: Neo4jProvider
    ):
        """Same regression, one level deeper: Subcategory3 must resolve to
        subcategories3, not categories or subcategories1."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [],  # department
                [{"target_id": "sub-3", "target_labels": ["Subcategories3"]}],  # category
                [],  # language
                [],  # topic
            ]
        )
        neo4j_provider.batch_create_edges = AsyncMock()  # type: ignore[method-assign]

        ok = await neo4j_provider.copy_document_relationships("src-1", "dst-1")

        assert ok is True
        edges = neo4j_provider.batch_create_edges.await_args.args[0]
        assert edges[0]["to_collection"] == "subcategories3"

    @pytest.mark.asyncio
    async def test_copy_document_relationships_unrecognised_label_falls_back(
        self, neo4j_provider: Neo4jProvider
    ):
        """An unrecognised label must not drop the edge outright -- it falls
        back to the group's first collection rather than raising."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [],  # department
                [{"target_id": "c-mystery", "target_labels": ["SomeUnknownLabel"]}],  # category
                [],  # language
                [],  # topic
            ]
        )
        neo4j_provider.batch_create_edges = AsyncMock()  # type: ignore[method-assign]

        ok = await neo4j_provider.copy_document_relationships("src-1", "dst-1")

        assert ok is True
        edges = neo4j_provider.batch_create_edges.await_args.args[0]
        assert edges[0]["to_collection"] == "categories"

    @pytest.mark.asyncio
    async def test_get_taxonomy_entities_for_record_empty_key_returns_empty(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock()
        result = await neo4j_provider.get_taxonomy_entities_for_record("")
        assert result == []
        neo4j_provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_taxonomy_entities_for_record_resolves_subcategory_level(
        self, neo4j_provider: Neo4jProvider
    ):
        """A Subcategory1-labelled node under BELONGS_TO_CATEGORY must resolve
        to entityType=subcategory, mirroring the edge-copy fix above."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [
                    {"entityId": "cat-1", "name": "Finance", "nodeLabels": ["Categories"]},
                    {"entityId": "sub-1", "name": "Budgets", "nodeLabels": ["Subcategories1"]},
                ],  # category_group
                [{"entityId": "dept-1", "name": "Engineering", "nodeLabels": ["Departments"]}],
                [{"entityId": "topic-1", "name": "OKRs", "nodeLabels": ["Topics"]}],
                [{"entityId": "lang-1", "name": "English", "nodeLabels": ["Languages"]}],
            ]
        )

        result = await neo4j_provider.get_taxonomy_entities_for_record("rec-1")

        by_id = {row["entityId"]: row for row in result}
        assert by_id["cat-1"]["entityType"] == "category"
        assert by_id["sub-1"]["entityType"] == "subcategory"
        assert by_id["dept-1"]["entityType"] == "department"
        assert by_id["topic-1"]["entityType"] == "topic"
        assert by_id["lang-1"]["entityType"] == "language"
        assert all("nodeLabels" not in row for row in result)

    @pytest.mark.asyncio
    async def test_get_taxonomy_entities_for_record_group_failure_raises(
        self, neo4j_provider: Neo4jProvider
    ):
        """A partial result would read as the record having fewer entities."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                RuntimeError("category query failed"),
                [{"entityId": "dept-1", "name": "Engineering", "nodeLabels": ["Departments"]}],
                [],
                [],
            ]
        )

        with pytest.raises(RuntimeError, match="category query failed"):
            await neo4j_provider.get_taxonomy_entities_for_record("rec-1")

    @pytest.mark.asyncio
    async def test_get_user_apps_success_and_error(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"app": {"id": "app-1"}}, {"app": None}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "app-1"})  # type: ignore[method-assign]
        apps = await neo4j_provider.get_user_apps("user-1", transaction="txn-apps")
        assert apps == [{"_key": "app-1"}]

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("apps fail"))
        with pytest.raises(RuntimeError):
            await neo4j_provider.get_user_apps("user-1")

    @pytest.mark.asyncio
    async def test_get_user_app_ids_filters_missing_ids(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": "a1"}, {"_key": "a2"}, None, {}, {"id": None, "_key": None}]
        )
        assert await neo4j_provider._get_user_app_ids("user-1") == ["a1", "a2"]

    @pytest.mark.asyncio
    async def test_sync_point_get_upsert_and_remove(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"sp": {"syncPointKey": "k1"}}])
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "k1"})  # type: ignore[method-assign]
        found = await neo4j_provider.get_sync_point("k1", "syncPoints", transaction="txn-sp")
        assert found == {"_key": "k1"}

        neo4j_provider.get_sync_point = AsyncMock(return_value=None)  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        with patch("app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms", return_value=200):
            created = await neo4j_provider.upsert_sync_point("k1", {"cursor": "c1"}, "syncPoints")
        assert created is True
        create_parameters = neo4j_provider.client.execute_query.await_args.args[1]
        assert create_parameters["data"]["createdAtTimestamp"] == 200
        assert create_parameters["data"]["updatedAtTimestamp"] == 200

        neo4j_provider.get_sync_point = AsyncMock(return_value={"_key": "k1"})  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        with patch("app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms", return_value=300):
            updated = await neo4j_provider.upsert_sync_point("k1", {"cursor": "c2"}, "syncPoints")
        assert updated is True
        update_parameters = neo4j_provider.client.execute_query.await_args.args[1]
        assert "createdAtTimestamp" not in update_parameters["data"]
        assert update_parameters["data"]["updatedAtTimestamp"] == 300

        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider.remove_sync_point("k1", "syncPoints", transaction="txn-rm")
        rm_kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert rm_kwargs["parameters"] == {"key": "k1"}
        assert rm_kwargs["txn_id"] == "txn-rm"

    @pytest.mark.asyncio
    async def test_sync_point_methods_error_paths(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("sync fail"))
        assert await neo4j_provider.get_sync_point("k1", "syncPoints") is None

        neo4j_provider.get_sync_point = AsyncMock(return_value=None)  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("upsert fail"))
        with pytest.raises(RuntimeError):
            await neo4j_provider.upsert_sync_point("k1", {"cursor": "c1"}, "syncPoints")

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("remove fail"))
        with pytest.raises(RuntimeError):
            await neo4j_provider.remove_sync_point("k1", "syncPoints")

    @pytest.mark.asyncio
    async def test_upsert_does_not_create_a_second_sync_point_when_its_read_fails(
        self, neo4j_provider: Neo4jProvider
    ):
        """The read that decides insert-or-update failing must not look like
        "no row here". Nothing makes syncPointKey unique and the CREATE sets no
        id, so the duplicate would land, and reads that LIMIT 1 would then race
        between two checkpoints for one key.

        get_sync_point is deliberately not stubbed: the fix is the flag it is
        called with, and a stub would answer the same either way.
        """
        queries: list[str] = []

        async def graph_flapping(query, *_args, **_kwargs):
            queries.append(query)
            if "MATCH" in query:
                raise RuntimeError("graph is restarting")
            return []  # the write that would create the duplicate succeeds

        neo4j_provider.client.execute_query = AsyncMock(side_effect=graph_flapping)

        with pytest.raises(RuntimeError):
            await neo4j_provider.upsert_sync_point("k1", {"cursor": "c1"}, "syncPoints")

        assert not any("CREATE" in q for q in queries), queries


class TestVirtualAccessAndRecordLookup:
    @pytest.mark.asyncio
    async def test_get_virtual_ids_for_connector_dedupes_and_passes_metadata(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"virtualId": "v1", "recordId": "r1"},
                {"virtualId": "v1", "recordId": "r1-mutated"},
                {"virtualId": "v2", "recordId": "r2"},
                {"virtualId": None, "recordId": "rx"},
            ]
        )

        result = await neo4j_provider._get_virtual_ids_for_connector(
            "user-1",
            "org-1",
            "conn-1",
            metadata_filters={
                "departments": ["Engineering"],
                "languages": ["English"],
                "topics": ["AI"],
            },
        )

        assert result == {"v1": "r1", "v2": "r2"}
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"]["userId"] == "user-1"
        assert kwargs["parameters"]["orgId"] == "org-1"
        assert kwargs["parameters"]["connectorId"] == "conn-1"
        assert kwargs["parameters"]["departmentNames"] == ["Engineering"]
        assert kwargs["parameters"]["languageNames"] == ["English"]
        assert kwargs["parameters"]["topicNames"] == ["AI"]

    @pytest.mark.asyncio
    async def test_get_virtual_ids_for_connector_follows_a_role_to_a_record(
        self, neo4j_provider: Neo4jProvider
    ) -> None:
        # BookStack shares a page with a role as Role -> Record. The real-graph check
        # is tests/integration/graph_db/test_connector_search_access.py.
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider._get_virtual_ids_for_connector("user-1", "org-1", "conn-1")

        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert "(userDoc)-[:PERMISSION]->(g:Group)-[:PERMISSION]->(r:Record)" not in query
        assert "(userDoc)-[:PERMISSION]->(g)-[:PERMISSION]->(r:Record)" in query
        assert "WHERE (g:Group OR g:Role)" in query

    @pytest.mark.asyncio
    async def test_get_virtual_ids_for_connector_returns_empty_on_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("connector fail"))
        assert await neo4j_provider._get_virtual_ids_for_connector("user-1", "org-1", "conn-1") == {}

    @pytest.mark.asyncio
    async def test_get_kb_virtual_ids_with_kb_filter_and_metadata(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"virtualId": "v1", "recordId": "r1"},
                {"virtualId": "v1", "recordId": "r1-later"},
                {"virtualId": "v2", "recordId": "r2"},
            ]
        )

        result = await neo4j_provider._get_kb_virtual_ids(
            "user-1",
            "org-1",
            kb_ids=["kb-1", "kb-2"],
            metadata_filters={"categories": ["Policy"]},
        )

        assert result == {"v1": "r1", "v2": "r2"}
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"]["kb_ids"] == ["kb-1", "kb-2"]
        assert kwargs["parameters"]["categoryNames"] == ["Policy"]

    @pytest.mark.asyncio
    async def test_get_kb_virtual_ids_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("kb fail"))
        assert await neo4j_provider._get_kb_virtual_ids("user-1", "org-1") == {}

    @pytest.mark.asyncio
    async def test_get_kb_virtual_ids_excludes_hidden_when_unfiltered(
        self, neo4j_provider: Neo4jProvider
    ):
        """The 'all accessible KBs' scenario (no kb_ids) must never surface a
        hidden KB (e.g. a project's linked file collection)."""
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider._get_kb_virtual_ids("user-1", "org-1", kb_ids=None)

        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert query.count("coalesce(kb.isHidden, false) = false") == 2

    @pytest.mark.asyncio
    async def test_get_kb_virtual_ids_explicit_filter_skips_hidden_exclusion(
        self, neo4j_provider: Neo4jProvider
    ):
        """An explicit kb_ids list (a project chat reaching its own hidden KB)
        is honoured as-is, without the hidden predicate."""
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider._get_kb_virtual_ids("user-1", "org-1", kb_ids=["hidden-kb"])

        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert "kb.id IN $kb_ids" in query
        assert "coalesce(kb.isHidden, false) = false" not in query

    @pytest.mark.asyncio
    async def test_get_accessible_kb_ids_excludes_hidden_by_default(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider._get_accessible_kb_ids("user-1")
        params = neo4j_provider.client.execute_query.await_args.kwargs["parameters"]
        assert params["includeHidden"] is False

    @pytest.mark.asyncio
    async def test_get_accessible_kb_ids_includes_hidden_when_flagged(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider._get_accessible_kb_ids("user-1", include_hidden=True)
        params = neo4j_provider.client.execute_query.await_args.kwargs["parameters"]
        assert params["includeHidden"] is True

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_returns_empty_when_user_missing(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
        assert await neo4j_provider.get_accessible_virtual_record_ids("user-1", "org-1") == {}

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_scenario_both_filters(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        # Mock get_user_apps to return app documents with type information
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {"id": "conn-1", "type": "google"},
                {"id": "kb-uuid-auto", "type": "KB"},  # KB app with proper type
                {"id": "conn-2", "type": "jira"},
            ]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock(  # type: ignore[method-assign]
            side_effect=[{"v1": "r1"}, {"v1": "r1-overwrite", "v2": "r2"}]
        )
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={"v3": "r3"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1",
            "org-1",
            filters={"apps": ["conn-1", "conn-2"], "kb": ["kb-1"], "topics": ["AI"]},
        )

        assert result == {"v1": "r1", "v2": "r2", "v3": "r3"}
        assert neo4j_provider._get_virtual_ids_for_connector.await_count == 2
        first_connector_call = neo4j_provider._get_virtual_ids_for_connector.await_args_list[0].args
        assert first_connector_call[2] == "conn-1"
        assert first_connector_call[3] == {"topics": ["AI"]}
        kb_call = neo4j_provider._get_kb_virtual_ids.await_args.args
        assert kb_call[2] == ["kb-1"]

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_scenario_only_kb(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})  # type: ignore[method-assign]
        neo4j_provider._get_user_app_ids = AsyncMock(return_value=["conn-1"])  # type: ignore[method-assign]
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock()  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={"vk": "rk"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1",
            "org-1",
            filters={"kb": ["kb-1"]},
        )

        assert result == {"vk": "rk"}
        neo4j_provider._get_virtual_ids_for_connector.assert_not_called()
        neo4j_provider._get_kb_virtual_ids.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_scenario_only_apps_skips_kb(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        # Mock get_user_apps to return both regular connector and KB app
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {"id": "conn-1", "type": "google"},
                {"id": "kb-uuid-sys", "type": "KB"},  # KB app with proper type
            ]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock(return_value={"v1": "r1"})  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock()  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1",
            "org-1",
            filters={"apps": ["conn-1"]},  # Only connector filter, no KB filter
        )

        assert result == {"v1": "r1"}
        neo4j_provider._get_virtual_ids_for_connector.assert_awaited_once()
        neo4j_provider._get_kb_virtual_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_strict_scope_empty_filters_short_circuits(
        self, neo4j_provider: Neo4jProvider
    ):
        """strictScope with no apps/kb must never fall back to Scenario 3's
        'search everything the user can access'."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": "conn-1", "type": "google"}]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock()  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock()  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1",
            "org-1",
            filters={"strictScope": True},
        )

        assert result == {}
        neo4j_provider._get_virtual_ids_for_connector.assert_not_called()
        neo4j_provider._get_kb_virtual_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_strict_scope_with_filters_still_queries(
        self, neo4j_provider: Neo4jProvider
    ):
        """strictScope only short-circuits an *empty* effective scope — an
        explicit kb/apps selection (e.g. a project's own hidden KB) still runs."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": "hidden-kb", "type": "KB"}]
        )
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={"v1": "r1"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1",
            "org-1",
            filters={"strictScope": True, "kb": ["hidden-kb"]},
        )

        assert result == {"v1": "r1"}
        neo4j_provider._get_kb_virtual_ids.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_unreachable_app_is_only_offered_to_the_kb_query(
        self, neo4j_provider: Neo4jProvider
    ):
        """An id that is not one of the user's connectors may still be a
        Collection shared with them, so the KB query — which checks its own
        permission edges — decides. It is never queried as a connector."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider._get_user_app_ids = AsyncMock(return_value=["conn-1"])  # type: ignore[method-assign]
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock()  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1",
            "org-1",
            filters={"apps": ["not-accessible"]},
        )

        assert result == {}
        neo4j_provider._get_virtual_ids_for_connector.assert_not_called()
        assert neo4j_provider._get_kb_virtual_ids.await_args.args[2] == ["not-accessible"]

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_honours_a_collection_id_under_apps(
        self, neo4j_provider: Neo4jProvider
    ):
        """Collection-page chat sends `apps: [collectionId], kb: []`. `apps` and
        `kb` are one scope, so the Collection is searched rather than dropped."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": "conn-1", "type": "google"}, {"id": "kb-1", "type": "KB"}]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock()  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={"vk": "rk"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1", "org-1", filters={"apps": ["kb-1"], "kb": []}
        )

        assert result == {"vk": "rk"}
        neo4j_provider._get_virtual_ids_for_connector.assert_not_called()
        assert neo4j_provider._get_kb_virtual_ids.await_args.args[2] == ["kb-1"]

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_honours_a_connector_id_under_kb(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": "conn-1", "type": "google"}]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock(return_value={"v1": "r1"})  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1", "org-1", filters={"kb": ["conn-1"]}
        )

        assert result == {"v1": "r1"}
        assert neo4j_provider._get_virtual_ids_for_connector.await_args.args[2] == "conn-1"
        # Still offered to the KB query, which matches nothing for a connector
        # id: an id named under `kb` is only known to be a Collection by that
        # query, so skipping it would lose a Collection whose app type could
        # not be read.

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_agent_sentinel_alone_searches_nothing(
        self, neo4j_provider: Neo4jProvider
    ):
        """Reading NO_KB_SELECTED as "no scope" would search the whole corpus."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": "conn-1", "type": "google"}]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock(return_value={"v1": "r1"})  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1", "org-1", filters={"apps": [], "kb": ["NO_KB_SELECTED"]}
        )

        assert result == {}
        neo4j_provider._get_virtual_ids_for_connector.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_connector_scope_runs_no_kb_query(
        self, neo4j_provider: Neo4jProvider
    ):
        """Every id is a connector, so there is nothing for the KB query to find."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": "conn-1", "type": "google"}, {"id": "conn-2", "type": "jira"}]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock(return_value={})  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock()  # type: ignore[method-assign]

        await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1", "org-1", filters={"apps": ["conn-2", "conn-1"]}
        )

        neo4j_provider._get_kb_virtual_ids.assert_not_called()
        queried = [c.args[2] for c in neo4j_provider._get_virtual_ids_for_connector.await_args_list]
        assert queried == ["conn-2", "conn-1"], "scope order decides which copy wins"

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_bare_string_scope_is_not_iterated(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider.get_user_apps = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"id": "a", "type": "google"}]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock(return_value={"v1": "r1"})  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={"v2": "r2"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1", "org-1", filters={"apps": "a"}
        )

        assert result == {}
        neo4j_provider._get_virtual_ids_for_connector.assert_not_called()
        neo4j_provider._get_kb_virtual_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_handles_task_exception_and_outer_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider._get_user_app_ids = AsyncMock(return_value=["conn-1"])  # type: ignore[method-assign]
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("task fail")
        )
        neo4j_provider._get_kb_virtual_ids = AsyncMock(return_value={"vk": "rk"})  # type: ignore[method-assign]

        partial = await neo4j_provider.get_accessible_virtual_record_ids("user-1", "org-1")
        assert partial == {"vk": "rk"}

        neo4j_provider.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("outer fail"))  # type: ignore[method-assign]
        fallback = await neo4j_provider.get_accessible_virtual_record_ids("user-1", "org-1")
        assert fallback == {}

    @pytest.mark.asyncio
    async def test_get_records_by_record_ids_empty_success_and_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        assert await neo4j_provider.get_records_by_record_ids([], "org-1") == []

        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"r": {"id": "r1"}}, {"x": "skip"}, {"r": {"id": "r2"}}]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=[{"_key": "r1"}, {"_key": "r2"}]
        )
        rows = await neo4j_provider.get_records_by_record_ids(["r1", "r2"], "org-9")
        assert rows == [{"_key": "r1"}, {"_key": "r2"}]
        query_kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert query_kwargs["parameters"] == {"record_ids": ["r1", "r2"], "org_id": "org-9"}

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("records fail"))
        assert await neo4j_provider.get_records_by_record_ids(["r1"], "org-1") == []


class TestRecordRelationOperations:
    @pytest.mark.asyncio
    async def test_batch_upsert_record_relations_empty_edges_returns_true(
        self, neo4j_provider: Neo4jProvider
    ):
        assert await neo4j_provider.batch_upsert_record_relations([]) is True
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_upsert_record_relations_transforms_edges_and_executes(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"upserted": 2}])
        edges = [
            {
                "from_id": "r1",
                "to_id": "r2",
                "from_collection": "records",
                "to_collection": "records",
                "relationshipType": "FOREIGN_KEY",
                "constraintName": "fk_a",
                "sourceColumn": "user_id",
            },
            {
                "_from": "records/r3",
                "_to": "records/r4",
                "relationshipType": "DEPENDS_ON",
                "targetColumn": "id",
            },
        ]

        result = await neo4j_provider.batch_upsert_record_relations(edges, transaction="txn-rel")

        assert result is True
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["txn_id"] == "txn-rel"
        payload = kwargs["parameters"]["edges"]
        assert payload[0]["from_key"] == "r1"
        assert payload[0]["to_key"] == "r2"
        assert payload[0]["relationshipType"] == "FOREIGN_KEY"
        assert payload[0]["constraintName"] == "fk_a"
        assert payload[0]["props"]["sourceColumn"] == "user_id"
        assert payload[1]["from_key"] == "r3"
        assert payload[1]["to_key"] == "r4"
        assert payload[1]["constraintName"] == ""
        assert payload[1]["props"]["targetColumn"] == "id"

    @pytest.mark.asyncio
    async def test_batch_upsert_record_relations_raises_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("upsert rel fail"))
        with pytest.raises(RuntimeError):
            await neo4j_provider.batch_upsert_record_relations([{"from_id": "r1", "to_id": "r2"}])

    @pytest.mark.asyncio
    async def test_get_child_record_ids_by_relation_type_success_and_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {
                    "record_id": "c1",
                    "childTable": "orders",
                    "sourceColumn": "user_id",
                    "targetColumn": "id",
                },
                {"record_id": "c2"},
            ]
        )

        rows = await neo4j_provider.get_child_record_ids_by_relation_type(
            "p1", "FOREIGN_KEY", transaction="txn-child"
        )
        assert rows == [
            {"record_id": "c1", "childTable": "orders", "sourceColumn": "user_id", "targetColumn": "id"},
            {"record_id": "c2", "childTable": "", "sourceColumn": "", "targetColumn": ""},
        ]
        child_kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert child_kwargs["parameters"] == {"record_id": "p1", "relation_type": "FOREIGN_KEY"}
        assert child_kwargs["txn_id"] == "txn-child"

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("child fail"))
        assert await neo4j_provider.get_child_record_ids_by_relation_type("p1", "FOREIGN_KEY") == []

    @pytest.mark.asyncio
    async def test_get_virtual_record_ids_for_record_ids_empty_success_and_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        assert await neo4j_provider.get_virtual_record_ids_for_record_ids([]) == {}

        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"record_id": "r1", "virtualRecordId": "v1"},
                {"record_id": "r2", "virtualRecordId": "v2"},
            ]
        )
        result = await neo4j_provider.get_virtual_record_ids_for_record_ids(
            ["r1", "r2"], transaction="txn-vid"
        )
        assert result == {"r1": "v1", "r2": "v2"}
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"record_ids": ["r1", "r2"]}
        assert kwargs["txn_id"] == "txn-vid"

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("vid fail"))
        assert await neo4j_provider.get_virtual_record_ids_for_record_ids(["r1"]) == {}

    @pytest.mark.asyncio
    async def test_get_parent_record_ids_by_relation_type_success_and_exception(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {
                    "record_id": "p1",
                    "parentTable": "users",
                    "sourceColumn": "user_id",
                    "targetColumn": "id",
                },
                {"record_id": "p2"},
            ]
        )

        rows = await neo4j_provider.get_parent_record_ids_by_relation_type(
            "c1", "FOREIGN_KEY", transaction="txn-parent"
        )
        assert rows == [
            {"record_id": "p1", "parentTable": "users", "sourceColumn": "user_id", "targetColumn": "id"},
            {"record_id": "p2", "parentTable": "", "sourceColumn": "", "targetColumn": ""},
        ]
        parent_kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert parent_kwargs["parameters"] == {"record_id": "c1", "relation_type": "FOREIGN_KEY"}
        assert parent_kwargs["txn_id"] == "txn-parent"

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("parent fail"))
        assert await neo4j_provider.get_parent_record_ids_by_relation_type("c1", "FOREIGN_KEY") == []


class TestKnowledgeHubSearchThreePhase:
    """
    Tests for the three-phase knowledge hub search implementation.
    
    The three-phase approach:
    1. Phase 1a: Count total accessible nodes (cached by Neo4j)
    2. Phase 1b: Get paginated node IDs with streaming (no collect() barrier)
    3. Phase 2: Hydrate full node structures for paginated IDs only
    """

    @pytest.mark.asyncio
    async def test_three_phase_basic_pagination(self, neo4j_provider: Neo4jProvider):
        """Test basic three-phase query execution with pagination."""
        # Mock Phase 1a: Count query returns 100 total nodes
        # Mock Phase 1b: Paginated IDs query returns 10 IDs
        # Mock Phase 2: Hydration query returns 10 full nodes
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"total": 100}],  # Phase 1a: count
                [{"paginated_ids": [f"id-{i}" for i in range(10)]}],  # Phase 1b: IDs
                [{"nodes": [
                    {
                        "id": f"id-{i}",
                        "name": f"Node {i}",
                        "nodeType": "record",
                        "createdAt": 1000 + i,
                        "updatedAt": 2000 + i,
                    }
                    for i in range(10)
                ]}],  # Phase 2: hydration
            ]
        )
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])
        neo4j_provider.get_user_permission_app_ids = AsyncMock(return_value=[])

        result = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1",
            user_key="user1",
            skip=0,
            limit=10,
            sort_field="name",
            sort_dir="ASC",
        )

        assert result["total"] == 100
        assert len(result["nodes"]) == 10
        assert result["nodes"][0]["id"] == "id-0"
        assert result["nodes"][0]["name"] == "Node 0"
        # Verify all three phases were called
        assert neo4j_provider.client.execute_query.call_count == 3

    @pytest.mark.asyncio
    async def test_three_phase_empty_count(self, neo4j_provider: Neo4jProvider):
        """Test early return when count is 0."""
        # Mock Phase 1a: Count query returns 0
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"total": 0}]
        )
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])
        neo4j_provider.get_user_permission_app_ids = AsyncMock(return_value=[])

        result = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1",
            user_key="user1",
            skip=0,
            limit=10,
            sort_field="name",
            sort_dir="ASC",
        )

        assert result["total"] == 0
        assert result["nodes"] == []
        # Only Phase 1a should be called
        assert neo4j_provider.client.execute_query.call_count == 1

    @pytest.mark.asyncio
    async def test_three_phase_empty_ids(self, neo4j_provider: Neo4jProvider):
        """Test when count > 0 but paginated IDs are empty (out of range page)."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"total": 50}],  # Phase 1a: count
                [{"paginated_ids": []}],  # Phase 1b: empty IDs (page out of range)
            ]
        )
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])
        neo4j_provider.get_user_permission_app_ids = AsyncMock(return_value=[])

        result = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1",
            user_key="user1",
            skip=1000,  # Page way beyond available data
            limit=10,
            sort_field="name",
            sort_dir="ASC",
        )

        assert result["total"] == 50
        assert result["nodes"] == []
        # Phase 1a and 1b should be called, but not Phase 2
        assert neo4j_provider.client.execute_query.call_count == 2

    @pytest.mark.asyncio
    async def test_three_phase_with_filters(self, neo4j_provider: Neo4jProvider):
        """Test three-phase query with filters applied."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"total": 5}],
                [{"paginated_ids": ["id-1", "id-2", "id-3"]}],
                [{"nodes": [
                    {
                        "id": "id-1",
                        "name": "Record 1",
                        "nodeType": "record",
                        "recordType": "document",
                        "indexingStatus": "COMPLETED",
                    },
                    {
                        "id": "id-2",
                        "name": "Record 2",
                        "nodeType": "record",
                        "recordType": "document",
                        "indexingStatus": "COMPLETED",
                    },
                    {
                        "id": "id-3",
                        "name": "Record 3",
                        "nodeType": "record",
                        "recordType": "document",
                        "indexingStatus": "COMPLETED",
                    },
                ]}],
            ]
        )
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])
        neo4j_provider.get_user_permission_app_ids = AsyncMock(return_value=[])

        result = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1",
            user_key="user1",
            skip=0,
            limit=10,
            sort_field="name",
            sort_dir="ASC",
            node_types=["record"],
            record_types=["document"],
            indexing_status=["COMPLETED"],
        )

        assert result["total"] == 5
        assert len(result["nodes"]) == 3
        assert all(node["recordType"] == "document" for node in result["nodes"])
        assert all(node["indexingStatus"] == "COMPLETED" for node in result["nodes"])

    @pytest.mark.asyncio
    async def test_three_phase_with_parent_scope(self, neo4j_provider: Neo4jProvider):
        """Test three-phase query with parent scoping."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"total": 20}],
                [{"paginated_ids": ["child-1", "child-2"]}],
                [{"nodes": [
                    {"id": "child-1", "name": "Child 1", "nodeType": "record"},
                    {"id": "child-2", "name": "Child 2", "nodeType": "record"},
                ]}],
            ]
        )
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])
        neo4j_provider.get_user_permission_app_ids = AsyncMock(return_value=[])

        result = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1",
            user_key="user1",
            skip=0,
            limit=10,
            sort_field="name",
            sort_dir="ASC",
            parent_id="parent-rg-1",
            parent_type="recordGroup",
        )

        assert result["total"] == 20
        assert len(result["nodes"]) == 2
        assert neo4j_provider.client.execute_query.call_count == 3

    @pytest.mark.asyncio
    async def test_three_phase_large_dataset_pagination(self, neo4j_provider: Neo4jProvider):
        """
        Test that large datasets with late-page pagination don't cause OOM.
        Simulates the scenario that was causing the original error.
        """
        # Simulate 4000 total nodes, page 6 (skip=250)
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"total": 4000}],  # Phase 1a: 4000 total
                [{"paginated_ids": [f"id-{i}" for i in range(250, 300)]}],  # Phase 1b: IDs 250-299
                [{"nodes": [
                    {"id": f"id-{i}", "name": f"Node {i}", "nodeType": "record"}
                    for i in range(250, 300)
                ]}],  # Phase 2: 50 nodes
            ]
        )
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])
        neo4j_provider.get_user_permission_app_ids = AsyncMock(return_value=[])

        result = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1",
            user_key="user1",
            skip=250,  # Page 6
            limit=50,
            sort_field="updatedAt",
            sort_dir="DESC",
        )

        assert result["total"] == 4000
        assert len(result["nodes"]) == 50
        assert result["nodes"][0]["id"] == "id-250"
        assert result["nodes"][-1]["id"] == "id-299"
        # Critical: All three phases executed successfully without OOM
        assert neo4j_provider.client.execute_query.call_count == 3

    @pytest.mark.asyncio
    async def test_three_phase_error_handling(self, neo4j_provider: Neo4jProvider):
        """Test error handling in three-phase execution."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=RuntimeError("Neo4j connection lost")
        )
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])

        result = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1",
            user_key="user1",
            skip=0,
            limit=10,
            sort_field="name",
            sort_dir="ASC",
        )

        # Should return empty result on error, not raise
        assert result["total"] == 0
        assert result["nodes"] == []

    @pytest.mark.asyncio
    async def test_three_phase_pagination_consistency(self, neo4j_provider: Neo4jProvider):
        """Test that pagination returns non-overlapping results across pages."""
        # Simulate fetching page 1 and page 2
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])

        # Page 1 (skip=0, limit=10)
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"total": 25}],
                [{"paginated_ids": [f"id-{i}" for i in range(0, 10)]}],
                [{"nodes": [{"id": f"id-{i}", "name": f"Node {i}"} for i in range(0, 10)]}],
            ]
        )
        page1 = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1", user_key="user1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC",
        )

        # Page 2 (skip=10, limit=10)
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"total": 25}],
                [{"paginated_ids": [f"id-{i}" for i in range(10, 20)]}],
                [{"nodes": [{"id": f"id-{i}", "name": f"Node {i}"} for i in range(10, 20)]}],
            ]
        )
        page2 = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1", user_key="user1", skip=10, limit=10,
            sort_field="name", sort_dir="ASC",
        )

        # Verify no overlapping IDs
        page1_ids = {node["id"] for node in page1["nodes"]}
        page2_ids = {node["id"] for node in page2["nodes"]}
        assert len(page1_ids.intersection(page2_ids)) == 0
        assert page1["total"] == page2["total"]  # Total should be consistent

    @pytest.mark.asyncio
    async def test_three_phase_with_search_query(self, neo4j_provider: Neo4jProvider):
        """Test three-phase query with search text filtering."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"total": 3}],
                [{"paginated_ids": ["match-1", "match-2", "match-3"]}],
                [{"nodes": [
                    {"id": "match-1", "name": "Technology Report", "nodeType": "record"},
                    {"id": "match-2", "name": "Tech Stack Guide", "nodeType": "record"},
                    {"id": "match-3", "name": "Technical Docs", "nodeType": "record"},
                ]}],
            ]
        )
        neo4j_provider.get_user_app_ids = AsyncMock(return_value=["app1"])
        neo4j_provider.get_user_permission_app_ids = AsyncMock(return_value=[])

        result = await neo4j_provider.get_knowledge_hub_search(
            org_id="org1",
            user_key="user1",
            skip=0,
            limit=10,
            sort_field="name",
            sort_dir="ASC",
            search_query="tech",
        )

        assert result["total"] == 3
        assert len(result["nodes"]) == 3
        assert all("tech" in node["name"].lower() for node in result["nodes"])


# ---------------------------------------------------------------------------
# get_filtered_connector_instances (Neo4j)
# ---------------------------------------------------------------------------


class TestNeo4jGetFilteredConnectorInstances:
    """Tests for Neo4jProvider.get_filtered_connector_instances."""

    @pytest.mark.asyncio
    async def test_basic_no_filters_returns_two_tuple(self, neo4j_provider: Neo4jProvider):
        """Returns (list[dict], int) — 2-tuple, not 3."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 3}],
            [
                {"doc": {"id": "c1", "name": "Conn1", "nodeType": "App"}},
                {"doc": {"id": "c2", "name": "Conn2", "nodeType": "App"}},
                {"doc": {"id": "c3", "name": "Conn3", "nodeType": "App"}},
            ],
        ])
        result = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        docs, total = result
        assert total == 3
        assert len(docs) == 3

    @pytest.mark.asyncio
    async def test_personal_scope_filter(self, neo4j_provider: Neo4jProvider):
        """personal scope appends scope and createdBy conditions."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 1}],
            [{"doc": {"id": "p1", "name": "Personal", "nodeType": "App"}}],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", scope="personal"
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_team_scope_admin_skips_accessible_ids_lookup(self, neo4j_provider: Neo4jProvider):
        """is_admin=True skips _get_user_accessible_team_app_ids entirely."""
        neo4j_provider._get_user_accessible_team_app_ids = AsyncMock(return_value=[])
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 5}],
            [{"doc": {"id": f"t{i}", "name": f"T{i}", "nodeType": "App"}} for i in range(5)],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="admin1", scope="team", is_admin=True
        )
        assert total == 5
        neo4j_provider._get_user_accessible_team_app_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_team_scope_non_admin_with_accessible_ids(self, neo4j_provider: Neo4jProvider):
        """Non-admin team scope pre-fetches accessible team IDs."""
        neo4j_provider._get_user_accessible_team_app_ids = AsyncMock(
            return_value=["id1", "id2"]
        )
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 2}],
            [
                {"doc": {"id": "id1", "name": "Team1", "nodeType": "App"}},
                {"doc": {"id": "id2", "name": "Team2", "nodeType": "App"}},
            ],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", scope="team", is_admin=False
        )
        assert total == 2
        assert len(docs) == 2
        neo4j_provider._get_user_accessible_team_app_ids.assert_awaited_once_with("user1", None)

    @pytest.mark.asyncio
    async def test_team_scope_non_admin_empty_ids_logs_warning(self, neo4j_provider: Neo4jProvider):
        """Warning is emitted when accessible team IDs list is empty for non-admin."""
        neo4j_provider._get_user_accessible_team_app_ids = AsyncMock(return_value=[])
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 0}],
            [],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user_no_edges", scope="team", is_admin=False
        )
        assert total == 0
        assert docs == []
        info_calls = neo4j_provider.logger.info.call_args_list
        assert any(
            len(call.args) >= 2 and "user_no_edges" in str(call.args[1])
            for call in info_calls
        )

    @pytest.mark.asyncio
    async def test_with_is_authenticated_filter_true(self, neo4j_provider: Neo4jProvider):
        """is_authenticated=True passes through to Cypher conditions."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 2}],
            [
                {"doc": {"id": "a1", "isAuthenticated": True, "nodeType": "App"}},
                {"doc": {"id": "a2", "isAuthenticated": True, "nodeType": "App"}},
            ],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", is_authenticated=True
        )
        assert total == 2
        assert neo4j_provider.client.execute_query.call_count == 2

    @pytest.mark.asyncio
    async def test_with_is_authenticated_filter_false(self, neo4j_provider: Neo4jProvider):
        """is_authenticated=False filters to unauthenticated connectors."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 1}],
            [{"doc": {"id": "unauth1", "isAuthenticated": False, "nodeType": "App"}}],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", is_authenticated=False
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_with_is_active_filter_true(self, neo4j_provider: Neo4jProvider):
        """is_active=True passes through to Cypher conditions."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 3}],
            [{"doc": {"id": f"a{i}", "isActive": True, "nodeType": "App"}} for i in range(3)],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", is_active=True
        )
        assert total == 3

    @pytest.mark.asyncio
    async def test_with_is_active_filter_false(self, neo4j_provider: Neo4jProvider):
        """is_active=False returns only inactive connectors."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 1}],
            [{"doc": {"id": "inactive1", "isActive": False, "nodeType": "App"}}],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", is_active=False
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_with_connector_type_filter(self, neo4j_provider: Neo4jProvider):
        """connector_type_filter narrows to a specific type."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 1}],
            [{"doc": {"id": "slack1", "type": "Slack", "nodeType": "App"}}],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", connector_type_filter="Slack"
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_with_search_filter(self, neo4j_provider: Neo4jProvider):
        """search param builds a regex condition on name/type/appGroup."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 1}],
            [{"doc": {"id": "gd1", "name": "Google Drive", "nodeType": "App"}}],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", search="google"
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_combined_filters(self, neo4j_provider: Neo4jProvider):
        """Multiple filters combined: is_authenticated + is_active + connector_type."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 1}],
            [{"doc": {"id": "c1", "type": "Jira", "isAuthenticated": True,
                      "isActive": True, "nodeType": "App"}}],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1",
            is_authenticated=True, is_active=True, connector_type_filter="Jira"
        )
        assert total == 1
        assert neo4j_provider.client.execute_query.call_count == 2

    @pytest.mark.asyncio
    async def test_exception_returns_empty_tuple(self, neo4j_provider: Neo4jProvider):
        """DB exception returns ([], 0) and logs the error."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("Neo4j down"))
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1"
        )
        assert docs == []
        assert total == 0
        neo4j_provider.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_count_result_returns_zero_total(self, neo4j_provider: Neo4jProvider):
        """If count query returns empty list, total defaults to 0."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [],   # empty count result
            [],   # empty main result
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1"
        )
        assert total == 0
        assert docs == []

    @pytest.mark.asyncio
    async def test_exclude_kb_with_kb_connector_type(self, neo4j_provider: Neo4jProvider):
        """exclude_kb=True with kb_connector_type appends type exclusion condition."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[
            [{"total": 4}],
            [{"doc": {"id": f"c{i}", "nodeType": "App"}} for i in range(4)],
        ])
        docs, total = await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1",
            exclude_kb=True, kb_connector_type="KnowledgeBase"
        )
        assert total == 4

    @pytest.mark.asyncio
    async def test_configured_and_agent_filters_go_into_both_the_count_and_the_page(self, neo4j_provider: Neo4jProvider) -> None:
        """The total and the page must both leave out unconfigured or agent-off connectors."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[[{"total": 0}], []])

        await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", is_configured=True, is_agent_active=True,
        )

        assert neo4j_provider.client.execute_query.await_count == 2
        for call in neo4j_provider.client.execute_query.await_args_list:
            query, params = call.args[0], call.kwargs["parameters"]
            assert "coalesce(doc.isConfigured, false) = $is_configured" in query
            assert "coalesce(doc.isAgentActive, false) = $is_agent_active" in query
            assert params["is_configured"] is True
            assert params["is_agent_active"] is True

    @pytest.mark.asyncio
    async def test_only_the_page_query_is_sorted_with_a_tie_break(self, neo4j_provider: Neo4jProvider) -> None:
        """Pages are a stable slice; the count needs no order."""
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[[{"total": 0}], []])

        await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", skip=20, limit=10,
        )

        count_call, page_call = neo4j_provider.client.execute_query.await_args_list
        assert "ORDER BY doc.createdAtTimestamp DESC, doc.id" not in count_call.args[0]
        assert "ORDER BY doc.createdAtTimestamp DESC, doc.id" in page_call.args[0]

    @pytest.mark.asyncio
    async def test_registered_types_limit_both_the_count_and_the_page(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[[{"total": 0}], []])

        await neo4j_provider.get_filtered_connector_instances(
            collection="App", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", allowed_connector_types=["Gmail", "Slack"],
        )

        for call in neo4j_provider.client.execute_query.await_args_list:
            assert "doc.type IN $allowed_connector_types" in call.args[0]
            assert call.kwargs["parameters"]["allowed_connector_types"] == ["Gmail", "Slack"]


# ---------------------------------------------------------------------------
# _get_user_accessible_team_app_ids (Neo4j)
# ---------------------------------------------------------------------------


class TestNeo4jGetUserAccessibleTeamAppIds:
    """Tests for Neo4jProvider._get_user_accessible_team_app_ids."""

    @pytest.mark.asyncio
    async def test_returns_ids_for_known_user(self, neo4j_provider: Neo4jProvider):
        """Happy path: returns list of app id strings."""
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"app_id": "app1"}, {"app_id": "app2"}]
        )
        result = await neo4j_provider._get_user_accessible_team_app_ids("user1")
        assert result == ["app1", "app2"]
        neo4j_provider.client.execute_query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_user_has_no_team_edges(self, neo4j_provider: Neo4jProvider):
        """User exists but has no team app edges — returns []."""
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        result = await neo4j_provider._get_user_accessible_team_app_ids("user_no_edges")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_query_returns_none(self, neo4j_provider: Neo4jProvider):
        """Falsy query result (None) treated as empty list."""
        neo4j_provider.client.execute_query = AsyncMock(return_value=None)
        result = await neo4j_provider._get_user_accessible_team_app_ids("user1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_propagates(self, neo4j_provider: Neo4jProvider):
        """DB errors propagate — they are NOT silently swallowed."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=RuntimeError("Connection refused")
        )
        with pytest.raises(RuntimeError, match="Connection refused"):
            await neo4j_provider._get_user_accessible_team_app_ids("user1")

    @pytest.mark.asyncio
    async def test_with_transaction_parameter(self, neo4j_provider: Neo4jProvider):
        """transaction kwarg is forwarded to client.execute_query."""
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"app_id": "app-txn"}]
        )
        result = await neo4j_provider._get_user_accessible_team_app_ids("user1", "txn-123")
        assert result == ["app-txn"]
        _, kwargs = neo4j_provider.client.execute_query.call_args
        assert kwargs.get("txn_id") == "txn-123"

    @pytest.mark.asyncio
    async def test_query_uses_userId_not_id(self, neo4j_provider: Neo4jProvider):
        """Cypher must look up the user by userId (MongoDB ID), not by id (graph key)."""
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider._get_user_accessible_team_app_ids("mongo-user-123")
        call_args = neo4j_provider.client.execute_query.call_args
        query = call_args[0][0] if call_args[0] else call_args.args[0]
        assert "userId" in query, "query must match on user.userId, not user.id"
        assert "{id:" not in query.replace(" ", ""), "query must not match on user.id"
# _validate_upload_context
# ---------------------------------------------------------------------------


class TestValidateUploadContext:
    """Tests for Neo4jProvider._validate_upload_context."""

    @pytest.mark.asyncio
    async def test_success_kb_root(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1", "userId": "u1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")

        result = await neo4j_provider._validate_upload_context("kb1", "u1", "org1")

        assert result["valid"] is True
        assert result["upload_target"] == "kb_root"
        assert result["user_role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_success_folder(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1", "userId": "u1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        neo4j_provider.get_and_validate_folder_in_kb = AsyncMock(
            return_value={"_key": "f1", "path": "/reports"}
        )

        result = await neo4j_provider._validate_upload_context(
            "kb1", "u1", "org1", parent_folder_id="f1"
        )

        assert result["valid"] is True
        assert result["upload_target"] == "folder"
        assert result["parent_folder"]["_key"] == "f1"

    @pytest.mark.asyncio
    async def test_a_folder_in_the_trash_is_refused_and_says_so(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1", "userId": "u1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        neo4j_provider.get_and_validate_folder_in_kb = AsyncMock(
            return_value={"_key": "f1", "recordName": "Reports", "isDeleted": True}
        )

        result = await neo4j_provider._validate_upload_context(
            "kb1", "u1", "org1", parent_folder_id="f1"
        )

        assert result == {
            "valid": False,
            "success": False,
            "code": 409,
            "reason": "'Reports' is in Recently deleted, so you can't upload files to it. "
            "Restore it first, or choose another folder.",
        }

    @pytest.mark.asyncio
    async def test_user_not_found(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await neo4j_provider._validate_upload_context("kb1", "ghost", "org1")

        assert result["valid"] is False
        assert result["code"] == 404
        assert "ghost" in result["reason"]

    @pytest.mark.asyncio
    async def test_malformed_user_record_returns_500(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"userId": "u1"}
        )

        result = await neo4j_provider._validate_upload_context("kb1", "u1", "org1")

        assert result["valid"] is False
        assert result["code"] == 500
        assert "malformed" in result["reason"]

    @pytest.mark.asyncio
    async def test_reader_role_rejected_with_role_in_message(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        neo4j_provider._fetch_kb_name = AsyncMock(return_value="My KB")

        result = await neo4j_provider._validate_upload_context("kb1", "u1", "org1")

        assert result["valid"] is False
        assert result["code"] == 403
        assert "READER" in result["reason"]
        assert "My KB" in result["reason"]
        assert "OWNER or WRITER" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_role_rejected_with_no_access_message(self, neo4j_provider: Neo4jProvider):
        """User with no KB role gets 404 to hide KB existence."""
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.kb_exists = AsyncMock(return_value=True)
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await neo4j_provider._validate_upload_context("kb1", "u1", "org1")

        assert result["valid"] is False
        assert result["code"] == 404
        assert "kb1" in result["reason"]

    @pytest.mark.asyncio
    async def test_folder_not_in_kb_includes_names_in_message(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        neo4j_provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
        neo4j_provider._fetch_kb_name = AsyncMock(return_value="Engineering Docs")
        neo4j_provider._fetch_record_name = AsyncMock(return_value="Wrong Folder")

        result = await neo4j_provider._validate_upload_context(
            "kb1", "u1", "org1", parent_folder_id="wrong-folder"
        )

        assert result["valid"] is False
        assert result["code"] == 404
        assert "wrong-folder" in result["reason"]
        assert "Engineering Docs" in result["reason"]
        assert "Wrong Folder" in result["reason"]

    @pytest.mark.asyncio
    async def test_folder_not_in_kb_falls_back_to_ids(self, neo4j_provider: Neo4jProvider):
        """When name lookups return None, the error still contains the raw IDs."""
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        neo4j_provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
        neo4j_provider._fetch_kb_name = AsyncMock(return_value=None)
        neo4j_provider._fetch_record_name = AsyncMock(return_value=None)

        result = await neo4j_provider._validate_upload_context(
            "kb1", "u1", "org1", parent_folder_id="wrong-folder"
        )

        assert result["valid"] is False
        assert result["code"] == 404
        assert "wrong-folder" in result["reason"]
        assert "kb1" in result["reason"]

    @pytest.mark.asyncio
    async def test_exception_returns_500(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            side_effect=RuntimeError("db timeout")
        )

        result = await neo4j_provider._validate_upload_context("kb1", "u1", "org1")

        assert result["valid"] is False
        assert result["code"] == 500


# ---------------------------------------------------------------------------
# validate_folder_for_upload (public interface method)
# ---------------------------------------------------------------------------


class TestValidateFolderForUpload:
    """Tests for Neo4jProvider.validate_folder_for_upload."""

    @pytest.mark.asyncio
    async def test_valid_folder_returns_valid(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        neo4j_provider.get_and_validate_folder_in_kb = AsyncMock(
            return_value={"_key": "f1", "path": "/docs"}
        )

        result = await neo4j_provider.validate_folder_for_upload(
            kb_id="kb1", folder_id="f1", user_id="u1", org_id="org1"
        )

        assert result["valid"] is True
        assert result["upload_target"] == "folder"
        neo4j_provider.get_and_validate_folder_in_kb.assert_awaited_once_with("kb1", "f1")

    @pytest.mark.asyncio
    async def test_folder_not_in_kb_returns_404_with_names(self, neo4j_provider: Neo4jProvider):
        """Folder belongs to a different KB: 404 with KB/folder names in reason."""
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        neo4j_provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
        neo4j_provider._fetch_kb_name = AsyncMock(return_value="Docs KB")
        neo4j_provider._fetch_record_name = AsyncMock(return_value="Foreign Folder")

        result = await neo4j_provider.validate_folder_for_upload(
            kb_id="kb1", folder_id="foreign-folder", user_id="u1", org_id="org1"
        )

        assert result["valid"] is False
        assert result["code"] == 404
        assert "foreign-folder" in result["reason"]
        assert "Docs KB" in result["reason"]
        assert "Foreign Folder" in result["reason"]

    @pytest.mark.asyncio
    async def test_nonexistent_folder_falls_back_to_ids(self, neo4j_provider: Neo4jProvider):
        """Completely unknown folder: 404 with bare IDs when name lookup returns None."""
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        neo4j_provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
        neo4j_provider._fetch_kb_name = AsyncMock(return_value=None)
        neo4j_provider._fetch_record_name = AsyncMock(return_value=None)

        result = await neo4j_provider.validate_folder_for_upload(
            kb_id="kb1", folder_id="ghost", user_id="u1", org_id="org1"
        )

        assert result["valid"] is False
        assert result["code"] == 404
        assert "ghost" in result["reason"]
        assert "kb1" in result["reason"]

    @pytest.mark.asyncio
    async def test_reader_role_returns_403_with_role_in_message(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        neo4j_provider._fetch_kb_name = AsyncMock(return_value=None)

        result = await neo4j_provider.validate_folder_for_upload(
            kb_id="kb1", folder_id="f1", user_id="u1", org_id="org1"
        )

        assert result["valid"] is False
        assert result["code"] == 403
        assert "READER" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_role_returns_403_without_role_none_text(self, neo4j_provider: Neo4jProvider):
        """User with no KB role gets 404 to hide KB existence."""
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.kb_exists = AsyncMock(return_value=True)
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await neo4j_provider.validate_folder_for_upload(
            kb_id="kb1", folder_id="f1", user_id="u1", org_id="org1"
        )

        assert result["valid"] is False
        assert result["code"] == 404
        assert "kb1" in result["reason"]

    @pytest.mark.asyncio
    async def test_user_not_found_returns_404(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await neo4j_provider.validate_folder_for_upload(
            kb_id="kb1", folder_id="f1", user_id="unknown", org_id="org1"
        )

        assert result["valid"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_writer_role_accepted(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "id": "uk1"}
        )
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        neo4j_provider.get_and_validate_folder_in_kb = AsyncMock(
            return_value={"_key": "f1", "path": "/shared"}
        )

        result = await neo4j_provider.validate_folder_for_upload(
            kb_id="kb1", folder_id="f1", user_id="u1", org_id="org1"
        )

        assert result["valid"] is True
        assert result["user_role"] == "WRITER"

    @pytest.mark.asyncio
    async def test_db_error_returns_500(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.get_user_by_user_id = AsyncMock(
            side_effect=Exception("neo4j unreachable")
        )

        result = await neo4j_provider.validate_folder_for_upload(
            kb_id="kb1", folder_id="f1", user_id="u1", org_id="org1"
        )

        assert result["valid"] is False
        assert result["code"] == 500


class TestCreateRecordsDuplicateName:
    """Regression tests for in-batch duplicate-name dedup in ``_create_records``.

    The dedup key is scoped by destination parent folder, so the same file name
    in DIFFERENT folders within a single upload batch must NOT be skipped, while a
    true same-folder repeat IS skipped. This mirrors the per-folder scoping the
    Arango provider gets for free by calling ``_create_files_batch`` once per
    folder; Neo4j processes the whole batch in one loop, so the folder dimension
    must live in the key. Guards against a regression where two files named e.g.
    ``README.md`` in different subfolders were silently dropped as duplicates.
    """

    @staticmethod
    def _file(key: str, name: str, path: str, mime: str = "text/markdown") -> dict:
        return {
            "filePath": path,
            "record": {"_key": key, "recordName": name},
            "fileRecord": {
                "_key": f"f-{key}",
                "name": name,
                "mimeType": mime,
                "isFile": True,
            },
        }

    @pytest.fixture
    def provider_no_db_conflict(
        self, neo4j_provider: Neo4jProvider
    ) -> Neo4jProvider:
        # Isolate the in-batch dedup: the DB has no existing conflicts (empty
        # pre-fetched set), and node/edge writes are stubbed.
        neo4j_provider._fetch_existing_file_names_in_parent = AsyncMock(
            return_value=set()
        )
        neo4j_provider.batch_upsert_nodes = AsyncMock()
        neo4j_provider.batch_create_edges = AsyncMock()
        return neo4j_provider


class TestBatchUpdateConnectorStatus:
    @pytest.mark.asyncio
    async def test_empty_keys_skips_query(self, neo4j_provider: Neo4jProvider):
        result = await neo4j_provider.batch_update_connector_status(
            collection="apps",
            connector_keys=[],
            is_active=False,
            is_agent_active=False,
        )

        assert result == 0
        neo4j_provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_returns_updated_count(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"updated_count": 2}]
        )

        result = await neo4j_provider.batch_update_connector_status(
            collection="apps",
            connector_keys=["c1", "c2"],
            is_active=False,
            is_agent_active=False,
        )

        assert result == 2
        neo4j_provider.client.execute_query.assert_awaited_once()
        call_args = neo4j_provider.client.execute_query.call_args
        query = call_args[0][0]
        call_kwargs = call_args.kwargs
        assert call_kwargs["parameters"] == {
            "connector_ids": ["c1", "c2"],
            "is_active": False,
            "is_agent_active": False,
        }
        assert "MATCH (app:App" in query
        assert "isAgentActive" in query
        assert call_kwargs["txn_id"] is None

    @pytest.mark.asyncio
    async def test_uses_label_from_collection(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"updated_count": 1}]
        )

        with patch.object(neo4j_provider, "_get_label", return_value="CustomLabel") as mock_get_label:
            await neo4j_provider.batch_update_connector_status(
                collection="apps",
                connector_keys=["c1"],
                is_active=False,
                is_agent_active=False,
            )

        query = neo4j_provider.client.execute_query.call_args[0][0]
        assert "MATCH (app:CustomLabel" in query
        mock_get_label.assert_called_once_with("apps")

    @pytest.mark.asyncio
    async def test_passes_transaction_id(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[{"updated_count": 1}]
        )

        await neo4j_provider.batch_update_connector_status(
            collection="apps",
            connector_keys=["c1"],
            is_active=True,
            is_agent_active=True,
            transaction="txn-123",
        )

        call_kwargs = neo4j_provider.client.execute_query.call_args.kwargs
        assert call_kwargs["txn_id"] == "txn-123"
        assert call_kwargs["parameters"]["is_active"] is True
        assert call_kwargs["parameters"]["is_agent_active"] is True

    @pytest.mark.asyncio
    async def test_exception_returns_zero(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=Exception("neo4j error")
        )

        result = await neo4j_provider.batch_update_connector_status(
            collection="apps",
            connector_keys=["c1"],
            is_active=False,
            is_agent_active=False,
        )

        assert result == 0
        neo4j_provider.logger.error.assert_called_once()


class TestListUserKnowledgeBases:
    @pytest.mark.asyncio
    async def test_success_returns_kbs_with_connector_id(
        self, neo4j_provider: Neo4jProvider
    ):
        kb_result = {
            "id": "kb1",
            "name": "KB1",
            "connectorId": "knowledgeBase_org1",
            "createdAtTimestamp": 1,
            "updatedAtTimestamp": 2,
            "createdBy": "u1",
            "userRole": "OWNER",
            "folders": [],
        }
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"result": kb_result}],
                [{"total": 1}],
                [{"permission": "OWNER"}],
            ]
        )

        kbs, total, filters = await neo4j_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10
        )

        assert len(kbs) == 1
        assert kbs[0]["connectorId"] == "knowledgeBase_org1"
        assert total == 1
        assert "permissions" in filters

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_query_includes_id_projection(
        self, neo4j_provider: Neo4jProvider
    ):
        """Verify KB query projects the id field (KB app UUID)"""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"result": {"id": "kb-uuid-1", "name": "KB1"}}],
                [{"total": 1}],
                [],
            ]
        )

        await neo4j_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10
        )

        main_query = neo4j_provider.client.execute_query.call_args_list[0][0][0]
        # With new KB architecture, id field is projected (not connectorId)
        assert "id: kb.id" in main_query

    @pytest.mark.asyncio
    async def test_query_excludes_hidden_kbs(self, neo4j_provider: Neo4jProvider):
        """Hidden KBs (e.g. a project's linked file collection) must never
        surface in the Collections listing."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[], [{"total": 0}], []]
        )

        await neo4j_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10
        )

        main_query = neo4j_provider.client.execute_query.call_args_list[0][0][0]
        count_query = neo4j_provider.client.execute_query.call_args_list[1][0][0]
        assert main_query.count("coalesce(kb.isHidden, false) = false") == 1
        assert main_query.count("coalesce(kb2.isHidden, false) = false") == 1
        assert count_query.count("coalesce(kb.isHidden, false) = false") == 1
        assert count_query.count("coalesce(kb2.isHidden, false) = false") == 1

    @pytest.mark.asyncio
    async def test_a_name_search_filters_each_branch_on_its_own_knowledge_base(
        self, neo4j_provider: Neo4jProvider
    ) -> None:
        """kb is null on the team branch for a team-only grant; searching kb.name there dropped it."""
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[], [{"total": 0}], []]
        )

        await neo4j_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10, search="roadmap"
        )

        for call in neo4j_provider.client.execute_query.call_args_list[:2]:
            query = call[0][0]
            assert query.count("toLower(kb.name) CONTAINS toLower($search_term)") == 1
            assert query.count("toLower(kb2.name) CONTAINS toLower($search_term)") == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=Exception("neo4j error")
        )

        kbs, total, filters = await neo4j_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10
        )

        assert kbs == []
        assert total == 0
        assert filters["permissions"] == []


class TestGetAppPermissionRoleCypher:
    def test_returns_string(self, neo4j_provider: Neo4jProvider):
        """Verify _get_app_permission_role_cypher returns a string"""
        rpm = {"OWNER": 4, "WRITER": 3, "READER": 2, "COMMENTER": 1}
        cypher = neo4j_provider._get_app_permission_role_cypher("node", "u", rpm)
        assert isinstance(cypher, str)

    def test_contains_team_kb_permission_path(self, neo4j_provider: Neo4jProvider):
        """Verify Cypher contains team KB sharing logic (user→team PERMISSION + team→app PERMISSION TEAM)"""
        rpm = {"OWNER": 4, "WRITER": 3, "READER": 2, "COMMENTER": 1}
        cypher = neo4j_provider._get_app_permission_role_cypher("node", "u", rpm)
        
        # Check for team KB pattern: user→team (PERMISSION USER) + team→app (PERMISSION TEAM)
        assert "PERMISSION {type: 'USER'}" in cypher
        assert "PERMISSION {type: 'TEAM'}" in cypher
        assert 'team:Teams' in cypher
        assert 'ut.role' in cypher or 'ut:PERMISSION' in cypher
        
        # Check for team KB role variable
        assert 'team_kb_role' in cypher
        
        # Check it's in the CASE priority chain
        assert 'WHEN team_kb_role IS NOT NULL THEN team_kb_role' in cypher


class TestBuildTimeRangeConditions:
    """Test Neo4jProvider._build_time_range_conditions static method."""

    @staticmethod
    def _call(time_range, parameters=None, record_var="r"):
        if parameters is None:
            parameters = {}
        clause = Neo4jProvider._build_time_range_conditions(time_range, parameters, record_var)
        return clause, parameters

    def test_returns_empty_when_no_time_range(self):
        clause, params = self._call(None)
        assert clause == ""
        assert params == {}

    def test_returns_empty_when_empty_dict(self):
        clause, params = self._call({})
        assert clause == ""
        assert params == {}

    def test_source_created_after(self):
        clause, params = self._call({"source_created_after_ms": 1000})
        assert "r.sourceCreatedAtTimestamp >= $sourceCreatedAfterMs" in clause
        assert params["sourceCreatedAfterMs"] == 1000

    def test_source_created_before(self):
        clause, params = self._call({"source_created_before_ms": 2000})
        assert "r.sourceCreatedAtTimestamp <= $sourceCreatedBeforeMs" in clause
        assert params["sourceCreatedBeforeMs"] == 2000

    def test_source_updated_after(self):
        clause, params = self._call({"source_updated_after_ms": 3000})
        assert "r.sourceLastModifiedTimestamp >= $sourceUpdatedAfterMs" in clause
        assert params["sourceUpdatedAfterMs"] == 3000

    def test_source_updated_before(self):
        clause, params = self._call({"source_updated_before_ms": 4000})
        assert "r.sourceLastModifiedTimestamp <= $sourceUpdatedBeforeMs" in clause
        assert params["sourceUpdatedBeforeMs"] == 4000

    def test_multiple_bounds_combined(self):
        clause, params = self._call({
            "source_created_after_ms": 1000,
            "source_updated_before_ms": 5000,
        })
        assert "r.sourceCreatedAtTimestamp >= $sourceCreatedAfterMs" in clause
        assert "r.sourceLastModifiedTimestamp <= $sourceUpdatedBeforeMs" in clause
        assert params["sourceCreatedAfterMs"] == 1000
        assert params["sourceUpdatedBeforeMs"] == 5000

    def test_all_four_keys(self):
        params = {}
        tr = {
            "source_created_after_ms": 100,
            "source_created_before_ms": 200,
            "source_updated_after_ms": 300,
            "source_updated_before_ms": 400,
        }
        clause, params = self._call(tr, params)
        assert params["sourceCreatedAfterMs"] == 100
        assert params["sourceCreatedBeforeMs"] == 200
        assert params["sourceUpdatedAfterMs"] == 300
        assert params["sourceUpdatedBeforeMs"] == 400
        lines = [line.strip() for line in clause.strip().split("\n")]
        assert len(lines) == 4

    def test_custom_record_var(self):
        clause, params = self._call({"source_created_after_ms": 1000}, record_var="record")
        assert "record.sourceCreatedAtTimestamp >= $sourceCreatedAfterMs" in clause

    def test_clause_starts_with_and(self):
        clause, _ = self._call({"source_created_after_ms": 1000})
        assert clause.lstrip().startswith("AND ")

    def test_each_condition_starts_with_and(self):
        clause, _ = self._call({
            "source_created_after_ms": 1000,
            "source_created_before_ms": 2000,
        })
        for line in clause.strip().split("\n"):
            assert line.strip().startswith("AND "), f"Condition must start with AND: {line}"

    def test_mutates_parameters_in_place(self):
        """Caller-supplied parameters dict must be updated in place, not replaced."""
        params = {"userId": "u1"}
        self._call({"source_created_after_ms": 1000}, params)
        assert params["userId"] == "u1"
        assert params["sourceCreatedAfterMs"] == 1000


class TestTimeRangeThreading:
    """Verify time_range is threaded through get_accessible_virtual_record_ids without warning."""

    @pytest.mark.asyncio
    async def test_time_range_no_longer_logs_warning(self, neo4j_provider: Neo4jProvider):
        """After implementation, time_range should be applied, not warned about."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1", "userId": "u1"})
        neo4j_provider.get_user_apps = AsyncMock(return_value=[])
        neo4j_provider.logger = MagicMock()

        await neo4j_provider.get_accessible_virtual_record_ids(
            user_id="u1",
            org_id="org1",
            time_range={"source_created_after_ms": 1000},
        )

        for call in neo4j_provider.logger.warning.call_args_list:
            assert "not yet supported" not in str(call), (
                "Warning about unsupported time_range should have been removed"
            )

    @pytest.mark.asyncio
    async def test_time_range_passed_to_kb_and_connector_dispatch(self, neo4j_provider: Neo4jProvider):
        """No filters scenario (Scenario 3) must forward time_range to both dispatch helpers."""
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "u1", "userId": "u1"}
        )
        neo4j_provider.get_user_apps = AsyncMock(
            return_value=[{"id": "conn-1", "type": "DRIVE"}]
        )
        neo4j_provider.logger = MagicMock()

        time_range = {"source_created_after_ms": 1000}

        with patch.object(
            neo4j_provider, "_get_virtual_ids_for_connector", new_callable=AsyncMock, return_value={}
        ) as mock_conn, patch.object(
            neo4j_provider, "_get_kb_virtual_ids", new_callable=AsyncMock, return_value={}
        ) as mock_kb:
            await neo4j_provider.get_accessible_virtual_record_ids(
                user_id="u1", org_id="org1", time_range=time_range,
            )

        mock_conn.assert_called_once()
        assert mock_conn.call_args.kwargs.get("time_range") == time_range
        mock_kb.assert_called_once()
        assert mock_kb.call_args.kwargs.get("time_range") == time_range
class TestDeleteRecordsRecursiveOrphanParentClear:
    @pytest.mark.asyncio
    async def test_clears_orphan_parent_when_cascade_children_false(
        self, neo4j_provider: Neo4jProvider
    ):
        inventory = {
            "valid_root_keys": ["epic-1"],
            "records_with_type": [{
                "record": {
                    "id": "epic-1",
                    "recordName": "Epic",
                    "externalRecordId": "jira-epic-99",
                    "connectorId": "conn-1",
                },
                "type_doc": {},
            }],
        }
        neo4j_provider.begin_transaction = AsyncMock(return_value="txn1")
        neo4j_provider.commit_transaction = AsyncMock()
        queries: list[str] = []
        params_list: list[dict] = []

        async def exec_query(query, parameters=None, txn_id=None):
            queries.append(query)
            params_list.append(parameters or {})
            if "AS inventory" in query:
                return [{"inventory": inventory}]
            return []

        neo4j_provider.client.execute_query = AsyncMock(side_effect=exec_query)

        result = await neo4j_provider.delete_records_recursive(
            ["epic-1"], "conn-1", cascade_children=False
        )

        assert result["success"] is True
        clear_indexes = [
            i for i, q in enumerate(queries)
            if "SET survivor.externalParentId = null" in q
        ]
        assert len(clear_indexes) == 1
        clear_q = queries[clear_indexes[0]]
        clear_params = params_list[clear_indexes[0]]
        assert "BELONGS_TO" in clear_q
        assert "RecordGroup" in clear_q
        assert clear_params["parent_external_ids"] == ["jira-epic-99"]
        assert clear_params["connector_id"] == "conn-1"
        assert clear_params["deleted_ids"] == ["epic-1"]

    @pytest.mark.asyncio
    async def test_skips_orphan_clear_when_cascade_children_true(
        self, neo4j_provider: Neo4jProvider
    ):
        inventory = {
            "valid_root_keys": ["epic-1"],
            "records_with_type": [{
                "record": {
                    "id": "epic-1",
                    "recordName": "Epic",
                    "externalRecordId": "jira-epic-99",
                },
                "type_doc": {},
            }],
        }
        neo4j_provider.begin_transaction = AsyncMock(return_value="txn1")
        neo4j_provider.commit_transaction = AsyncMock()

        async def exec_query(query, parameters=None, txn_id=None):
            if "AS inventory" in query:
                return [{"inventory": inventory}]
            return []

        neo4j_provider.client.execute_query = AsyncMock(side_effect=exec_query)

        await neo4j_provider.delete_records_recursive(
            ["epic-1"], "conn-1", cascade_children=True
        )

        for call in neo4j_provider.client.execute_query.await_args_list:
            query = call.args[0] if call.args else call.kwargs.get("query", "")
            assert "SET survivor.externalParentId = null" not in query

    @pytest.mark.asyncio
    async def test_skips_orphan_clear_when_root_has_no_external_record_id(
        self, neo4j_provider: Neo4jProvider
    ):
        inventory = {
            "valid_root_keys": ["epic-1"],
            "records_with_type": [{
                "record": {
                    "id": "epic-1",
                    "recordName": "Epic",
                },
                "type_doc": {},
            }],
        }
        neo4j_provider.begin_transaction = AsyncMock(return_value="txn1")
        neo4j_provider.commit_transaction = AsyncMock()

        async def exec_query(query, parameters=None, txn_id=None):
            if "AS inventory" in query:
                return [{"inventory": inventory}]
            return []

        neo4j_provider.client.execute_query = AsyncMock(side_effect=exec_query)

        await neo4j_provider.delete_records_recursive(
            ["epic-1"], "conn-1", cascade_children=False
        )

        for call in neo4j_provider.client.execute_query.await_args_list:
            query = call.args[0] if call.args else call.kwargs.get("query", "")
            assert "SET survivor.externalParentId = null" not in query


class TestDeleteSingleRecord:
    @pytest.mark.asyncio
    async def test_empty_id(self, neo4j_provider: Neo4jProvider):
        result = await neo4j_provider.delete_single_record("")
        assert result["success"] is True
        assert result["total_requested"] == 0
        assert result["eventData"] is None

    @pytest.mark.asyncio
    async def test_found_with_event(self, neo4j_provider: Neo4jProvider):
        inventory = {
            "valid_root_keys": ["r1"],
            "records_with_type": [{
                "record": {
                    "id": "r1",
                    "recordName": "doc.pdf",
                    "virtualRecordId": "virt-1",
                    "connectorName": "GITHUB",
                    "origin": "CONNECTOR",
                },
                "type_doc": {},
            }],
        }
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"inventory": inventory}])
        with patch.object(neo4j_provider, "begin_transaction", AsyncMock(return_value="txn1")), \
             patch.object(neo4j_provider, "commit_transaction", AsyncMock()), \
             patch.object(neo4j_provider, "_create_deleted_record_event_payload", AsyncMock(return_value={"recordId": "r1"})):
            result = await neo4j_provider.delete_single_record("r1")

        assert result["successfully_deleted"] == 1
        assert result["eventData"]["eventType"] == "deleteRecord"
        assert result["eventData"]["payloads"][0]["connectorName"] == "GITHUB"
        delete_queries = [c.args[0] for c in neo4j_provider.client.execute_query.await_args_list[1:]]
        assert any("DETACH DELETE t" in q for q in delete_queries)
        assert any("DETACH DELETE r" in q for q in delete_queries)

    @pytest.mark.asyncio
    async def test_missing_is_noop(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{
            "inventory": {"valid_root_keys": [], "records_with_type": []},
        }])
        with patch.object(neo4j_provider, "begin_transaction", AsyncMock(return_value="txn1")), \
             patch.object(neo4j_provider, "commit_transaction", AsyncMock()) as mock_commit:
            result = await neo4j_provider.delete_single_record("missing")

        mock_commit.assert_awaited_once()
        assert result["success"] is True
        assert result["successfully_deleted"] == 0
        assert result["eventData"] is None
        assert neo4j_provider.client.execute_query.await_count == 1

    @pytest.mark.asyncio
    async def test_no_virtual_record_id(self, neo4j_provider: Neo4jProvider):
        inventory = {
            "valid_root_keys": ["r1"],
            "records_with_type": [{
                "record": {"id": "r1", "recordName": "draft.txt", "connectorName": "GITHUB", "origin": "CONNECTOR"},
                "type_doc": {},
            }],
        }
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"inventory": inventory}])
        with patch.object(neo4j_provider, "begin_transaction", AsyncMock(return_value="txn1")), \
             patch.object(neo4j_provider, "commit_transaction", AsyncMock()):
            result = await neo4j_provider.delete_single_record("r1")

        assert result["successfully_deleted"] == 1
        assert result["eventData"] is None

    @pytest.mark.asyncio
    async def test_db_error_rolls_back(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("query failed"))
        with patch.object(neo4j_provider, "begin_transaction", AsyncMock(return_value="txn1")), \
             patch.object(neo4j_provider, "rollback_transaction", AsyncMock()) as mock_rb:
            result = await neo4j_provider.delete_single_record("r1")

        assert result["success"] is False
        assert result["code"] == 500
        mock_rb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_external_transaction_not_committed(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{
            "inventory": {"valid_root_keys": [], "records_with_type": []},
        }])
        with patch.object(neo4j_provider, "begin_transaction", AsyncMock()) as mock_begin, \
             patch.object(neo4j_provider, "commit_transaction", AsyncMock()) as mock_commit:
            await neo4j_provider.delete_single_record("r1", transaction="ext-txn")

        mock_begin.assert_not_awaited()
        mock_commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Team member listing must exclude removed (isActive = false) users
# ---------------------------------------------------------------------------


class TestTeamQueriesExcludeInactiveUsers:
    """Member listing must skip removed users (isActive = false) and must not
    emit a placeholder member when an OPTIONAL MATCH yields no active member."""

    @staticmethod
    def _member_query(neo4j_provider: Neo4jProvider) -> str:
        # get_user_teams issues a count query first; the member projection is
        # always in the last executed query for all three team methods.
        return neo4j_provider.client.execute_query.call_args_list[-1].args[0]

    @staticmethod
    def _assert_guarded(query: str) -> None:
        assert "member_user.isActive = true" in query
        assert "CASE WHEN member_user IS NULL THEN null ELSE" in query
        assert "END) AS" in query

    @pytest.mark.asyncio
    async def test_get_team_with_users_filters_inactive(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider.get_team_with_users("t1", "uk1")
        self._assert_guarded(self._member_query(neo4j_provider))

    @pytest.mark.asyncio
    async def test_get_user_teams_filters_inactive(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider.get_user_teams("uk1")
        calls = neo4j_provider.client.execute_query.call_args_list
        assert len(calls) == 2
        assert "isActive" not in calls[0].args[0]  # count query counts teams, not members
        self._assert_guarded(calls[1].args[0])

    @pytest.mark.asyncio
    async def test_get_team_users_filters_inactive(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider.get_team_users("t1", "org1", "uk1")
        self._assert_guarded(self._member_query(neo4j_provider))

class TestAppChildrenExternalHoisting:
    """Blocks 3 and 4 of _get_app_children_cypher -- surfacing records shared directly
    with an external collaborator whose container they cannot see."""

    @pytest.fixture
    def cypher(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        return object.__new__(Neo4jProvider)._get_app_children_cypher()

    def test_gated_on_is_external_user(self, cypher):
        """An unflagged user must fail the gate immediately -- an internal user can hold
        50k direct permission edges and must not pay to collect them as candidates."""
        assert "coalesce(uar.isExternalUser, false) AS is_external_user" in cypher
        assert cypher.count("WHERE is_external_user AND NOT is_kb_app") == 2

    def test_parent_probe_is_wrapped_in_an_aggregation(self, cypher):
        """_get_permission_role_cypher ends in LIMIT 1 and yields zero rows when there is
        no permission, and a zero-row CALL deletes the outer row. Testing for absence
        with it unwrapped drops exactly the candidates that should be hoisted -- the
        precise inversion of the feature. The collect() wrapper is what preserves the
        row, so this assertion is load-bearing."""
        assert cypher.count("RETURN collect(permission_role) AS visible_parent_records") == 1
        assert cypher.count("RETURN collect(permission_role) AS visible_parent_groups") == 2

    def test_hoists_only_when_no_parent_is_visible(self, cypher):
        """If any parent is visible the user reaches the node by drilling in, so hoisting
        it as well would show it twice."""
        assert (
            "WHERE size(visible_parent_records) = 0 AND size(visible_parent_groups) = 0"
            in cypher
        )

    def test_both_parent_directions_are_followed(self, cypher):
        """Parent folder: RECORD_RELATION backwards. Record group: BELONGS_TO forwards."""
        assert (
            "OPTIONAL MATCH (parent_rec:Record)-[:RECORD_RELATION {relationshipType: 'PARENT_CHILD'}]->(orphan_record)"
            in cypher
        )
        assert "OPTIONAL MATCH (orphan_record)-[:BELONGS_TO]->(parent_rg:RecordGroup)" in cypher

    def test_container_less_and_top_level_nodes_are_skipped(self, cypher):
        """Branch 2 already returns top-level record groups."""
        assert "WHERE size(parent_recs) > 0 OR size(parent_rgs) > 0" in cypher
        assert "WHERE size(parent_rgs) > 0" in cypher

    def test_candidates_include_group_role_team_grants(self, cypher):
        """Access via a group must count, or the reaper and this branch disagree about
        who is reachable."""
        assert "(principal:Group OR principal:Role OR principal:Teams)" in cypher
        assert "(rg_principal:Group OR rg_principal:Role OR rg_principal:Teams)" in cypher

    def test_org_wide_grants_are_excluded_from_candidates(self, cypher):
        """Org-level grants apply org-wide and would flood the view, so they must not
        *select* candidates. They still count when grading the user's role on a
        candidate, which is why this looks only at the collection step and not at the
        permission helper that follows it."""
        collection_step = cypher.split("hoist orphaned Records")[1].split("AS candidates")[0]
        assert "Organization" not in collection_step
        assert collection_step.count("OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->") == 2

    def test_results_are_merged_into_raw_children(self, cypher):
        assert "coalesce(hoisted_records, [])" in cypher
        assert "coalesce(hoisted_groups, [])" in cypher


class TestPersonMigrationAndReaper:
    """Phase 5 (Person -> User) and Phase 6 (reaping stale external membership)."""

    @pytest.fixture
    def provider(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        return object.__new__(Neo4jProvider)

    def test_reaper_matches_browse_candidates(self, provider):
        """The reaper's "still has a grant" test must recognise every access path browse
        hoists on.

        This is the invariant most likely to rot, and it fails silently in the worst
        direction: drop the group/role/team hop here and the reaper deletes membership
        for collaborators whose access is entirely real, so their shared records vanish
        from the tree with nothing in the logs.
        """
        reaper = provider._external_grant_exists_cypher("principal", "app")
        browse = provider._get_app_children_cypher()

        # Both hops present in the reaper.
        assert "-[:PERMISSION {type: 'USER'}]->(granted)" in reaper
        assert "-[:PERMISSION {type: 'USER'}]->(via)-[:PERMISSION]->(granted)" in reaper

        # Same principal kinds browse accepts for the indirect hop.
        for label in ("Group", "Role", "Teams"):
            assert f"via:{label}" in reaper, f"reaper ignores {label} grants"
            assert f":{label}" in browse

        # Both scope to the app, so a grant on another connector cannot keep membership
        # alive here.
        assert "granted.connectorId = app.id" in reaper

        # Soft-deleted grants must not keep membership alive — browse already drops them.
        assert "coalesce(granted.isDeleted, false) = false" in reaper
        assert "coalesce(orphan_record.isDeleted, false) = false" in browse
        assert "coalesce(orphan_group.isDeleted, false) = false" in browse

    # -- rendered-query assertions -------------------------------------------------
    #
    # These render the query the way the driver would see it and then execute the method
    # against a mocked client. Asserting on inspect.getsource() instead would pass on
    # `{crm_types}` -- the literal template text -- and so would keep passing if the
    # substitution broke.

    @pytest.fixture
    def mocked(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        p.client = AsyncMock()
        return p

    @staticmethod
    def _query_of(mock_client):
        return mock_client.execute_query.await_args.args[0]

    @staticmethod
    def _params_of(mock_client):
        return mock_client.execute_query.await_args.kwargs["parameters"]

    @pytest.mark.asyncio
    async def test_migration_reports_migrated_mode(self, mocked):
        from app.config.constants.arangodb import PersonMigrationMode

        mocked.client.execute_query.return_value = [
            {"mode": "migrated", "moved_permissions": 3, "moved_app_relations": 1}
        ]
        assert (
            await mocked.migrate_person_to_user("A@Corp.com", "user-1", "org-1")
            == PersonMigrationMode.MIGRATED
        )

    @pytest.mark.asyncio
    async def test_migration_reports_split_mode(self, mocked):
        from app.config.constants.arangodb import PersonMigrationMode

        mocked.client.execute_query.return_value = [
            {"mode": "split", "moved_permissions": 2, "moved_app_relations": 1}
        ]
        assert (
            await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1")
            == PersonMigrationMode.SPLIT
        )

    @pytest.mark.asyncio
    async def test_migration_returns_none_when_no_person_exists(self, mocked):
        """The ordinary case for anyone who was never an external collaborator. It must
        not look like a failure -- both call sites run on every new user."""
        mocked.client.execute_query.return_value = []
        assert await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1") is None

    @pytest.mark.asyncio
    async def test_migration_matches_person_on_lowercased_email(self, mocked):
        """Person.email is normalised at rest (Phase 1), so an exact match against a
        mixed-case address would silently find nothing and skip the migration."""
        mocked.client.execute_query.return_value = [{"mode": "migrated"}]
        await mocked.migrate_person_to_user("Mixed@Case.COM", "user-1", "org-1")
        assert self._params_of(mocked.client)["email"] == "mixed@case.com"

    @pytest.mark.asyncio
    async def test_migration_matches_person_on_org_id(self, mocked):
        """Person is org-scoped like User: matching on email alone would let this user
        adopt another org's Person node."""
        mocked.client.execute_query.return_value = [{"mode": "migrated"}]
        await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1")
        assert self._params_of(mocked.client)["org_id"] == "org-1"
        q = self._query_of(mocked.client)
        assert "{email: $email, orgId: $org_id}" in q

    @pytest.mark.asyncio
    async def test_migration_mode_literals_come_from_the_constants(self, mocked):
        """The CASE arms are bound as parameters rather than written into the Cypher, so
        the strings the query returns cannot drift from what callers compare against."""
        from app.config.constants.arangodb import PersonMigrationMode

        mocked.client.execute_query.return_value = [{"mode": "migrated"}]
        await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1")
        params = self._params_of(mocked.client)
        assert params["split"] == PersonMigrationMode.SPLIT
        assert params["migrated"] == PersonMigrationMode.MIGRATED

    @pytest.mark.asyncio
    async def test_migration_checks_crm_edges_in_both_directions(self, mocked):
        """memberOf points away from the Person; lead and contact point at it. A directed
        check would miss two of the three and delete a Salesforce contact outright."""
        mocked.client.execute_query.return_value = [{"mode": "migrated"}]
        await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1")
        q = self._query_of(mocked.client)
        assert "EXISTS { (p)-[:MEMBER_OF|LEAD|CONTACT]-() }" in q
        assert "(p)-[:MEMBER_OF|LEAD|CONTACT]->()" not in q

    @pytest.mark.asyncio
    async def test_migration_moves_exactly_the_transferable_edges(self, mocked):
        """CRM edges are what identify the Person as a contact. Moving one would make a
        split indistinguishable from a migration and strand the CRM history."""
        from app.config.constants.arangodb import (
            PERSON_CRM_EDGES,
            PERSON_TRANSFERABLE_EDGES,
        )
        from app.config.constants.neo4j import edge_collection_to_relationship

        mocked.client.execute_query.return_value = [{"mode": "migrated"}]
        await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1")
        q = self._query_of(mocked.client)

        for edge in PERSON_TRANSFERABLE_EDGES:
            rel = edge_collection_to_relationship(edge)
            assert f"MATCH (p)-[r:{rel}]->(target)" in q
            assert f"MERGE (u)-[moved:{rel}]->(target)" in q
        for edge in PERSON_CRM_EDGES:
            rel = edge_collection_to_relationship(edge)
            assert f"MERGE (u)-[moved:{rel}]->" not in q

    @pytest.mark.asyncio
    async def test_migration_preserves_existing_user_edges_on_collision(self, mocked):
        """MERGE matches any User->target edge of that type. Copying Person properties
        onto it would replace OWNER with READER and isExternalUser False with True.
        ON CREATE copies only when the User has no edge; the Person's copy is still
        deleted either way."""
        from app.config.constants.arangodb import PERSON_TRANSFERABLE_EDGES
        from app.config.constants.neo4j import edge_collection_to_relationship

        mocked.client.execute_query.return_value = [{"mode": "migrated"}]
        await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1")
        q = self._query_of(mocked.client)

        assert "SET moved += properties(r)" not in q
        for edge in PERSON_TRANSFERABLE_EDGES:
            rel = edge_collection_to_relationship(edge)
            assert (
                f"MERGE (u)-[moved:{rel}]->(target)\n"
                "                ON CREATE SET moved = properties(r)\n"
                "                DELETE r"
            ) in q

    @pytest.mark.asyncio
    async def test_migration_deletes_person_only_when_not_crm(self, mocked):
        mocked.client.execute_query.return_value = [{"mode": "migrated"}]
        await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1")
        q = self._query_of(mocked.client)
        assert "WITH p WHERE NOT is_crm" in q
        assert "DETACH DELETE p" in q
        # Aggregation, not a bare RETURN: a zero-row CALL deletes the outer row, so the
        # method would report None for a migration that actually happened.
        assert "RETURN count(*) AS deleted" in q

    @pytest.mark.asyncio
    async def test_migration_propagates_failure(self, mocked):
        """Unlike the reaper this raises, so the caller can decide -- both call sites
        catch it and continue, but silently returning None would report a migration that
        never happened."""
        mocked.client.execute_query.side_effect = RuntimeError("db down")
        with pytest.raises(RuntimeError):
            await mocked.migrate_person_to_user("a@corp.com", "user-1", "org-1")

    @pytest.mark.asyncio
    async def test_reaper_returns_reaped_count(self, mocked):
        mocked.client.execute_query.return_value = [{"reaped_people": 4}]
        assert await mocked.reap_stale_external_app_relations("conn-1") == 4

    @pytest.mark.asyncio
    async def test_reaper_swallows_failure(self, mocked):
        """Housekeeping must not fail a sync that indexed every record."""
        mocked.client.execute_query.side_effect = RuntimeError("db down")
        assert await mocked.reap_stale_external_app_relations("conn-1") == 0

    @pytest.mark.asyncio
    async def test_reaper_only_touches_flagged_edges(self, mocked):
        """A real app member's membership edge must never be a candidate."""
        mocked.client.execute_query.return_value = [{"reaped_people": 0}]
        await mocked.reap_stale_external_app_relations("conn-1")
        assert "uar.isExternalUser = true" in self._query_of(mocked.client)

    @pytest.mark.asyncio
    async def test_reaper_scopes_person_sweep_to_this_pass(self, mocked):
        """A global orphan sweep would race a Person created moments ago whose first edge
        is not committed yet."""
        mocked.client.execute_query.return_value = [{"reaped_people": 0}]
        await mocked.reap_stale_external_app_relations("conn-1")
        q = self._query_of(mocked.client)
        assert "UNWIND stale_principals AS principal" in q
        assert "MATCH (p:Person)" not in q


class TestEnsureAppMembership:
    """Phase 1 -- create-only membership edges, the primitive both the external-user flow
    and the browse gate depend on."""

    @pytest.fixture
    def mocked(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        p.client = AsyncMock()
        return p

    @pytest.mark.asyncio
    async def test_is_create_only(self, mocked):
        """Never downgrades an existing edge. batch_create_edges cannot be reused here:
        it does `SET r = edge.props`, which would clear a real member's flag on Neo4j
        while Arango's merge kept it -- the two backends would disagree."""
        from app.config.constants.arangodb import CollectionNames

        await mocked.ensure_app_membership(
            "user-1", CollectionNames.USERS.value, "conn-1", is_external=True
        )
        q = mocked.client.execute_query.await_args.args[0]
        assert "ON CREATE SET r = $props" in q
        assert "SET r +=" not in q


    @pytest.mark.asyncio
    @pytest.mark.parametrize("is_external", [True, False])
    async def test_flag_is_written_explicitly(self, mocked, is_external):
        from app.config.constants.arangodb import CollectionNames

        await mocked.ensure_app_membership(
            "user-1", CollectionNames.USERS.value, "conn-1", is_external=is_external
        )
        props = mocked.client.execute_query.await_args.kwargs["parameters"]["props"]
        assert props["isExternalUser"] is is_external

    @pytest.mark.asyncio
    async def test_person_principal_uses_person_label(self, mocked):
        """An external collaborator who is not yet a platform user holds membership as a
        Person, so the label has to follow the collection."""
        from app.config.constants.arangodb import CollectionNames

        await mocked.ensure_app_membership(
            "person-1", CollectionNames.PEOPLE.value, "conn-1", is_external=True
        )
        q = mocked.client.execute_query.await_args.args[0]
        assert "MATCH (principal:Person {id: $principal_id})" in q

    @pytest.mark.asyncio
    async def test_source_user_id_omitted_when_unknown(self, mocked):
        from app.config.constants.arangodb import CollectionNames

        await mocked.ensure_app_membership(
            "person-1", CollectionNames.PEOPLE.value, "conn-1", is_external=True
        )
        props = mocked.client.execute_query.await_args.kwargs["parameters"]["props"]
        assert "sourceUserId" not in props


class TestCreateEdgesIfAbsent:
    """The create-only counterpart of batch_create_edges, for a repair that must not
    replace an edge that is already there."""

    @pytest.fixture
    def mocked(self) -> "Neo4jProvider":
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        p.client = AsyncMock()
        return p

    @pytest.mark.asyncio
    async def test_is_create_only(self, mocked: "Neo4jProvider") -> None:
        edge = {"from_id": "u1", "from_collection": "users", "to_id": "a1", "to_collection": "apps",
                "role": "OWNER", "type": "USER"}
        await mocked.create_edges_if_absent([edge], "permission", transaction="txn-1")
        q = mocked.client.execute_query.await_args.args[0]
        assert "MERGE (from)-[r:PERMISSION]->(to)" in q
        assert "ON CREATE SET r = edge.props" in q
        assert q.count("SET r") == 1, "a bare SET would replace an existing edge"
        rows = mocked.client.execute_query.await_args.kwargs["parameters"]["edges"]
        assert rows == [{"from_key": "u1", "to_key": "a1", "props": {"role": "OWNER", "type": "USER"}}]
        assert mocked.client.execute_query.await_args.kwargs["txn_id"] == "txn-1"

    @pytest.mark.asyncio
    async def test_a_failed_write_is_raised(self, mocked: "Neo4jProvider") -> None:
        mocked.client.execute_query.side_effect = RuntimeError("lock timeout")
        edge = {"from_id": "u1", "from_collection": "users", "to_id": "a1", "to_collection": "apps"}
        with pytest.raises(RuntimeError, match="lock timeout"):
            await mocked.create_edges_if_absent([edge], "permission")

    @pytest.mark.asyncio
    async def test_nothing_to_write_is_no_statement(self, mocked: "Neo4jProvider") -> None:
        await mocked.create_edges_if_absent([], "permission")
        mocked.client.execute_query.assert_not_awaited()


class TestPersonUpsertByEmail:
    """Phase 1 -- email is the Person's business key under D1's uuid4 ids."""

    @pytest.fixture
    def mocked(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        p.client = AsyncMock()
        return p

    @pytest.mark.asyncio
    async def test_returns_surviving_id_not_the_local_uuid(self, mocked):
        """A concurrent sync may have created the node first. Callers must point edges at
        whatever won, or two Person nodes each end up holding half the permissions."""
        from app.models.entities import Person

        mocked.client.execute_query.return_value = [{"id": "winner-id"}]
        person = Person(email="out@x.io")
        assert await mocked.upsert_person_by_email(person) == "winner-id"
        assert person.id != "winner-id"

    @pytest.mark.asyncio
    async def test_merges_on_lowercased_email(self, mocked):
        from app.models.entities import Person

        mocked.client.execute_query.return_value = [{"id": "p1"}]
        await mocked.upsert_person_by_email(Person(email="Mixed@Case.COM"))
        params = mocked.client.execute_query.await_args.kwargs["parameters"]
        assert params["email"] == "mixed@case.com"
        assert params["props"]["email"] == "mixed@case.com"

    @pytest.mark.asyncio
    async def test_never_overwrites_an_existing_person(self, mocked):
        """A Person written by Salesforce carries real names and a phone number; a
        connector that knows only an email must not blank them."""
        from app.models.entities import Person

        mocked.client.execute_query.return_value = [{"id": "p1"}]
        await mocked.upsert_person_by_email(Person(email="out@x.io"))
        q = mocked.client.execute_query.await_args.args[0]
        assert "ON CREATE SET p = $props" in q
        assert "ON MATCH SET" not in q

    @pytest.mark.asyncio
    async def test_get_person_by_email_normalises(self, mocked):
        mocked.client.execute_query.return_value = []
        await mocked.get_person_by_email("Mixed@Case.COM", "org-1")
        params = mocked.client.execute_query.await_args.kwargs["parameters"]
        assert params["email"] == "mixed@case.com"
        assert params["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_upsert_merges_on_composite_org_and_email(self, mocked):
        """(orgId, email) is the business key, not email alone — the same email may
        legitimately exist once per org."""
        from app.models.entities import Person

        mocked.client.execute_query.return_value = [{"id": "p1"}]
        await mocked.upsert_person_by_email(Person(email="out@x.io", org_id="org-1"))
        params = mocked.client.execute_query.await_args.kwargs["parameters"]
        assert params["org_id"] == "org-1"
        assert params["props"]["orgId"] == "org-1"


class TestAppUserMembershipClearsExternalFlag:
    """Phase 1 -- the self-healing half of the external-user flag."""

    @pytest.fixture
    def mocked(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        p.client = AsyncMock()
        return p

    @pytest.mark.asyncio
    async def test_membership_edge_sets_flag_false_explicitly(self, mocked):
        """A collaborator first seen as external and later added to the workspace must be
        un-flagged, or browse keeps hoisting their records to app level forever.

        The value is written rather than omitted because the two backends disagree about
        omission: Neo4j's batch_create_edges does `SET r = props` (a replace) while
        Arango's does `UPDATE edge` (a merge), so only an explicit False clears the flag
        on both.
        """
        from app.config.constants.arangodb import CollectionNames, Connectors
        from app.models.entities import AppUser

        mocked.get_all_orgs = AsyncMock(return_value=[{"id": "org-1"}])
        mocked.get_document = AsyncMock(return_value={"id": "conn-1"})
        mocked.get_user_by_email = AsyncMock(return_value=MagicMock(id="u1"))
        mocked.batch_upsert_nodes = AsyncMock()
        mocked.migrate_person_to_user = AsyncMock(return_value=None)
        mocked.batch_create_edges = AsyncMock()

        await mocked.batch_upsert_app_users([
            AppUser(
                app_name=Connectors.GOOGLE_DRIVE,
                connector_id="conn-1",
                source_user_id="src-1",
                email="member@corp.com",
                full_name="Member",
            )
        ])

        app_edges = [
            c for c in mocked.batch_create_edges.await_args_list
            if c.kwargs.get("collection") == CollectionNames.USER_APP_RELATION.value
        ]
        assert app_edges, "no membership edge written"
        assert app_edges[0].args[0][0]["isExternalUser"] is False


class TestBreadcrumbVisibilityFilter:
    """Phase 8 -- breadcrumbs must not name ancestors the caller cannot open.

    Exercises the pure filter directly with a fixed leaf-first trail, so the cases are
    the graph shapes rather than Cypher/AQL text.
    """

    APP = {"id": "app1", "name": "Drive", "nodeType": "app", "subType": None}
    DRIVE = {"id": "rg1", "name": "Engineering", "nodeType": "recordGroup", "subType": None}
    FOLDER = {"id": "f1", "name": "Specs", "nodeType": "folder", "subType": None}
    LEAF = {"id": "r1", "name": "api.pdf", "nodeType": "record", "subType": "FILE"}

    @pytest.fixture
    def provider(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        p.client = AsyncMock()
        return p

    @staticmethod
    def _with_visibility(provider, visible_non_app, visible_apps):
        provider.filter_nodes_with_permission_role = AsyncMock(
            return_value=set(visible_non_app)
        )

        async def node_access(node_id, user_key, org_id, folder_mime_types, transaction=None):
            return {"id": node_id} if node_id in visible_apps else None

        provider.get_knowledge_hub_node_access = AsyncMock(side_effect=node_access)

    # trail is leaf-first; the walk reverses afterwards
    def _trail(self):
        return [dict(self.LEAF), dict(self.FOLDER), dict(self.DRIVE), dict(self.APP)]

    @pytest.mark.asyncio
    async def test_folder_granted_drive_not(self, provider):
        """Browse hoists Specs to app level and shows api.pdf inside it, so the trail is
        App > Specs > api.pdf."""
        self._with_visibility(provider, {"r1", "f1"}, {"app1"})
        out = await provider._filter_visible_breadcrumbs(self._trail(), "u1", "org1")
        assert [s["id"] for s in out] == ["r1", "f1", "app1"]

    @pytest.mark.asyncio
    async def test_only_the_file_granted(self, provider):
        """Browse hoists api.pdf itself, so the trail collapses to App > api.pdf."""
        self._with_visibility(provider, {"r1"}, {"app1"})
        out = await provider._filter_visible_breadcrumbs(self._trail(), "u1", "org1")
        assert [s["id"] for s in out] == ["r1", "app1"]

    @pytest.mark.asyncio
    async def test_drive_member_sees_everything(self, provider):
        self._with_visibility(provider, {"r1", "f1", "rg1"}, {"app1"})
        out = await provider._filter_visible_breadcrumbs(self._trail(), "u1", "org1")
        assert [s["id"] for s in out] == ["r1", "f1", "rg1", "app1"]

    @pytest.mark.asyncio
    async def test_drive_visible_folder_not(self, provider):
        """api.pdf is not hoisted here (its record group is visible), and browse shows it
        under Engineering -- the trail has to agree."""
        self._with_visibility(provider, {"r1", "rg1"}, {"app1"})
        out = await provider._filter_visible_breadcrumbs(self._trail(), "u1", "org1")
        assert [s["id"] for s in out] == ["r1", "rg1", "app1"]

    @pytest.mark.asyncio
    async def test_denied_middle_ancestor_keeps_the_leaf(self, provider):
        """The regression guard against 'simplifying' this into filter_accessible_prefix,
        which breaks at the first denied node and would drop the very node being viewed."""
        self._with_visibility(provider, {"r1", "rg1"}, {"app1"})
        out = await provider._filter_visible_breadcrumbs(self._trail(), "u1", "org1")
        assert out[0]["id"] == "r1", "leaf must survive a denied ancestor"

    @pytest.mark.asyncio
    async def test_invisible_leaf_returns_empty(self, provider):
        """Decision 1: no partial trail for a node the user cannot see at all."""
        self._with_visibility(provider, {"f1", "rg1"}, {"app1"})
        assert await provider._filter_visible_breadcrumbs(self._trail(), "u1", "org1") == []

    @pytest.mark.asyncio
    async def test_denied_app_is_dropped_rest_survives(self, provider):
        """Decision 2: the root App is checked like any other level."""
        self._with_visibility(provider, {"r1", "f1", "rg1"}, set())
        out = await provider._filter_visible_breadcrumbs(self._trail(), "u1", "org1")
        assert [s["id"] for s in out] == ["r1", "f1", "rg1"]

    @pytest.mark.asyncio
    async def test_apps_never_reach_the_batched_filter(self, provider):
        """filter_nodes_with_permission_role does not check Apps, and App access is
        USER_APP_RELATION-based -- grading one with the record model would be wrong."""
        self._with_visibility(provider, {"r1", "f1", "rg1"}, {"app1"})
        await provider._filter_visible_breadcrumbs(self._trail(), "u1", "org1")

        batched = provider.filter_nodes_with_permission_role.await_args.args[0]
        assert all(n["type"] != "app" for n in batched)
        # 'folder' is a record; the helper only understands record / recordGroup.
        assert {n["type"] for n in batched} == {"record", "recordGroup"}
        assert sorted(n["id"] for n in batched) == ["f1", "r1", "rg1"]

    @pytest.mark.asyncio
    async def test_empty_trail_short_circuits(self, provider):
        provider.filter_nodes_with_permission_role = AsyncMock()
        provider.get_knowledge_hub_node_access = AsyncMock()
        assert await provider._filter_visible_breadcrumbs([], "u1", "org1") == []
        provider.filter_nodes_with_permission_role.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_walk_result_is_filtered_and_reversed(self, provider):
        """End-to-end through the public method: the walk is untouched, the filter runs
        on its output, and the caller still gets root-first order."""
        pages = [
            [{"result": {"id": "r1", "name": "api.pdf", "nodeType": "record", "parentId": "f1"}}],
            [{"result": {"id": "f1", "name": "Specs", "nodeType": "folder", "parentId": "app1"}}],
            [{"result": {"id": "app1", "name": "Drive", "nodeType": "app", "parentId": None}}],
        ]
        provider.client.execute_query = AsyncMock(side_effect=pages)
        self._with_visibility(provider, {"r1"}, {"app1"})

        out = await provider.get_knowledge_hub_breadcrumbs("r1", "u1", "org1")
        assert [s["id"] for s in out] == ["app1", "r1"]


class TestCheckConnectorNameExistsExcludesSelf:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scope", ["personal", "team"])
    async def test_rename_excludes_the_connector_itself(self, neo4j_provider, scope) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.check_connector_name_exists(
            "apps", "Jira", scope, org_id="org-1", user_id="user-1", exclude_connector_id="c1"
        )

        query = neo4j_provider.client.execute_query.call_args.args[0]
        params = neo4j_provider.client.execute_query.call_args.kwargs["parameters"]
        assert "AND doc.id <> $exclude_id" in query
        assert params["exclude_id"] == "c1"

    @pytest.mark.asyncio
    async def test_create_has_no_exclusion(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.check_connector_name_exists("apps", "Jira", "team", org_id="org-1")

        assert "$exclude_id" not in neo4j_provider.client.execute_query.call_args.args[0]


class TestRecordLinksAreOneStatement:
    """With NEO4J_EXPLICIT_TRANSACTIONS off each statement commits on its own, so these must stay one."""

    @pytest.mark.asyncio
    async def test_replace_record_permissions(self, neo4j_provider: Neo4jProvider) -> None:
        edge = {"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records",
                "role": "READER"}

        await neo4j_provider.replace_record_permissions("r1", [edge], "g1", inherit=False, transaction="tx")

        neo4j_provider.client.execute_query.assert_awaited_once()
        call = neo4j_provider.client.execute_query.await_args
        query, parameters = call.args[0], call.kwargs["parameters"]
        assert query.index("DELETE old") < query.index("MERGE (from)-[r:PERMISSION]->(to)")
        assert query.index("MERGE (from)-[r:PERMISSION]->(to)") < query.index("-[link:INHERIT_PERMISSIONS]->")
        # Every record group, not only g1.
        assert "(:RecordGroup)\n                DELETE link" in query
        assert "$group_id" not in query
        assert (parameters["to_id"], parameters["record_id"]) == ("r1", "r1")
        assert parameters["edges_0"] == [{"from_key": "u1", "to_key": "r1", "props": {"role": "READER"}}]
        assert call.kwargs["txn_id"] == "tx"

    @pytest.mark.asyncio
    async def test_replace_record_permissions_without_a_group_leaves_inheritance_alone(
        self, neo4j_provider: Neo4jProvider
    ) -> None:
        await neo4j_provider.replace_record_permissions("r1", [], None, inherit=True)

        neo4j_provider.client.execute_query.assert_awaited_once()
        assert "INHERIT_PERMISSIONS" not in neo4j_provider.client.execute_query.await_args.args[0]

    @pytest.mark.asyncio
    async def test_replace_record_permissions_stops_inheriting_without_a_group(
        self, neo4j_provider: Neo4jProvider
    ) -> None:
        await neo4j_provider.replace_record_permissions("r1", [], None, inherit=False)

        neo4j_provider.client.execute_query.assert_awaited_once()
        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert "-[link:INHERIT_PERMISSIONS]->" in query
        assert "DELETE link" in query

    @pytest.mark.asyncio
    async def test_replace_record_permissions_starts_inheriting(self, neo4j_provider: Neo4jProvider) -> None:
        await neo4j_provider.replace_record_permissions("r1", [], "g1", inherit=True)

        call = neo4j_provider.client.execute_query.await_args
        assert "MERGE (record)-[link:INHERIT_PERMISSIONS]->(record_group)" in call.args[0]
        assert "DELETE link" not in call.args[0]
        assert call.kwargs["parameters"]["group_id"] == "g1"

    @pytest.mark.asyncio
    async def test_moving_a_record_between_groups(self, neo4j_provider: Neo4jProvider) -> None:
        await neo4j_provider.link_record_to_group(
            "r1", "new", inherit=True, leaving_group_id="old", transaction="tx"
        )

        neo4j_provider.client.execute_query.assert_awaited_once()
        call = neo4j_provider.client.execute_query.await_args
        query, parameters = call.args[0], call.kwargs["parameters"]
        assert "-[old:BELONGS_TO|INHERIT_PERMISSIONS]->" in query
        assert query.index("DELETE old") < query.index("MERGE (record)-[link:BELONGS_TO]->(record_group)")
        assert "MERGE (record)-[link:INHERIT_PERMISSIONS]->(record_group)" in query
        assert (parameters["record_id"], parameters["group_id"], parameters["leaving_group_id"]) == (
            "r1", "new", "old"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("inherit", "present", "absent"),
        [
            (None, "MERGE (record)-[link:BELONGS_TO]->(record_group)", "INHERIT_PERMISSIONS"),
            (False, "-[link:INHERIT_PERMISSIONS]->(:RecordGroup {id: $group_id})\n                DELETE link",
             "MERGE (record)-[link:INHERIT_PERMISSIONS]"),
        ],
    )
    async def test_joining_a_group(
        self, neo4j_provider: Neo4jProvider, inherit: bool | None, present: str, absent: str
    ) -> None:
        await neo4j_provider.link_record_to_group("r1", "g1", inherit=inherit)

        neo4j_provider.client.execute_query.assert_awaited_once()
        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert present in query
        assert absent not in query
        assert "DELETE old" not in query

    @pytest.mark.asyncio
    async def test_nothing_to_leave_or_join_runs_nothing(self, neo4j_provider: Neo4jProvider) -> None:
        await neo4j_provider.link_record_to_group("r1", None, inherit=True)

        neo4j_provider.client.execute_query.assert_not_awaited()


class TestValidateFolderInKb:
    """A folder in the trash is no place to put something; the caller can still ask about it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("call", "clause"),
        [
            ({}, "(folder.isDeleted IS NULL OR folder.isDeleted = false)"),
            ({"visibility": RecordVisibility.DELETED}, "folder.isDeleted = true"),
        ],
        ids=["live-by-default", "in-the-trash"],
    )
    async def test_the_folder_is_matched_by_visibility(
        self, neo4j_provider: Neo4jProvider, call: dict, clause: str
    ) -> None:
        neo4j_provider.client.execute_query.return_value = [{"count": 1}]

        assert await neo4j_provider.validate_folder_in_kb("kb1", "f1", **call) is True

        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert f"AND {clause}" in query

    @pytest.mark.asyncio
    async def test_exists_in_kb_still_counts_a_folder_in_the_trash(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query.return_value = [{"count": 1}]

        assert await neo4j_provider.validate_folder_exists_in_kb("kb1", "f1") is True

        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert "isDeleted" not in query

    @pytest.mark.asyncio
    async def test_no_match_or_a_failure_is_false(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query.return_value = [{"count": 0}]
        assert await neo4j_provider.validate_folder_in_kb("kb1", "f1") is False

        neo4j_provider.client.execute_query.side_effect = RuntimeError("down")
        assert await neo4j_provider.validate_folder_in_kb("kb1", "f1") is False


class TestRecordMoveIsOneStatement:
    """A move's record write and its parent edges are checked against each other by the path reads."""

    @staticmethod
    def _folder() -> FileRecord:
        return FileRecord(
            id="r1", org_id="org-1", record_name="Reports", record_type=RecordType.FILE,
            external_record_id="r1", version=0, origin=OriginTypes.UPLOAD,
            connector_name=Connectors.KNOWLEDGE_BASE, connector_id="kb-1", mime_type="application/vnd.folder",
            is_file=False, parent_external_record_id="folder-2",
        )

    @pytest.mark.asyncio
    async def test_under_another_parent(self, neo4j_provider: Neo4jProvider) -> None:
        await neo4j_provider.upsert_record_under_parent(self._folder(), "folder-2", transaction="tx")

        neo4j_provider.client.execute_query.assert_awaited_once()
        call = neo4j_provider.client.execute_query.await_args
        query, parameters = call.args[0], call.kwargs["parameters"]
        steps = [
            # Before any write: a parent that is gone, or in the trash, leaves no row to write for.
            "MATCH (parent:Record {id: $parent_id})",
            # Its write lock before its trash state is read, so a trash still being written is waited for.
            "SET parent.moveLock = true",
            "REMOVE parent.moveLock",
            "WHERE (parent.isDeleted IS NULL OR parent.isDeleted = false)",
            "trashedExternalRecordId: holder.externalRecordId",
            "MERGE (n:Record {id: node.id})",
            "MERGE (n)-[e:IS_OF_TYPE]->(t)",
            "OPTIONAL MATCH ()-[old:RECORD_RELATION {relationshipType: $parent_child}]->(n)",
            "DELETE old",
            "MERGE (parent)-[link:RECORD_RELATION]->(n)",
            "RETURN n.id",
        ]
        assert [query.index(step) for step in steps] == sorted(query.index(step) for step in steps)
        assert parameters["nodes"][0]["externalParentId"] == "folder-2"
        assert (parameters["parent_child"], parameters["parent_id"]) == ("PARENT_CHILD", "folder-2")
        assert parameters["parent_edge"]["relationshipType"] == "PARENT_CHILD"
        assert call.kwargs["txn_id"] == "tx"

    @pytest.mark.asyncio
    async def test_to_the_root(self, neo4j_provider: Neo4jProvider) -> None:
        record = self._folder()
        record.parent_external_record_id = None

        await neo4j_provider.upsert_record_under_parent(record, None)

        neo4j_provider.client.execute_query.assert_awaited_once()
        call = neo4j_provider.client.execute_query.await_args
        assert "DELETE old" in call.args[0]
        assert "(parent" not in call.args[0]
        assert "parent_id" not in call.kwargs["parameters"]

    @pytest.mark.asyncio
    async def test_a_parent_that_is_gone_is_raised(self, neo4j_provider: Neo4jProvider) -> None:
        """Its MATCH comes first, so the statement wrote nothing; answered quietly, the move looked done."""
        neo4j_provider.client.execute_query.return_value = []

        with pytest.raises(MoveDestinationMissing, match="its new parent folder-2 is not in the graph or is in the trash"):
            await neo4j_provider.upsert_record_under_parent(self._folder(), "folder-2")

    @pytest.mark.asyncio
    async def test_a_move_to_the_root_needs_no_parent(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query.return_value = []

        await neo4j_provider.upsert_record_under_parent(self._folder(), None)

    @pytest.mark.asyncio
    async def test_a_failure_is_raised(self, neo4j_provider: Neo4jProvider) -> None:
        neo4j_provider.client.execute_query.side_effect = RuntimeError("LockAcquisitionTimeout")

        with pytest.raises(RuntimeError, match="LockAcquisitionTimeout"):
            await neo4j_provider.upsert_record_under_parent(self._folder(), "folder-2")

    @pytest.mark.asyncio
    async def test_the_plain_upsert_touches_no_parent_edge(self, neo4j_provider: Neo4jProvider) -> None:
        await neo4j_provider.batch_upsert_records([self._folder()])

        neo4j_provider.client.execute_query.assert_awaited_once()
        query = neo4j_provider.client.execute_query.await_args.args[0]
        assert "MERGE (n:Record {id: node.id})" in query
        assert query.rstrip().endswith("RETURN n.id")
        assert "RECORD_RELATION" not in query
        assert "trashedExternalRecordId" not in query


class TestDeleteParentChildEdgeToRecord:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("rows", "deleted"), [([{"deleted": True}], True), ([{"deleted": False}], False), ([], False)])
    async def test_answers_whether_an_edge_was_deleted(
        self, neo4j_provider: Neo4jProvider, rows: list[dict], deleted: bool
    ) -> None:
        neo4j_provider.client.execute_query.return_value = rows

        assert await neo4j_provider.delete_parent_child_edge_to_record("r1", transaction="tx") is deleted

        call = neo4j_provider.client.execute_query.await_args
        assert call.kwargs == {"parameters": {"record_id": "r1"}, "txn_id": "tx"}

    @pytest.mark.asyncio
    async def test_a_failed_delete_is_raised(self, neo4j_provider: Neo4jProvider) -> None:
        """Answered False, the caller went on to write the new parent edge beside the old one."""
        neo4j_provider.client.execute_query.side_effect = RuntimeError("LockAcquisitionTimeout")

        with pytest.raises(RuntimeError, match="LockAcquisitionTimeout"):
            await neo4j_provider.delete_parent_child_edge_to_record("r1")


class TestPrincipalLookupsCanRaise:
    """None means "no such principal"; a caller that acts on that asks for a failed read to raise."""

    @staticmethod
    def _call(provider: Neo4jProvider, method: str, *, raise_on_error: bool):  # noqa: ANN205
        from app.models.entities import Person

        args = {
            "get_user_by_email": ("a@b.com",),
            "get_person_by_email": ("a@b.com", "org-1"),
            "upsert_person_by_email": (Person(email="a@b.com", org_id="org-1"),),
        }[method]
        return getattr(provider, method)(*args, raise_on_error=raise_on_error)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["get_user_by_email", "get_person_by_email", "upsert_person_by_email"])
    async def test_a_failed_read_raises_when_asked(self, neo4j_provider: Neo4jProvider, method: str) -> None:
        neo4j_provider.client.execute_query.side_effect = RuntimeError("connection lost")

        with pytest.raises(RuntimeError, match="connection lost"):
            await self._call(neo4j_provider, method, raise_on_error=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["get_user_by_email", "get_person_by_email", "upsert_person_by_email"])
    async def test_a_failed_read_is_none_by_default(self, neo4j_provider: Neo4jProvider, method: str) -> None:
        neo4j_provider.client.execute_query.side_effect = RuntimeError("connection lost")

        assert await self._call(neo4j_provider, method, raise_on_error=False) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["get_user_by_email", "get_person_by_email", "upsert_person_by_email"])
    async def test_nothing_found_is_none_even_when_asked_to_raise(
        self, neo4j_provider: Neo4jProvider, method: str
    ) -> None:
        neo4j_provider.client.execute_query.return_value = []

        assert await self._call(neo4j_provider, method, raise_on_error=True) is None
