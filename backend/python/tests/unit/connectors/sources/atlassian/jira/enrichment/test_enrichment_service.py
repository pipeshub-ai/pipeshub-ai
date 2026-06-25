"""Tests for Jira ticket live enrichment."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.atlassian.jira.enrichment.service import (
    _discover_custom_fields,
    _get_data_source,
    _sync_oauth_token_if_needed,
    enrich_ticket_llm_context,
    is_jira_connector,
)
from app.models.entities import OriginTypes, RecordType, TicketRecord


def _ticket_record(**overrides):
    defaults = {
        "record_name": "PROJ-1",
        "record_type": RecordType.TICKET,
        "external_record_id": "10324",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.JIRA,
        "connector_id": "conn-jira-1",
    }
    defaults.update(overrides)
    return TicketRecord(**defaults)


class TestJiraEnrichmentHelpers:
    def test_is_jira_connector_accepts_enum_and_string(self):
        assert is_jira_connector(Connectors.JIRA) is True
        assert is_jira_connector(Connectors.JIRA.value) is True
        assert is_jira_connector(Connectors.JIRA_DATA_CENTER) is True
        assert is_jira_connector(Connectors.GOOGLE_DRIVE) is False
        assert is_jira_connector("not-a-connector") is False


class TestEnrichTicketLlmContext:
    @pytest.mark.asyncio
    async def test_fetches_issue_and_merges(self):
        config_service = AsyncMock()
        ticket = _ticket_record()
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"
        issue = {"id": "10324", "fields": {"status": {"name": "In Progress"}, "labels": ["live"]}}

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={"10324": issue}),
        ):
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert "* Status: Open" in merged
        assert "* Status: In Progress" not in merged
        assert "* Labels: live" in merged

    @pytest.mark.asyncio
    async def test_returns_base_when_fetch_fails(self):
        config_service = AsyncMock()
        ticket = _ticket_record()
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=None),
        ):
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert merged == base

    @pytest.mark.asyncio
    async def test_cloud_search_uses_is_cloud_after_client_build(self):
        """First request must read is_cloud after _get_data_source populates the cache."""
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        config_service = AsyncMock()
        ticket = _ticket_record(
            connector_name=Connectors.JIRA,
            external_record_id="20086",
            record_name="PST-11",
        )
        base = "Record: PST-11\nTicket Information:\n* Status: Open\n"
        issue = {"id": "20086", "fields": {"labels": ["live"]}}
        mock_ds = MagicMock()
        enrichment_service._is_cloud.clear()

        async def _build_ds(_config, connector_id, _connector_name):
            enrichment_service._is_cloud[connector_id] = True
            return mock_ds

        with patch.object(enrichment_service, "_get_data_source", side_effect=_build_ds), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={"20086": issue}),
        ) as mock_batch, patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.enrich_from_issue",
            return_value=base + "\n* Labels: live",
        ):
            await enrich_ticket_llm_context(base, ticket, config_service)

        mock_batch.assert_awaited_once()
        assert mock_batch.await_args.kwargs["is_cloud"] is True

    @pytest.mark.asyncio
    async def test_skips_non_jira_connector(self):
        config_service = AsyncMock()
        ticket = _ticket_record(connector_name=Connectors.LINEAR)
        base = "Record: LIN-1\nTicket Information:\n* Status: Open\n"

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(),
        ) as mock_batch:
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert merged == base
        mock_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_base_when_missing_ids(self):
        config_service = AsyncMock()
        ticket = _ticket_record(connector_id="", external_record_id="")
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"

        merged = await enrich_ticket_llm_context(base, ticket, config_service)
        assert merged == base

    @pytest.mark.asyncio
    async def test_returns_base_when_issue_not_found(self):
        config_service = AsyncMock()
        ticket = _ticket_record()
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={}),
        ):
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert merged == base

    @pytest.mark.asyncio
    async def test_returns_base_when_format_raises(self):
        config_service = AsyncMock()
        ticket = _ticket_record()
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"
        issue = {"id": "10324", "fields": {"labels": ["live"]}}

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={"10324": issue}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.enrich_from_issue",
            side_effect=RuntimeError("format failed"),
        ):
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert merged == base


@pytest.fixture(autouse=True)
def clear_enrichment_caches():
    from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

    enrichment_service._data_sources.clear()
    enrichment_service._auth_types.clear()
    enrichment_service._is_cloud.clear()
    yield
    enrichment_service._data_sources.clear()
    enrichment_service._auth_types.clear()
    enrichment_service._is_cloud.clear()


class TestOAuthTokenSync:
    @pytest.mark.asyncio
    async def test_oauth_cache_hit_syncs_token(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        connector_id = "conn-oauth-1"
        inner_client = MagicMock()
        inner_client.get_token.return_value = "old-token"
        mock_ds = MagicMock()
        mock_ds._client = inner_client

        enrichment_service._data_sources[connector_id] = mock_ds
        enrichment_service._auth_types[connector_id] = "OAUTH"

        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "credentials": {"access_token": "new-token"},
        }

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(),
        ) as mock_build:
            ds = await _get_data_source(config_service, connector_id, Connectors.JIRA)

        assert ds is mock_ds
        mock_build.assert_not_awaited()
        config_service.get_config.assert_awaited_once()
        inner_client.set_token.assert_called_once_with("new-token")

    @pytest.mark.asyncio
    async def test_non_oauth_cache_hit_skips_sync(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        connector_id = "conn-pat-1"
        inner_client = MagicMock()
        mock_ds = MagicMock()
        mock_ds._client = inner_client

        enrichment_service._data_sources[connector_id] = mock_ds
        enrichment_service._auth_types[connector_id] = "API_TOKEN"

        config_service = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(),
        ) as mock_build:
            ds = await _get_data_source(config_service, connector_id, Connectors.JIRA_DATA_CENTER)

        assert ds is mock_ds
        mock_build.assert_not_awaited()
        config_service.get_config.assert_not_awaited()
        inner_client.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth_same_token_no_set_token(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        connector_id = "conn-oauth-2"
        inner_client = MagicMock()
        inner_client.get_token.return_value = "same-token"
        mock_ds = MagicMock()
        mock_ds._client = inner_client

        enrichment_service._data_sources[connector_id] = mock_ds
        enrichment_service._auth_types[connector_id] = "OAUTH"

        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "credentials": {"access_token": "same-token"},
        }

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(),
        ):
            await _get_data_source(config_service, connector_id, Connectors.JIRA)

        config_service.get_config.assert_awaited_once()
        inner_client.set_token.assert_not_called()


class TestDiscoverCustomFields:
    @pytest.mark.asyncio
    async def test_returns_empty_when_data_source_unavailable(self):
        config_service = AsyncMock()
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=None),
        ):
            result = await _discover_custom_fields(
                config_service,
                "conn-missing",
                Connectors.JIRA,
            )
        assert result == {}

    @pytest.mark.asyncio
    async def test_uses_provided_data_source(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        config_service = AsyncMock()
        mock_ds = MagicMock()
        enrichment_service._is_cloud["conn-provided"] = True

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.discover_custom_field_ids",
            new=AsyncMock(return_value={"sprint": "customfield_1"}),
        ) as mock_discover, patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(),
        ) as mock_get:
            result = await _discover_custom_fields(
                config_service,
                "conn-provided",
                Connectors.JIRA,
                ds=mock_ds,
            )

        assert result == {"sprint": "customfield_1"}
        mock_get.assert_not_awaited()
        mock_discover.assert_awaited_once_with(
            mock_ds,
            is_cloud=True,
            connector_id="conn-provided",
        )


class TestBuildDataSource:
    @pytest.mark.asyncio
    async def test_builds_and_caches_new_data_source(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        connector_id = "conn-new"
        inner_client = MagicMock()
        inner_client.base_url = "https://team.atlassian.net"
        jira_client = MagicMock()
        jira_client.get_client.return_value = inner_client

        config_service = AsyncMock()
        config_service.get_config.return_value = {"auth": {"authType": "API_TOKEN"}}

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(return_value=jira_client),
        ) as mock_build:
            ds = await _get_data_source(config_service, connector_id, Connectors.JIRA)

        assert ds is not None
        mock_build.assert_awaited_once()
        assert enrichment_service._data_sources[connector_id] is ds
        assert enrichment_service._auth_types[connector_id] == "API_TOKEN"
        assert enrichment_service._is_cloud[connector_id] is True

    @pytest.mark.asyncio
    async def test_build_failure_returns_none(self):
        config_service = AsyncMock()
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            ds = await _get_data_source(config_service, "conn-bad", Connectors.JIRA)

        assert ds is None


class TestOAuthTokenSyncEdgeCases:
    @pytest.mark.asyncio
    async def test_no_config_returns_early(self):
        mock_ds = MagicMock()
        config_service = AsyncMock()
        config_service.get_config.return_value = None

        await _sync_oauth_token_if_needed(mock_ds, config_service, "conn-1")
        mock_ds._client.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_access_token_returns_early(self):
        mock_ds = MagicMock()
        config_service = AsyncMock()
        config_service.get_config.return_value = {"credentials": {}}

        await _sync_oauth_token_if_needed(mock_ds, config_service, "conn-1")
        mock_ds._client.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_without_token_methods_returns_early(self):
        mock_ds = MagicMock()
        mock_ds._client = object()
        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "credentials": {"access_token": "token"},
        }

        await _sync_oauth_token_if_needed(mock_ds, config_service, "conn-1")

    @pytest.mark.asyncio
    async def test_config_read_exception_is_swallowed(self):
        mock_ds = MagicMock()
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("etcd down")

        await _sync_oauth_token_if_needed(mock_ds, config_service, "conn-1")
        mock_ds._client.set_token.assert_not_called()
