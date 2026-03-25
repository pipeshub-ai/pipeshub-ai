"""Tests for GitHub connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.github.connector import GithubConnector, RecordUpdate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.github")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-gh-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="dev@test.com"))
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
            "oauthConfigId": "gh-oauth-1",
        },
        "credentials": {
            "access_token": "ghp_test_token",
        },
    })
    return svc


@pytest.fixture()
def github_connector(mock_logger, mock_data_entities_processor,
                     mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.github.connector.GithubApp"):
        connector = GithubConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="gh-conn-1",
        )
    return connector


# ===========================================================================
# RecordUpdate dataclass
# ===========================================================================

class TestGitHubRecordUpdate:
    def test_default_values(self):
        ru = RecordUpdate(
            record=None,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        assert ru.old_permissions is None
        assert ru.new_permissions is None
        assert ru.external_record_id is None

    def test_with_all_fields(self):
        record = MagicMock()
        ru = RecordUpdate(
            record=record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=True,
            old_permissions=[MagicMock()],
            new_permissions=[MagicMock()],
            external_record_id="ext-gh-1",
        )
        assert ru.record is record
        assert ru.is_updated is True
        assert ru.external_record_id == "ext-gh-1"


# ===========================================================================
# GithubConnector
# ===========================================================================

class TestGithubConnectorInit:
    def test_constructor(self, github_connector):
        assert github_connector.connector_id == "gh-conn-1"
        assert github_connector.data_source is None
        assert github_connector.external_client is None
        assert github_connector.batch_size == 5

    def test_sync_points_created(self, github_connector):
        assert github_connector.record_sync_point is not None

    @patch("app.connectors.sources.github.connector.GitHubClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.github.connector.GitHubDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, github_connector):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()

        result = await github_connector.init()
        assert result is True
        assert github_connector.data_source is not None
        assert github_connector.external_client is mock_client

    @patch("app.connectors.sources.github.connector.GitHubClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_exception(self, mock_build, github_connector):
        mock_build.side_effect = Exception("Auth error")
        result = await github_connector.init()
        assert result is False


class TestGithubTestConnection:
    async def test_not_initialized(self, github_connector):
        github_connector.data_source = None
        result = await github_connector.test_connection_and_access()
        assert result is False

    async def test_success(self, github_connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_ds.get_authenticated.return_value = mock_response
        github_connector.data_source = mock_ds

        result = await github_connector.test_connection_and_access()
        assert result is True

    async def test_auth_failure(self, github_connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = False
        mock_response.error = "Bad credentials"
        mock_ds.get_authenticated.return_value = mock_response
        github_connector.data_source = mock_ds

        result = await github_connector.test_connection_and_access()
        assert result is False

    async def test_exception(self, github_connector):
        mock_ds = MagicMock()
        mock_ds.get_authenticated.side_effect = Exception("Network error")
        github_connector.data_source = mock_ds

        result = await github_connector.test_connection_and_access()
        assert result is False


class TestGithubStreamRecord:
    async def test_unsupported_record_type(self, github_connector):
        from fastapi import HTTPException
        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"

        with pytest.raises(HTTPException):
            await github_connector.stream_record(record)


class TestGithubFetchUsers:
    async def test_returns_empty_if_not_initialized(self, github_connector):
        github_connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await github_connector._fetch_users()

    async def test_returns_empty_on_auth_failure(self, github_connector):
        mock_ds = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success = False
        mock_resp.error = "Token expired"
        mock_ds.get_authenticated.return_value = mock_resp
        github_connector.data_source = mock_ds

        result = await github_connector._fetch_users()
        assert result == []

    async def test_returns_empty_if_no_creator_user(self, github_connector):
        mock_ds = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.data = MagicMock()
        mock_resp.data.login = "testuser"
        mock_ds.get_authenticated.return_value = mock_resp
        github_connector.data_source = mock_ds
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(return_value=None)

        result = await github_connector._fetch_users()
        assert result == []

    async def test_returns_user_on_success(self, github_connector):
        mock_ds = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.data = MagicMock()
        mock_resp.data.login = "testuser"
        mock_ds.get_authenticated.return_value = mock_resp
        github_connector.data_source = mock_ds

        creator = MagicMock()
        creator.email = "dev@test.com"
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(return_value=creator)

        result = await github_connector._fetch_users()
        assert len(result) == 1
        assert result[0].full_name == "testuser"
        assert result[0].email == "dev@test.com"


class TestGithubRunSync:
    async def test_sync_raises_on_no_users(self, github_connector):
        github_connector.data_source = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success = False
        mock_resp.error = "Auth error"
        github_connector.data_source.get_authenticated.return_value = mock_resp

        with pytest.raises(ValueError, match="Failed to retrieve"):
            await github_connector.run_sync()
