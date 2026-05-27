from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.fixture
def neo4j_provider() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
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
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"agent": {"id": "agent-1", "name": "Agent One"}}],  # agent query
                [{"toolset": {"_key": "ts1", "name": "Toolset1", "tools": []}}],  # toolsets query
                [{"_key": "k1", "connectorId": "conn-1", "filters": "{}"}],  # knowledge query
                [{"share_with_org": True}],  # org share query
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            return_value={"_key": "agent-1", "name": "Agent One"}
        )
        neo4j_provider.get_document = AsyncMock(  # type: ignore[method-assign]
            return_value={"name": "Jira", "type": "APP"}
        )

        result = await neo4j_provider.get_agent("agent-1", org_id="org-1", transaction="txn-1")

        assert result is not None
        assert result["_key"] == "agent-1"
        assert result["toolsets"] == [{"_key": "ts1", "name": "Toolset1", "tools": []}]
        assert result["knowledge"][0]["name"] == "Jira"
        assert result["knowledge"][0]["type"] == "APP"
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
        assert result["shareWithOrg"] is False

    @pytest.mark.asyncio
    async def test_invalid_filters_json_falls_back_to_empty_dict(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"agent": {"id": "agent-1", "name": "Agent One"}}],  # agent query
                [],  # toolsets query
                [{"_key": "k1", "connectorId": "conn-1", "filters": "{not-json"}],  # knowledge query
                [],  # org share query
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            return_value={"_key": "agent-1", "name": "Agent One"}
        )
        neo4j_provider.get_document = AsyncMock(  # type: ignore[method-assign]
            return_value={"name": "Connector One", "type": "APP"}
        )

        result = await neo4j_provider.get_agent("agent-1")

        assert result is not None
        assert result["knowledge"][0]["filtersParsed"] == {}

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("boom"))

        result = await neo4j_provider.get_agent("agent-1")

        assert result is None


class TestCheckAgentPermission:
    @pytest.mark.asyncio
    async def test_returns_individual_access(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [],  # user query
                [{"role": "OWNER", "access_type": "INDIVIDUAL"}],  # individual query
            ]
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
                [],  # user query
                [],  # individual query
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
    async def test_returns_team_access_when_user_has_team(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"u": {"id": "user-1"}}],  # user query
                [{"team_id": "team-1"}],  # teams query
                [],  # individual query
                [],  # org query
                [{"role": "ORGANIZER", "access_type": "TEAM"}],  # team query
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            return_value={"userId": "resolved-user-id"}
        )

        result = await neo4j_provider.check_agent_permission("agent-1", "user-1", "org-1")

        assert result is not None
        assert result["access_type"] == "TEAM"
        assert result["user_role"] == "ORGANIZER"
        assert result["can_edit"] is True
        assert result["can_delete"] is False
        assert result["can_share"] is True

    @pytest.mark.asyncio
    async def test_returns_none_when_no_access(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [],  # user query
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
                [{"team_ids": []}],  # user teams
                [  # combined result
                    {
                        "agent": {"id": "a1", "name": "Agent 1", "updatedAtTimestamp": 10},
                        "role": "OWNER",
                        "access_type": "INDIVIDUAL",
                        "priority": 1,
                    }
                ],
                [{"org_shared_ids": ["a1"]}],  # org share query
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda data, _collection: {"_key": data["id"], "name": data["name"], "updatedAtTimestamp": data.get("updatedAtTimestamp")}
        )

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
                [{"team_ids": ["team-1"]}],
                [
                    {
                        "agent": {"id": "a1", "name": "Agent 1", "updatedAtTimestamp": 10},
                        "role": "READER",
                        "access_type": "ORG",
                        "priority": 3,
                    },
                    {
                        "agent": {"id": "a1", "name": "Agent 1", "updatedAtTimestamp": 10},
                        "role": "WRITER",
                        "access_type": "TEAM",
                        "priority": 2,
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
                [{"team_ids": []}],
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

        result = await neo4j_provider.get_all_agents("user-1", "org-1", page=2, limit=1)

        assert isinstance(result, dict)
        assert result["totalItems"] == 3
        assert len(result["agents"]) == 1
        assert result["agents"][0]["_key"] == "a2"

    @pytest.mark.asyncio
    async def test_search_is_normalized_and_passed_to_query(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"team_ids": []}],
                [],
            ]
        )

        result = await neo4j_provider.get_all_agents("user-1", "org-1", search="  AGENT  ")

        assert result == []
        # second query is combined_query with parameters containing q
        second_call_kwargs = neo4j_provider.client.execute_query.await_args_list[1].kwargs
        assert second_call_kwargs["parameters"]["q"] == "agent"

    @pytest.mark.asyncio
    async def test_org_share_defaults_false_when_no_agents(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"team_ids": []}],
                [],
            ]
        )

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


