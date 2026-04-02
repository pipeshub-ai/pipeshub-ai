"""Comprehensive tests for S3 connector to achieve >97% coverage.

Targets uncovered lines:
- 157->160: scope_from_config falsy branch in init()
- 181-186: _build_data_source() method
- 210-230: create_connector() class method
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.s3.connector import S3Connector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.s3.full_cov")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-s3-fc"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_external_revision_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "accessKey": "AKIAIOSFODNN7EXAMPLE",
            "secretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        },
        "scope": "TEAM",
    })
    return svc


@pytest.fixture()
def s3_connector(mock_logger, mock_data_entities_processor,
                 mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.s3.connector.S3App"):
        connector = S3Connector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="s3-conn-fc",
            scope="personal",
            created_by="test-user-1",
        )
    return connector


# ===========================================================================
# init() - scope_from_config is falsy (branch 157->160)
# ===========================================================================
class TestS3ConnectorInitScopeFalsy:
    """Cover the branch where scope_from_config is falsy (None, empty, etc.)."""

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.s3.connector.S3DataSource")
    @patch("app.connectors.sources.s3.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_no_scope_in_config_defaults_to_personal(
        self, mock_filters, mock_ds_cls, mock_build, s3_connector
    ):
        """When config has no 'scope' key, scope stays PERSONAL."""
        s3_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "accessKey": "AKIA_TEST",
                "secretKey": "secret_test",
            },
            # No "scope" key at all
            "created_by": "user-42",
        })
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())

        result = await s3_connector.init()
        assert result is True
        assert s3_connector.scope == ConnectorScope.PERSONAL.value

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.s3.connector.S3DataSource")
    @patch("app.connectors.sources.s3.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_scope_empty_string_stays_personal(
        self, mock_filters, mock_ds_cls, mock_build, s3_connector
    ):
        """When config has scope as empty string, scope stays PERSONAL."""
        s3_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "accessKey": "AKIA_TEST",
                "secretKey": "secret_test",
            },
            "scope": "",  # Falsy
        })
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())

        result = await s3_connector.init()
        assert result is True
        assert s3_connector.scope == ConnectorScope.PERSONAL.value

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.s3.connector.S3DataSource")
    @patch("app.connectors.sources.s3.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_scope_none_stays_personal(
        self, mock_filters, mock_ds_cls, mock_build, s3_connector
    ):
        """When config has scope as None, scope stays PERSONAL."""
        s3_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "accessKey": "AKIA_TEST",
                "secretKey": "secret_test",
            },
            "scope": None,  # Falsy
        })
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())

        result = await s3_connector.init()
        assert result is True
        assert s3_connector.scope == ConnectorScope.PERSONAL.value


# ===========================================================================
# _build_data_source() (lines 181-186)
# ===========================================================================
class TestS3BuildDataSource:
    """Cover the _build_data_source method."""

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.s3.connector.S3DataSource")
    async def test_build_data_source_returns_s3_data_source(
        self, mock_ds_cls, mock_build, s3_connector
    ):
        """_build_data_source creates an S3Client and wraps it in S3DataSource."""
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_data_source = MagicMock()
        mock_ds_cls.return_value = mock_data_source

        result = await s3_connector._build_data_source()

        mock_build.assert_awaited_once_with(
            logger=s3_connector.logger,
            config_service=s3_connector.config_service,
            connector_instance_id=s3_connector.connector_id,
        )
        mock_ds_cls.assert_called_once_with(mock_client)
        assert result is mock_data_source

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.s3.connector.S3DataSource")
    async def test_build_data_source_propagates_build_error(
        self, mock_ds_cls, mock_build, s3_connector
    ):
        """If S3Client.build_from_services fails, the exception propagates."""
        mock_build.side_effect = Exception("Auth failed")

        with pytest.raises(Exception, match="Auth failed"):
            await s3_connector._build_data_source()


# ===========================================================================
# create_connector() class method (lines 210-230)
# ===========================================================================
class TestS3CreateConnector:
    """Cover the create_connector factory method."""

    @patch("app.connectors.sources.s3.connector.S3App")
    @patch("app.connectors.sources.s3.connector.S3CompatibleDataSourceEntitiesProcessor")
    async def test_create_connector_returns_s3_connector(
        self, mock_proc_cls, mock_app_cls
    ):
        """create_connector creates processor, initializes it, and returns connector."""
        mock_logger = logging.getLogger("test.s3.factory")
        mock_data_store_provider = MagicMock()
        mock_config_service = AsyncMock()

        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc

        connector = await S3Connector.create_connector(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="s3-conn-factory",
            scope="personal",
            created_by="test-user-1",
        )

        assert isinstance(connector, S3Connector)
        assert connector.connector_id == "s3-conn-factory"
        mock_proc.initialize.assert_awaited_once()

    @patch("app.connectors.sources.s3.connector.S3App")
    @patch("app.connectors.sources.s3.connector.S3CompatibleDataSourceEntitiesProcessor")
    async def test_create_connector_sets_parent_url_generator(
        self, mock_proc_cls, mock_app_cls
    ):
        """create_connector assigns a parent_url_generator on the processor."""
        mock_logger = logging.getLogger("test.s3.factory")
        mock_data_store_provider = MagicMock()
        mock_config_service = AsyncMock()

        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc

        connector = await S3Connector.create_connector(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="s3-conn-factory-2",
            scope="personal",
            created_by="test-user-1",
        )

        # The parent_url_generator should have been set on the processor
        assert mock_proc.parent_url_generator is not None
        # Call it to verify it delegates to connector._generate_parent_web_url
        result = mock_proc.parent_url_generator("mybucket/folder")
        assert "mybucket" in result
        assert "s3.console.aws.amazon.com" in result

    @patch("app.connectors.sources.s3.connector.S3App")
    @patch("app.connectors.sources.s3.connector.S3CompatibleDataSourceEntitiesProcessor")
    async def test_create_connector_processor_receives_correct_args(
        self, mock_proc_cls, mock_app_cls
    ):
        """create_connector passes correct base_console_url to processor."""
        mock_logger = logging.getLogger("test.s3.factory")
        mock_data_store_provider = MagicMock()
        mock_config_service = AsyncMock()

        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc

        await S3Connector.create_connector(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="s3-conn-factory-3",
            scope="personal",
            created_by="test-user-1",
        )

        mock_proc_cls.assert_called_once_with(
            mock_logger,
            mock_data_store_provider,
            mock_config_service,
            base_console_url="https://s3.console.aws.amazon.com",
        )

    @patch("app.connectors.sources.s3.connector.S3App")
    @patch("app.connectors.sources.s3.connector.S3CompatibleDataSourceEntitiesProcessor")
    async def test_create_connector_with_extra_kwargs(
        self, mock_proc_cls, mock_app_cls
    ):
        """create_connector accepts and ignores extra kwargs."""
        mock_logger = logging.getLogger("test.s3.factory")
        mock_data_store_provider = MagicMock()
        mock_config_service = AsyncMock()

        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc

        connector = await S3Connector.create_connector(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="s3-conn-factory-4",
            scope="personal",
            created_by="test-user-1",
            extra_param="should_be_ignored",
        )

        assert isinstance(connector, S3Connector)

    @patch("app.connectors.sources.s3.connector.S3App")
    @patch("app.connectors.sources.s3.connector.S3CompatibleDataSourceEntitiesProcessor")
    async def test_create_connector_parent_url_bucket_only(
        self, mock_proc_cls, mock_app_cls
    ):
        """Parent URL generator works for bucket-only external IDs."""
        mock_logger = logging.getLogger("test.s3.factory")
        mock_data_store_provider = MagicMock()
        mock_config_service = AsyncMock()

        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc

        connector = await S3Connector.create_connector(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="s3-conn-factory-5",
            scope="personal",
            created_by="test-user-1",
        )

        # Test the bucket-only URL (no path after bucket name)
        result = mock_proc.parent_url_generator("mybucket")
        assert "s3/buckets/mybucket" in result
