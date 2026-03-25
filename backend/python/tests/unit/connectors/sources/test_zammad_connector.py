"""Tests for Zammad connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.zammad.connector import (
    ZAMMAD_LINK_OBJECT_MAP,
    ZAMMAD_LINK_TYPE_MAP,
    ZammadConnector,
)
from app.config.constants.arangodb import RecordRelations
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.zammad")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-zm-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "authType": "API_TOKEN",
            "baseUrl": "https://zammad.example.com",
            "token": "test-zammad-token",
        },
    })
    return svc


@pytest.fixture()
def zammad_connector(mock_logger, mock_data_entities_processor,
                     mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        connector = ZammadConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="zm-conn-1",
        )
    return connector


# ===========================================================================
# Constants
# ===========================================================================

class TestZammadConstants:
    def test_link_type_map_has_expected_keys(self):
        assert "normal" in ZAMMAD_LINK_TYPE_MAP
        assert "parent" in ZAMMAD_LINK_TYPE_MAP
        assert "child" in ZAMMAD_LINK_TYPE_MAP

    def test_link_type_normal(self):
        assert ZAMMAD_LINK_TYPE_MAP["normal"] == RecordRelations.RELATED

    def test_link_type_parent(self):
        assert ZAMMAD_LINK_TYPE_MAP["parent"] == RecordRelations.DEPENDS_ON

    def test_link_type_child(self):
        assert ZAMMAD_LINK_TYPE_MAP["child"] == RecordRelations.LINKED_TO

    def test_link_object_map_ticket(self):
        assert ZAMMAD_LINK_OBJECT_MAP["Ticket"] == RecordType.TICKET

    def test_link_object_map_kb_answer(self):
        assert ZAMMAD_LINK_OBJECT_MAP["KnowledgeBase::Answer::Translation"] == RecordType.WEBPAGE


# ===========================================================================
# ZammadConnector
# ===========================================================================

class TestZammadConnectorInit:
    def test_constructor(self, zammad_connector):
        assert zammad_connector.connector_id == "zm-conn-1"
        assert zammad_connector.data_source is None
        assert zammad_connector.external_client is None
        assert zammad_connector._state_map == {}
        assert zammad_connector._priority_map == {}

    @patch("app.connectors.sources.zammad.connector.ZammadClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.zammad.connector.ZammadDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, zammad_connector):
        mock_client = MagicMock()
        mock_client.get_base_url.return_value = "https://zammad.example.com"
        mock_build.return_value = mock_client

        mock_ds = MagicMock()
        # Mock successful state/priority loading
        states_resp = MagicMock()
        states_resp.success = True
        states_resp.data = [{"id": 1, "name": "open"}, {"id": 2, "name": "closed"}]
        mock_ds.list_ticket_states = AsyncMock(return_value=states_resp)

        priorities_resp = MagicMock()
        priorities_resp.success = True
        priorities_resp.data = [{"id": 1, "name": "low"}, {"id": 2, "name": "high"}]
        mock_ds.list_ticket_priorities = AsyncMock(return_value=priorities_resp)

        mock_ds_cls.return_value = mock_ds

        result = await zammad_connector.init()
        assert result is True
        assert zammad_connector.base_url == "https://zammad.example.com"
        assert zammad_connector._state_map == {1: "open", 2: "closed"}
        assert zammad_connector._priority_map == {1: "low", 2: "high"}

    @patch("app.connectors.sources.zammad.connector.ZammadClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_exception(self, mock_build, zammad_connector):
        mock_build.side_effect = Exception("Auth failed")
        result = await zammad_connector.init()
        assert result is False


class TestZammadGetFreshDatasource:
    async def test_raises_if_not_initialized(self, zammad_connector):
        zammad_connector.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await zammad_connector._get_fresh_datasource()

    async def test_raises_if_no_config(self, zammad_connector):
        zammad_connector.external_client = MagicMock()
        zammad_connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await zammad_connector._get_fresh_datasource()

    async def test_raises_if_no_token(self, zammad_connector):
        zammad_connector.external_client = MagicMock()
        zammad_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "token": ""}
        })
        with pytest.raises(Exception, match="No access token"):
            await zammad_connector._get_fresh_datasource()

    async def test_returns_existing_datasource_if_token_unchanged(self, zammad_connector):
        mock_client = MagicMock()
        internal = MagicMock()
        internal.token = "test-zammad-token"
        mock_client.get_client.return_value = internal
        zammad_connector.external_client = mock_client
        zammad_connector.data_source = MagicMock()

        result = await zammad_connector._get_fresh_datasource()
        assert result is zammad_connector.data_source


class TestZammadGroupFilter:
    def test_no_filters_allows_all(self, zammad_connector):
        zammad_connector.sync_filters = None
        assert zammad_connector._is_group_allowed_by_filter("1") is True

    def test_empty_filter_allows_all(self, zammad_connector):
        mock_filters = MagicMock()
        mock_filters.get.return_value = None
        zammad_connector.sync_filters = mock_filters
        assert zammad_connector._is_group_allowed_by_filter("1") is True

    def test_in_filter_matches(self, zammad_connector):
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["1", "2", "3"]
        mock_operator = MagicMock()
        mock_operator.value = "in"
        mock_filter.get_operator.return_value = mock_operator

        mock_filters = MagicMock()
        mock_filters.get.return_value = mock_filter
        zammad_connector.sync_filters = mock_filters

        assert zammad_connector._is_group_allowed_by_filter("1") is True
        assert zammad_connector._is_group_allowed_by_filter("99") is False

    def test_not_in_filter_excludes(self, zammad_connector):
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["5", "6"]
        mock_operator = MagicMock()
        mock_operator.value = "not_in"
        mock_filter.get_operator.return_value = mock_operator

        mock_filters = MagicMock()
        mock_filters.get.return_value = mock_filter
        zammad_connector.sync_filters = mock_filters

        assert zammad_connector._is_group_allowed_by_filter("5") is False
        assert zammad_connector._is_group_allowed_by_filter("1") is True


class TestZammadLoadLookupTables:
    async def test_loads_states_and_priorities(self, zammad_connector):
        mock_ds = MagicMock()
        states_resp = MagicMock()
        states_resp.success = True
        states_resp.data = [{"id": 1, "name": "new"}, {"id": 2, "name": "pending"}]
        mock_ds.list_ticket_states = AsyncMock(return_value=states_resp)

        priorities_resp = MagicMock()
        priorities_resp.success = True
        priorities_resp.data = [{"id": 1, "name": "normal"}]
        mock_ds.list_ticket_priorities = AsyncMock(return_value=priorities_resp)

        zammad_connector.data_source = mock_ds
        await zammad_connector._load_lookup_tables()
        assert zammad_connector._state_map == {1: "new", 2: "pending"}
        assert zammad_connector._priority_map == {1: "normal"}

    async def test_handles_state_error_gracefully(self, zammad_connector):
        mock_ds = MagicMock()
        mock_ds.list_ticket_states = AsyncMock(side_effect=Exception("API error"))
        priorities_resp = MagicMock()
        priorities_resp.success = True
        priorities_resp.data = []
        mock_ds.list_ticket_priorities = AsyncMock(return_value=priorities_resp)

        zammad_connector.data_source = mock_ds
        # Should not raise
        await zammad_connector._load_lookup_tables()
        assert zammad_connector._state_map == {}

    async def test_skips_entries_without_name(self, zammad_connector):
        mock_ds = MagicMock()
        states_resp = MagicMock()
        states_resp.success = True
        states_resp.data = [{"id": 1, "name": ""}, {"id": 2, "name": "open"}]
        mock_ds.list_ticket_states = AsyncMock(return_value=states_resp)

        priorities_resp = MagicMock()
        priorities_resp.success = True
        priorities_resp.data = []
        mock_ds.list_ticket_priorities = AsyncMock(return_value=priorities_resp)

        zammad_connector.data_source = mock_ds
        await zammad_connector._load_lookup_tables()
        assert 1 not in zammad_connector._state_map  # Empty name skipped
        assert zammad_connector._state_map == {2: "open"}