class TestShareAgent:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_permission(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value=None)  # type: ignore[method-assign]

        result = await neo4j_provider.share_agent("a1", "u1", "o1", ["u2"], ["t1"])

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_user_cannot_share(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_share": False}
        )

        result = await neo4j_provider.share_agent("a1", "u1", "o1", ["u2"], ["t1"])

        assert result is False

    @pytest.mark.asyncio
    async def test_shares_to_users_and_teams_successfully(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(  # type: ignore[method-assign]
            return_value={"can_share": True}
        )
        neo4j_provider.get_user_by_user_id = AsyncMock(  # type: ignore[method-assign]
            side_effect=[{"id": "u2-id"}, {"id": "u3-id"}]
        )
        neo4j_provider.get_document = AsyncMock(  # type: ignore[method-assign]
            side_effect=[{"id": "t1-id"}]
        )
        neo4j_provider.batch_create_edges = AsyncMock(return_value=True)  # type: ignore[method-assign]
        with patch("app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms", return_value=111):
            result = await neo4j_provider.share_agent("a1", "u1", "o1", ["u2", "u3"], ["t1"], transaction="txn-share")

        assert result is True
        assert neo4j_provider.batch_create_edges.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_false_when_user_edge_creation_fails(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"can_share": True})  # type: ignore[method-assign]
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u2-id"})  # type: ignore[method-assign]
        neo4j_provider.batch_create_edges = AsyncMock(return_value=False)  # type: ignore[method-assign]

        result = await neo4j_provider.share_agent("a1", "u1", "o1", ["u2"], None)

        assert result is False

    @pytest.mark.asyncio
    async def test_skips_missing_user_or_team_and_can_still_succeed(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"can_share": True})  # type: ignore[method-assign]
        neo4j_provider.get_user_by_user_id = AsyncMock(side_effect=[None, {"id": "u2-id"}])  # type: ignore[method-assign]
        neo4j_provider.get_document = AsyncMock(side_effect=[None])  # type: ignore[method-assign]
        neo4j_provider.batch_create_edges = AsyncMock(return_value=True)  # type: ignore[method-assign]
        with patch("app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms", return_value=222):
            result = await neo4j_provider.share_agent("a1", "u1", "o1", ["missing", "u2"], ["missing-team"])

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(side_effect=RuntimeError("perm fail"))  # type: ignore[method-assign]

        result = await neo4j_provider.share_agent("a1", "u1", "o1", ["u2"], ["t1"])

        assert result is False


class TestUnshareAgent:
    @pytest.mark.asyncio
    async def test_returns_permission_error_when_user_cannot_share(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"can_share": False})  # type: ignore[method-assign]

        result = await neo4j_provider.unshare_agent("a1", "u1", "o1", ["u2"], ["t1"])

        assert result == {"success": False, "reason": "Insufficient permissions to unshare agent"}

    @pytest.mark.asyncio
    async def test_unshares_users_and_teams_with_deleted_count(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"can_share": True})  # type: ignore[method-assign]
        neo4j_provider.get_user_by_user_id = AsyncMock(side_effect=[{"id": "u2-id"}, {"id": "u3-id"}])  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"deleted": 2}],  # user delete
                [{"deleted": 1}],  # team delete
            ]
        )

        result = await neo4j_provider.unshare_agent("a1", "u1", "o1", ["u2", "u3"], ["t1"], transaction="txn-u")

        assert result == {"success": True, "agent_id": "a1", "deleted_permissions": 3}

    @pytest.mark.asyncio
    async def test_handles_missing_users_and_empty_lists(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"can_share": True})  # type: ignore[method-assign]
        neo4j_provider.get_user_by_user_id = AsyncMock(side_effect=[None])  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"deleted": 0}])

        result = await neo4j_provider.unshare_agent("a1", "u1", "o1", ["missing-user"], None)

        assert result == {"success": True, "agent_id": "a1", "deleted_permissions": 0}

    @pytest.mark.asyncio
    async def test_returns_internal_error_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(side_effect=RuntimeError("perm boom"))  # type: ignore[method-assign]

        result = await neo4j_provider.unshare_agent("a1", "u1", "o1", ["u2"], ["t1"])

        assert result is not None
        assert result["success"] is False
        assert "Internal error" in result["reason"]


class TestUpdateAgentPermission:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_permission(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value=None)  # type: ignore[method-assign]

        result = await neo4j_provider.update_agent_permission("a1", "u1", "o1", ["u2"], ["t1"], "WRITER")

        assert result == {"success": False, "reason": "Agent not found or no permission"}

    @pytest.mark.asyncio
    async def test_returns_error_when_requester_not_owner(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"user_role": "WRITER"})  # type: ignore[method-assign]

        result = await neo4j_provider.update_agent_permission("a1", "u1", "o1", ["u2"], ["t1"], "WRITER")

        assert result == {"success": False, "reason": "Only OWNER can update permissions"}

    @pytest.mark.asyncio
    async def test_updates_users_and_teams_successfully(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"user_role": "OWNER"})  # type: ignore[method-assign]
        neo4j_provider.get_user_by_user_id = AsyncMock(  # type: ignore[method-assign]
            side_effect=[{"id": "u2-id"}, {"id": "u3-id"}]
        )
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"updated": 2}],  # user update
                [{"updated": 1}],  # team update
            ]
        )
        with patch("app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms", return_value=333):
            result = await neo4j_provider.update_agent_permission(
                "a1", "u1", "o1", ["u2", "u3"], ["t1"], "WRITER", transaction="txn-up"
            )

        assert result == {
            "success": True,
            "agent_id": "a1",
            "new_role": "WRITER",
            "updated_permissions": 3,
            "updated_users": 2,
            "updated_teams": 1,
        }

    @pytest.mark.asyncio
    async def test_returns_error_when_no_edges_updated(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"user_role": "OWNER"})  # type: ignore[method-assign]
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(return_value=[{"updated": 0}])

        result = await neo4j_provider.update_agent_permission("a1", "u1", "o1", ["missing"], ["t1"], "WRITER")

        assert result == {"success": False, "reason": "No permissions found to update"}

    @pytest.mark.asyncio
    async def test_returns_internal_error_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(side_effect=RuntimeError("perm fail"))  # type: ignore[method-assign]

        result = await neo4j_provider.update_agent_permission("a1", "u1", "o1", ["u2"], ["t1"], "WRITER")

        assert result is not None
        assert result["success"] is False
        assert "Internal error" in result["reason"]


class TestGetAgentPermissions:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_permission(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value=None)  # type: ignore[method-assign]

        result = await neo4j_provider.get_agent_permissions("a1", "u1", "o1")

        assert result is None


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
    async def test_get_record_by_external_id_none_and_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.get_record_by_external_id("conn-1", "ext-1") is None

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("ext fail"))
        assert await neo4j_provider.get_record_by_external_id("conn-1", "ext-1") is None

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
    async def test_get_record_by_path_returns_none_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("path fail"))
        assert await neo4j_provider.get_record_by_path("conn-1", ["root"], "rg-1") is None

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
    async def test_get_records_by_status_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("status2 fail"))

        result = await neo4j_provider.get_records_by_status("org-1", "conn-1", ["FAILED"])

        assert result == []

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
    async def test_get_records_by_parent_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("parent fail"))

        result = await neo4j_provider.get_records_by_parent("conn-1", "parent-ext-1")

        assert result == []

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

        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("apps fail"))
        assert await neo4j_provider.get_org_apps("org-1") == []

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
    async def test_returns_none_when_requester_not_owner(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"user_role": "WRITER"})  # type: ignore[method-assign]

        result = await neo4j_provider.get_agent_permissions("a1", "u1", "o1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_permissions_list_for_owner(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"user_role": "OWNER"})  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"permission": {"id": "u2", "role": "READER", "type": "USER"}},
                {"permission": {"id": "t1", "role": "WRITER", "type": "TEAM"}},
            ]
        )

        result = await neo4j_provider.get_agent_permissions("a1", "u1", "o1", transaction="txn-gp")

        assert result == [
            {"id": "u2", "role": "READER", "type": "USER"},
            {"id": "t1", "role": "WRITER", "type": "TEAM"},
        ]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_query_returns_no_rows(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"user_role": "OWNER"})  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        result = await neo4j_provider.get_agent_permissions("a1", "u1", "o1")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.check_agent_permission = AsyncMock(return_value={"user_role": "OWNER"})  # type: ignore[method-assign]
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await neo4j_provider.get_agent_permissions("a1", "u1", "o1")

        assert result is None

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
                [{"team_ids": []}],
                [
                    {"agent": {"id": "a1", "name": "A", "updatedAtTimestamp": 1}, "role": "OWNER", "access_type": "INDIVIDUAL", "priority": 1}
                ],
                [{"org_shared_ids": []}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda data, _collection: {"_key": data["id"], "name": data["name"], "updatedAtTimestamp": data.get("updatedAtTimestamp")}
        )

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
    async def test_dedupes_rows_by_agent_key(self, neo4j_provider: Neo4jProvider):
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
    async def test_skips_rows_without_key(self, neo4j_provider: Neo4jProvider):
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
    async def test_returns_empty_on_query_error(self, neo4j_provider: Neo4jProvider):
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
            record_type="FILE",
            size_in_bytes=42,
            transaction="txn-dup",
        )

        assert result == [{"_key": "rec-2", "id": "rec-2"}]
        kwargs = neo4j_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {
            "md5_checksum": "md5-1",
            "record_key": "rec-1",
            "record_type": "FILE",
            "size_in_bytes": 42,
        }
        assert kwargs["txn_id"] == "txn-dup"

    @pytest.mark.asyncio
    async def test_find_duplicate_records_returns_empty_on_exception(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("dup fail"))
        assert await neo4j_provider.find_duplicate_records("rec-1", "md5-1") == []

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
                [{"record": {"id": "rec-1", "md5Checksum": "m1", "sizeInBytes": 10}}],
                [{"record": {"id": "rec-2"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rec-2"})  # type: ignore[method-assign]

        found = await neo4j_provider.find_next_queued_duplicate("rec-1", transaction="txn-q")

        assert found == {"_key": "rec-2"}
        second_call = neo4j_provider.client.execute_query.await_args_list[1].kwargs
        assert second_call["parameters"]["queued_status"] == "QUEUED"
        assert second_call["parameters"]["size_in_bytes"] == 10
        assert second_call["txn_id"] == "txn-q"

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_returns_zero_on_missing_reference(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        assert await neo4j_provider.update_queued_duplicates_status("rec-1", "COMPLETED") == 0

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_updates_records_and_maps_completed_status(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"record": {"id": "rec-1", "md5Checksum": "m1", "sizeInBytes": 12}}],
                [{"record": {"id": "rec-2"}}, {"record": {"id": "rec-3"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=[{"_key": "rec-2"}, {"_key": "rec-3"}]
        )
        neo4j_provider.batch_upsert_nodes = AsyncMock()  # type: ignore[method-assign]

        with patch("app.services.graph_db.neo4j.neo4j_provider.get_epoch_timestamp_in_ms", return_value=101):
            updated_count = await neo4j_provider.update_queued_duplicates_status(
                "rec-1",
                "COMPLETED",
                virtual_record_id="v-1",
                transaction="txn-upd",
            )

        assert updated_count == 2
        payload = neo4j_provider.batch_upsert_nodes.await_args.args[0]
        assert payload[0]["indexingStatus"] == "COMPLETED"
        assert payload[0]["extractionStatus"] == "COMPLETED"
        assert payload[0]["virtualRecordId"] == "v-1"
        assert payload[1]["lastIndexTimestamp"] == 101
        assert neo4j_provider.batch_upsert_nodes.await_args.args[1] == "records"
        assert neo4j_provider.batch_upsert_nodes.await_args.args[2] == "txn-upd"

    @pytest.mark.asyncio
    async def test_update_queued_duplicates_status_maps_failed_and_empty(self, neo4j_provider: Neo4jProvider):
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[
                [{"record": {"id": "rec-1", "md5Checksum": "m1"}}],
                [{"record": {"id": "rec-2"}}],
                [{"record": {"id": "rec-1", "md5Checksum": "m1"}}],
                [{"record": {"id": "rec-2"}}],
            ]
        )
        neo4j_provider._neo4j_to_arango_node = MagicMock(return_value={"_key": "rec-2"})  # type: ignore[method-assign]
        neo4j_provider.batch_upsert_nodes = AsyncMock()  # type: ignore[method-assign]

        await neo4j_provider.update_queued_duplicates_status("rec-1", "FAILED")
        failed_payload = neo4j_provider.batch_upsert_nodes.await_args_list[0].args[0]
        assert failed_payload[0]["extractionStatus"] == "FAILED"

        await neo4j_provider.update_queued_duplicates_status("rec-1", "EMPTY")
        empty_payload = neo4j_provider.batch_upsert_nodes.await_args_list[1].args[0]
        assert empty_payload[0]["extractionStatus"] == "EMPTY"

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
        neo4j_provider._get_user_app_ids = AsyncMock(  # type: ignore[method-assign]
            return_value=["conn-1", "knowledgeBase_auto", "conn-2"]
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
        neo4j_provider._get_user_app_ids = AsyncMock(  # type: ignore[method-assign]
            return_value=["conn-1", "knowledgeBase_sys"]
        )
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock(return_value={"v1": "r1"})  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock()  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1",
            "org-1",
            filters={"apps": ["conn-1"]},
        )

        assert result == {"v1": "r1"}
        neo4j_provider._get_virtual_ids_for_connector.assert_awaited_once()
        neo4j_provider._get_kb_virtual_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_no_tasks_returns_empty(
        self, neo4j_provider: Neo4jProvider
    ):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})  # type: ignore[method-assign]
        neo4j_provider._get_user_app_ids = AsyncMock(return_value=["conn-1"])  # type: ignore[method-assign]
        neo4j_provider._get_virtual_ids_for_connector = AsyncMock()  # type: ignore[method-assign]
        neo4j_provider._get_kb_virtual_ids = AsyncMock()  # type: ignore[method-assign]

        result = await neo4j_provider.get_accessible_virtual_record_ids(
            "user-1",
            "org-1",
            filters={"apps": ["not-accessible"]},
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
